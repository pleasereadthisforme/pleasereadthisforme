"""Explicit import workflow used by the Preview first-run actions."""
import json
import os
import time
from contextlib import ExitStack
from pathlib import Path

from prtfm.store.import_activation import _activate_prepared, _move_new, _validate
from prtfm.store.import_staging import prepared_store
from prtfm.store.lifecycle import _lock
from prtfm.store.offline_snapshot import SnapshotBlocked
from prtfm.store.ownership import CHANNELS, IMPORT_PENDING, MARKER, _unsafe


def _pending(target):
    for part in (target, *target.parents):
        if _unsafe(part.lstat()):
            raise SnapshotBlocked('unsafe_recovery_path')
    path = target / IMPORT_PENDING
    if _unsafe(path.lstat()) or path.stat().st_size > 4096:
        raise SnapshotBlocked('invalid_import_record')
    raw = path.read_bytes()
    data = json.loads(raw)
    if (not isinstance(data, dict) or set(data) != {'format_version', 'stage', 'target', 'phase'}
            or type(data['format_version']) is not int or data['format_version'] != 1
            or data['phase'] not in ('awaiting_source', 'source_verified')
            or data['target'] != str(target) or not isinstance(data['stage'], str)):
        raise SnapshotBlocked('invalid_import_record')
    stage = Path(data['stage'])
    if (not stage.is_absolute() or stage.parent != target.parent or stage == target
            or not stage.name.startswith('.prtfm-capture-')):
        raise SnapshotBlocked('invalid_recovery_destination')
    return data, raw


def _source_verified(target):
    data, _ = _pending(target)
    data['phase'] = 'source_verified'
    temporary = target / '.prtfm-import-progress'
    with temporary.open('x', encoding='utf-8') as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(target / IMPORT_PENDING)
    from prtfm.store.empty_target import journal_path, mark_source_verified
    if journal_path(target).exists():
        owner = json.loads((target / MARKER).read_bytes())
        mark_source_verified(target, owner['channel'])


def _import_store(source, target, channel, *, max_bytes, max_seconds, max_files=2000, allow_empty_target=False, source_kind="marked"):
    """Internal opt-in operation. Source capture limitations still apply."""
    source, target = Path(source).absolute(), Path(target).absolute()
    from prtfm.config import storage_paths_overlap
    if storage_paths_overlap(source, target):
        raise SnapshotBlocked('overlapping_import_paths')
    deadline = time.monotonic() + max_seconds
    # Target activation commits only after prepared_store's source checks exit.
    with ExitStack() as activation:
        with prepared_store(source, target.parent, max_bytes=max_bytes,
                            max_seconds=max_seconds, max_files=max_files, source_kind=source_kind) as prepared:
            activated = activation.enter_context(_activate_prepared(
                prepared['path'], target, channel, max_seconds=deadline - time.monotonic(),
                allow_empty_target=allow_empty_target))
        _source_verified(target)
    return {'path': str(target), 'status': 'committed',
            'preserved_initialization': activated['preserved_initialization'],
            'consistency': prepared['consistency']}


def recover_import(target, channel, *, action):
    """Explicit resume or move-back; never delete data or overwrite a path."""
    if os.name != 'nt':
        raise SnapshotBlocked('windows_activation_required')
    if channel not in CHANNELS or action not in ('resume', 'rollback'):
        raise SnapshotBlocked('invalid_recovery_request')
    target = Path(target).absolute()
    with _lock(target):
        from prtfm.store.empty_target import journal_only_state, journal_path, read_record, restore_empty
        empty_journal = journal_path(target)
        if empty_journal.exists():
            read_record(target, channel)
            if not os.path.lexists(target):
                if action != 'rollback':
                    raise SnapshotBlocked('empty_target_restore_required')
                restore_empty(target, channel)
                return {'status': 'original_initialization_restored', 'path': str(target)}
            if not os.path.lexists(target / IMPORT_PENDING):
                state = journal_only_state(target, channel)
                if state == 'original_in_place' and action == 'rollback':
                    empty_journal.unlink()
                    return {'status': 'original_initialization_restored', 'path': str(target)}
                if state == 'final_verification_required' and action == 'resume':
                    from prtfm.store.ownership import inspect_ownership
                    owner = inspect_ownership(target, _ignore_import_journal=True)
                    if owner.state != 'owned' or owner.channel != channel:
                        raise SnapshotBlocked('invalid_recovery_owner')
                    _validate(target)
                    if journal_only_state(target, channel) != state:
                        raise SnapshotBlocked('empty_target_changed')
                    empty_journal.unlink()
                    return {'status': 'committed', 'path': str(target)}
                raise SnapshotBlocked('empty_journal_requires_review')
        data, raw = _pending(target)
        marker = target / MARKER
        if _unsafe(marker.lstat()) or marker.stat().st_size > 4096:
            raise SnapshotBlocked('invalid_recovery_owner')
        if json.loads(marker.read_bytes()) != {
            'application': 'prtfm', 'format_version': 1, 'channel': channel
        }:
            raise SnapshotBlocked('invalid_recovery_owner')
        if action == 'rollback':
            _move_new(target, Path(data['stage']))
            if empty_journal.exists():
                restore_empty(target, channel)
            return {'status': 'rolled_back_pending', 'path': data['stage']}
        if data['phase'] != 'source_verified':
            raise SnapshotBlocked('source_verification_incomplete')
        _validate(target)
        if (target / IMPORT_PENDING).read_bytes() != raw:
            raise SnapshotBlocked('import_record_changed')
        (target / IMPORT_PENDING).unlink()
        if empty_journal.exists():
            empty_journal.unlink()
        return {'status': 'committed', 'path': str(target)}


def import_store(source, target, channel, **limits):
    """Explicit marked-source import; cannot opt into legacy via generic kwargs."""
    if 'source_kind' in limits:
        raise SnapshotBlocked('invalid_source_kind')
    return _import_store(source, target, channel, **limits)


def import_legacy_store(source, target, channel, **limits):
    """Explicit Preview legacy copy; source remains unmarked and is re-reviewed."""
    if channel != 'preview' or 'source_kind' in limits:
        raise SnapshotBlocked('invalid_legacy_request')
    from prtfm.store.legacy_review import review_legacy_source
    if review_legacy_source(source)['status'] != 'recognized_candidate':
        raise SnapshotBlocked('legacy_review_required')
    return _import_store(source, target, channel, source_kind='legacy', **limits)
