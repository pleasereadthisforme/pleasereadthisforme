"""Private Windows activation transaction. Not exposed by CLI/API.

Caller must enter this context in an ExitStack OUTSIDE prepared_store, so source
validation on prepared_store exit finishes before commit. Never pass a source.
"""
import json
import os
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from prtfm.store.import_inspection import inspect_import_source
from prtfm.store.import_payloads import inspect_payloads
from prtfm.store.import_schema import inspect_schemas
from prtfm.store.import_staging import _rewrite_references
from prtfm.store.lifecycle import _lock
from prtfm.store.offline_snapshot import PREPARED_DATABASE_LIMIT, SnapshotBlocked, prepared_database
from prtfm.store.ownership import CHANNELS, IMPORT_PENDING, MARKER, _unsafe, inspect_ownership


def _move_new(source, target):
    """Same-volume rename without replacement, including empty destinations."""
    if os.name != 'nt':
        raise SnapshotBlocked('windows_activation_required')
    import ctypes
    from ctypes import wintypes
    move = ctypes.WinDLL('kernel32', use_last_error=True).MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    if not move(str(source), str(target), 8):  # WRITE_THROUGH, no REPLACE/COPY flags
        raise SnapshotBlocked('activation_move_failed')


def _validate(root):
    structure = inspect_import_source(root, max_bytes=PREPARED_DATABASE_LIMIT, max_seconds=30)
    if structure['status'] != 'observed':
        raise SnapshotBlocked('activation_references_unverified')
    if inspect_schemas(root, structure, reader=prepared_database)['status'] != 'recognized':
        raise SnapshotBlocked('activation_schema_unverified')
    if inspect_payloads(root, structure, reader=prepared_database)['status'] != 'verified_scope':
        raise SnapshotBlocked('activation_payloads_unverified')


@contextmanager
def _activate_prepared(stage, target, channel, *, max_seconds, allow_empty_target=False):
    if os.name != 'nt':
        raise SnapshotBlocked('windows_activation_required')
    stage, target = Path(os.path.abspath(stage)), Path(os.path.abspath(target))
    if channel not in CHANNELS or stage.parent != target.parent or stage == target:
        raise SnapshotBlocked('invalid_activation_target')
    for part in (stage, *stage.parents):
        if _unsafe(part.lstat()):
            raise SnapshotBlocked('unsafe_activation_path')
    if inspect_ownership(stage).state != 'owned':
        raise SnapshotBlocked('invalid_staging_owner')
    deadline = time.monotonic() + max_seconds
    with _lock(target), ExitStack() as preservation:
        backup = None
        if os.path.lexists(target):
            if not allow_empty_target:
                raise SnapshotBlocked('activation_target_exists')
            from prtfm.store.empty_target import preserve_empty
            backup = preservation.enter_context(preserve_empty(target, channel))
        _validate(stage)
        pending = stage / IMPORT_PENDING
        with pending.open('x', encoding='utf-8') as handle:
            json.dump({'format_version': 1, 'stage': str(stage), 'target': str(target),
                       'phase': 'awaiting_source'}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        moved = False
        try:
            _rewrite_references(stage, stage, target, deadline)
            # Replace rather than mutate a possibly shared marker inode.
            marker_tmp = stage / '.prtfm-owner-new'
            with marker_tmp.open('x', encoding='utf-8') as handle:
                json.dump({'application': 'prtfm', 'format_version': 1, 'channel': channel}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            marker_tmp.replace(stage / MARKER)
            if time.monotonic() >= deadline:
                raise SnapshotBlocked('activation_time_limit')
            _move_new(stage, target)
            moved = True
            _validate(target)
            yield {'path': target, 'status': 'pending_commit',
                   'preserved_initialization': str(backup) if backup else None}
            if time.monotonic() >= deadline:
                raise SnapshotBlocked('activation_time_limit')
            # Caller/source checks have completed. This is the commit point.
            (target / IMPORT_PENDING).unlink()
        except BaseException:
            if moved:
                try:
                    _move_new(target, stage)
                except SnapshotBlocked as exc:
                    # Keep pending target blocked. Never delete an occupied path.
                    raise SnapshotBlocked('activation_rollback_requires_review') from exc
            # Staging is disposable and still marked pending; capture owns cleanup.
            raise
