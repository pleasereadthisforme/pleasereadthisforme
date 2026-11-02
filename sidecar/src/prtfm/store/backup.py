"""Explicit offline backup publication; no restore or automatic scheduling."""
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from prtfm.store.backup_plan import plan_backup
from prtfm.store.import_activation import _move_new
from prtfm.store.import_capture import captured_backup
from prtfm.store.import_policy import MAX_BYTES, MAX_FILES, MAX_SECONDS
from prtfm.store.import_staging import _prepare, _rewrite_references
from prtfm.store.offline_snapshot import SnapshotBlocked
from prtfm.store.ownership import inspect_ownership


def _manifest(payload, reference_root, deadline):
    records, groups = [], {}
    for path in sorted(payload.rglob('*')):
        if time.monotonic() >= deadline:
            raise SnapshotBlocked('backup_time_limit')
        if path.is_dir():
            continue
        info = path.stat()
        key = (info.st_dev, info.st_ino)
        group = groups.setdefault(key, len(groups))
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while block := stream.read(128 * 1024):
                if time.monotonic() >= deadline:
                    raise SnapshotBlocked('backup_time_limit')
                digest.update(block)
        records.append({'path': path.relative_to(payload).as_posix(), 'bytes': info.st_size,
                        'sha256': digest.hexdigest(), 'shared_group': group})
    return {'format_version': 1, 'application': 'prtfm-backup',
            'created_at': datetime.now(UTC).isoformat(),
            'source_channel': inspect_ownership(payload).channel,
            'reference_root': str(reference_root), 'files': records}


def create_backup(source, destination_parent, *, max_seconds=MAX_SECONDS):
    """Publish only after capture's final source check. Caller stops the server first."""
    if os.name != 'nt':
        raise SnapshotBlocked('windows_backup_required')
    source, parent = Path(source).absolute(), Path(destination_parent).absolute()
    deadline = time.monotonic() + max_seconds
    plan = plan_backup(source, parent, max_seconds=min(5, max_seconds))
    if plan['status'] != 'planned':
        raise SnapshotBlocked(plan['reasons'][0])
    backup_id = uuid.uuid4().hex
    final = parent / ('PRTFM-backup-' + backup_id[:12])
    # The outer directory holds the same capture after rename, not a second copy.
    with TemporaryDirectory(prefix='.prtfm-backup-', dir=parent) as pending:
        pending = Path(pending)
        payload = pending / 'payload'
        with captured_backup(source, parent, max_bytes=MAX_BYTES, max_files=MAX_FILES,
                             max_seconds=deadline - time.monotonic()) as capture:
            stage = capture['path']
            verified = _prepare(stage, source, deadline)
            # Validate at the current path, then relocate only reviewed fields.
            _rewrite_references(stage, stage, final / 'payload', deadline)
            _move_new(stage, payload)
            manifest = _manifest(payload, final / 'payload', deadline)
            manifest.update(backup_id=backup_id, schema_inspection=verified['schema_inspection'])
        # Capture has released handles and verified the source before publication.
        if time.monotonic() >= deadline:
            raise SnapshotBlocked('backup_time_limit')
        with (pending / 'backup.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        _move_new(pending, final)
    return {'status': 'created', 'path': str(final), 'backup_id': backup_id,
            'files': len(manifest['files']), 'restore_available': False}
