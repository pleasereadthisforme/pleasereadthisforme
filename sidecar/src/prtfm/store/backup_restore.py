"""Bounded backup verification and explicit empty-target Windows restoration."""
from prtfm.localization import text as _ui_text

import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path, PurePosixPath

from prtfm.config import storage_paths_overlap
from prtfm.store.import_activation import _activate_prepared
from prtfm.store.import_capture import captured_backup_payload
from prtfm.store.import_policy import MAX_BYTES, MAX_FILES, MAX_SECONDS
from prtfm.store.import_staging import _prepare
from prtfm.store.import_workflow import _source_verified
from prtfm.store.lifecycle import StorageBlocked
from prtfm.store.locked_inspection import _inventory
from prtfm.store.offline_recovery import _open_protected
from prtfm.store.offline_snapshot import SnapshotBlocked
from prtfm.store.ownership import _unsafe, inspect_ownership


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotBlocked('duplicate_manifest_key')
        result[key] = value
    return result


def inspect_backup(root, *, max_seconds=30):
    root = Path(root).absolute()
    deadline = time.monotonic() + max_seconds
    for part in (root, *root.parents):
        if _unsafe(part.lstat()):
            raise SnapshotBlocked('unsafe_backup_path')
    if root.name.startswith(('.prtfm-backup-', '.prtfm-capture-')):
        raise SnapshotBlocked('incomplete_backup')
    before = _inventory(root, deadline)
    manifest_path = root / 'backup.json'
    if manifest_path.stat().st_size > 4 * 1024**2:
        raise SnapshotBlocked('manifest_size_limit')
    raw = manifest_path.read_bytes()
    data = json.loads(raw, object_pairs_hook=_unique)
    expected = {'format_version', 'application', 'created_at', 'source_channel',
                'reference_root', 'files', 'backup_id', 'schema_inspection'}
    if (not isinstance(data, dict) or set(data) != expected
            or type(data['format_version']) is not int or data['format_version'] != 1
            or data['application'] != 'prtfm-backup'
            or not isinstance(data['reference_root'], str) or not Path(data['reference_root']).is_absolute()
            or not isinstance(data['files'], list) or len(data['files']) > MAX_FILES):
        raise SnapshotBlocked('invalid_backup_manifest')
    if (not isinstance(data['backup_id'], str) or not re.fullmatch('[0-9a-f]{32}', data['backup_id'])
            or data['source_channel'] not in ('preview', 'stable', 'development')
            or not isinstance(data['schema_inspection'], dict)
            or not isinstance(data['created_at'], str)):
        raise SnapshotBlocked('invalid_backup_manifest')
    try:
        timestamp = datetime.fromisoformat(data['created_at'])
        if timestamp.tzinfo is None:
            raise ValueError('timezone missing')
    except ValueError as exc:
        raise SnapshotBlocked('invalid_backup_timestamp') from exc
    if '..' in Path(data['reference_root']).parts or '\x00' in data['reference_root']:
        raise SnapshotBlocked('invalid_backup_reference')
    names, groups, total = set(), {}, 0
    for item in data['files']:
        if time.monotonic() >= deadline:
            raise SnapshotBlocked('backup_time_limit')
        if not isinstance(item, dict) or set(item) != {'path', 'bytes', 'sha256', 'shared_group'}:
            raise SnapshotBlocked('invalid_backup_entry')
        name = item['path']
        if (not isinstance(name, str) or not name or '\\' in name or ':' in name
                or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in name.split('/'))
                or any(ord(c) < 32 or c in '<>"|?*' for c in name)
                or any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p, re.I) for p in name.split('/'))
                or PurePosixPath(name).is_absolute() or name.casefold() in names):
            raise SnapshotBlocked('unsafe_backup_entry')
        names.add(name.casefold())
        if (type(item['bytes']) is not int or item['bytes'] < 0
                or type(item['shared_group']) is not int or item['shared_group'] < 0
                or not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256'])):
            raise SnapshotBlocked('invalid_backup_entry')
        total += item['bytes']
        if total > MAX_BYTES:
            raise SnapshotBlocked('capture_size_limit')
        path = root / 'payload' / name
        if path.stat().st_size != item['bytes']:
            raise SnapshotBlocked('backup_hash_mismatch')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(128 * 1024):
                if time.monotonic() >= deadline:
                    raise SnapshotBlocked('backup_time_limit')
                digest.update(block)
        if digest.hexdigest() != item['sha256']:
            raise SnapshotBlocked('backup_hash_mismatch')
        signature = (item['bytes'], item['sha256'])
        if groups.setdefault(item['shared_group'], signature) != signature:
            raise SnapshotBlocked('invalid_backup_sharing')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != {'backup.json'} | {'payload/' + item['path'] for item in data['files']}:
        raise SnapshotBlocked('backup_inventory_mismatch')
    owner = inspect_ownership(root / 'payload')
    if owner.state != 'owned' or owner.channel != data['source_channel']:
        raise SnapshotBlocked('backup_owner_mismatch')
    if before != _inventory(root, deadline) or manifest_path.read_bytes() != raw:
        raise SnapshotBlocked('backup_changed')
    return data


def _invalidate_quotes(stage):
    for path in stage.glob('folders/*/index.db'):
        con = sqlite3.connect(path)
        try:
            with con:
                con.execute("UPDATE semantic_quotes SET state='consumed' WHERE state='quoted'")
        finally:
            con.close()
    for path in stage.glob('folders/*/vision-quotes/*.json'):
        if re.fullmatch(r'[0-9a-f]{32}\.json', path.name):
            data = json.loads(path.read_text(encoding='utf-8'))
            if data.get('state') == 'quoted':
                data['state'] = 'consumed'
                path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


def restore_backup(root, target, channel, *, max_seconds=MAX_SECONDS):
    if os.name != 'nt':
        raise SnapshotBlocked('windows_restore_required')
    root, target = Path(root).absolute(), Path(target).absolute()
    if storage_paths_overlap(root, target):
        raise SnapshotBlocked('overlapping_backup_paths')
    deadline = time.monotonic() + max_seconds
    # No existing target replacement, including a damaged user store.
    if os.path.lexists(target):
        raise SnapshotBlocked('restore_target_exists')
    with ExitStack() as activation:
        with _open_protected(root / 'backup.json'):
            data = inspect_backup(root, max_seconds=min(30, max_seconds))
            for entry in data['files']:
                for base in (target, target.parent / ('.prtfm-capture-' + '0' * 8)):
                    if len(str(base / entry['path']).encode('utf-16-le')) // 2 >= 260:
                        raise SnapshotBlocked('restore_path_too_long')
            with captured_backup_payload(root / 'payload', target.parent, max_bytes=MAX_BYTES,
                                 max_files=MAX_FILES, max_seconds=deadline-time.monotonic()) as capture:
                # Recheck while all payload files coexist under protected handles.
                if inspect_backup(root, max_seconds=min(30, deadline-time.monotonic())) != data:
                    raise SnapshotBlocked('backup_changed')
                stage = capture['path']
                _prepare(stage, Path(data['reference_root']), deadline)
                _invalidate_quotes(stage)
                activation.enter_context(_activate_prepared(stage, target, channel,
                    max_seconds=deadline-time.monotonic()))
            if inspect_backup(root, max_seconds=min(30, deadline-time.monotonic())) != data:
                raise SnapshotBlocked('backup_changed')
        _source_verified(target)
    return {'status': 'restored', 'path': str(target)}


def backup_status(root, *, max_seconds=5):
    """Read-only UI assessment. Never delete, resume or approve a partial tree."""
    path = Path(root).absolute()
    if path.name.startswith(('.prtfm-backup-', '.prtfm-capture-')):
        return {'status': 'incomplete_or_temporary', 'restore_available': False,
                'automatic_cleanup': False,
                'message': _ui_text('작업 중이거나 중단된 임시 데이터입니다. 완료된 백업을 선택하세요. 자동으로 삭제하지 않습니다.')}
    try:
        data = inspect_backup(path, max_seconds=max_seconds)
        return {'status': 'verified', 'restore_available': True,
                'backup_id': data['backup_id'], 'created_at': data['created_at'],
                'message': _ui_text('백업 검사를 통과했습니다. 복원 실행 시 다시 검사합니다.')}
    except (OSError, ValueError, RecursionError, SnapshotBlocked, StorageBlocked) as exc:
        return {'status': 'unavailable', 'restore_available': False,
                'reason': str(exc) if isinstance(exc, (SnapshotBlocked, StorageBlocked)) else 'backup_unreadable',
                'message': _ui_text('완료된 백업인지 확인하지 못했습니다. 데이터를 보존하고 다른 백업을 선택하세요.')}
