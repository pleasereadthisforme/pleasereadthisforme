"""Conservative read-only target classification; never clears an empty store."""
import os
from pathlib import Path

from prtfm.store.import_schema import SUPPORTED, schema_identity
from prtfm.store.offline_snapshot import SnapshotBlocked, database_snapshot
from prtfm.store.ownership import CHANNELS, MARKER, inspect_ownership


def inspect_target(root, channel, *, _ignore_import_journal=False):
    if channel not in CHANNELS:
        raise ValueError('unsupported storage channel')
    root = Path(os.path.abspath(root))
    owner = inspect_ownership(root, _ignore_import_journal=_ignore_import_journal)
    result = {'state': 'blocked', 'reason': owner.state, 'read_only': True,
              'import_target_available': False, 'requires_preservation': False}
    if owner.state == 'missing':
        result.update(state='missing', reason='new_destination', import_target_available=True)
        return result
    if owner.state == 'empty':
        result.update(state='empty_directory', reason='existing_directory_requires_preservation',
                      requires_preservation=True)
        return result
    if owner.state != 'owned':
        return result
    if owner.channel != channel:
        result['reason'] = 'channel_mismatch'
        return result
    try:
        before = root.stat()
        entries = list(root.iterdir())
        # A created workspace, even with empty docs, may own history/preparation
        # state. Do not infer emptiness from a single row count or byte size.
        if any(p.name not in (MARKER, 'registered-folders.db') for p in entries):
            result.update(state='existing_data', reason='additional_entries')
            return result
        database = root / 'registered-folders.db'
        if database.exists():
            with database_snapshot(database) as con:
                if schema_identity(con) != SUPPORTED['registry']:
                    result['reason'] = 'unsupported_registry_schema'
                    return result
                if any(con.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone()
                       for table in ('folders', 'workspaces')):
                    result.update(state='existing_data', reason='registered_or_known_workspace')
                    return result
        after = root.stat()
        def signature(s):
            return s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns
        if (signature(before) != signature(after)
                or sorted(p.name for p in root.iterdir()) != sorted(p.name for p in entries)
                or inspect_ownership(root, _ignore_import_journal=_ignore_import_journal) != owner):
            result['reason'] = 'target_changed'
            return result
        result.update(state='initialized_empty', reason='existing_initialization_requires_preservation',
                      requires_preservation=True)
    except (OSError, SnapshotBlocked):
        result['reason'] = 'target_unverified'
    return result
