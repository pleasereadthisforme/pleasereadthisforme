"""Preserve initialized empty targets by rename, never by deletion/copy."""
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from prtfm.store.import_target import inspect_target
from prtfm.store.offline_snapshot import SnapshotBlocked
from prtfm.store.ownership import IMPORT_PENDING, _unsafe


def journal_path(target):
    return target.with_name(f'.{target.name}.prtfm-empty-import.json')


def _record(target, channel):
    path = journal_path(target)
    if _unsafe(path.lstat()) or path.stat().st_size > 4096:
        raise SnapshotBlocked('invalid_empty_target_record')
    data = json.loads(path.read_bytes())
    if (not isinstance(data, dict) or set(data) not in (
            {'target', 'backup', 'channel'}, {'target', 'backup', 'channel', 'phase', 'original_id'})
            or data['target'] != str(target) or data['channel'] != channel
            or not isinstance(data['backup'], str)):
        raise SnapshotBlocked('invalid_empty_target_record')
    backup = Path(data['backup'])
    if (backup.parent != target.parent or not backup.is_absolute()
            or not backup.name.startswith(f'.{target.name}.prtfm-empty-')):
        raise SnapshotBlocked('invalid_empty_target_record')
    if 'phase' in data and (data['phase'] not in ('preserving', 'source_verified')
                           or not isinstance(data['original_id'], list)
                           or len(data['original_id']) != 2
                           or any(type(n) is not int for n in data['original_id'])):
        raise SnapshotBlocked('invalid_empty_target_record')
    return data


def read_record(target, channel):
    return Path(_record(target, channel)['backup'])


def mark_source_verified(target, channel):
    data = _record(target, channel)
    if 'original_id' not in data:
        raise SnapshotBlocked('old_empty_record_requires_review')
    data['phase'] = 'source_verified'
    temporary = journal_path(target).with_suffix('.progress')
    with temporary.open('x', encoding='utf-8') as handle:
        json.dump(data, handle)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(journal_path(target))


def journal_only_state(target, channel):
    """Read-only decisions for boundaries without an internal pending marker."""
    data = _record(target, channel)
    backup = Path(data['backup'])
    if 'original_id' not in data or not target.exists() or os.path.lexists(target / IMPORT_PENDING):
        return 'needs_review'
    for part in (target, *target.parents):
        if _unsafe(part.lstat()):
            return 'needs_review'
    info = target.stat()
    if not os.path.lexists(backup):
        if ([info.st_dev, info.st_ino] == data['original_id']
                and inspect_target(target, channel, _ignore_import_journal=True)['state']
                in ('empty_directory', 'initialized_empty')):
            return 'original_in_place'
    elif data['phase'] == 'source_verified':
        info = backup.lstat()
        if (not _unsafe(info) and [info.st_dev, info.st_ino] == data['original_id']
                and inspect_target(backup, channel)['state'] in ('empty_directory', 'initialized_empty')):
            return 'final_verification_required'
    return 'needs_review'


def restore_empty(target, channel):
    """Caller holds target lifecycle lock; restore only to a missing target."""
    from prtfm.store.import_activation import _move_new
    backup = read_record(target, channel)
    if os.path.lexists(target):
        raise SnapshotBlocked('empty_target_restore_occupied')
    if inspect_target(backup, channel)['state'] not in ('initialized_empty', 'empty_directory'):
        raise SnapshotBlocked('empty_backup_requires_review')
    _move_new(backup, target)
    journal_path(target).unlink()
    return target


@contextmanager
def preserve_empty(target, channel):
    """Caller holds lifecycle lock. Journal blocks startup during either rename."""
    from prtfm.store.import_activation import _move_new
    if inspect_target(target, channel)['state'] not in ('initialized_empty', 'empty_directory'):
        raise SnapshotBlocked('target_not_verified_empty')
    backup = target.with_name(f'.{target.name}.prtfm-empty-{uuid.uuid4().hex}')
    journal = journal_path(target)
    with journal.open('x', encoding='utf-8') as handle:
        info = target.stat()
        json.dump({'target': str(target), 'backup': str(backup), 'channel': channel,
                   'phase': 'preserving', 'original_id': [info.st_dev, info.st_ino]}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    moved = False
    try:
        _move_new(target, backup)
        moved = True
        if inspect_target(backup, channel)['state'] not in ('initialized_empty', 'empty_directory'):
            raise SnapshotBlocked('empty_target_changed')
        yield backup
        journal.unlink()  # Keep the tiny prior initialization, report its location.
    except BaseException:
        if moved and not os.path.lexists(target):
            _move_new(backup, target)  # Preserve even if an unexpected change was found.
            journal.unlink()
        elif not moved:
            journal.unlink()
        # An occupied target or failed rename retains the recovery record.
        raise
