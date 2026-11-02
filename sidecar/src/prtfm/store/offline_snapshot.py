"""Bounded SQLite readers: source snapshots and private import-copy reads.

No checkpoint/recovery or disk copy. WAL/journal sources are deliberately refused.
This cannot stop old applications that ignore the lifecycle gate.
"""
import os
import sqlite3
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from prtfm.store.ownership import _unsafe

DATABASE_LIMIT = 16 * 1024**2
PREPARED_DATABASE_LIMIT = 256 * 1024**2


class SnapshotBlocked(RuntimeError):
    pass


@contextmanager
def _database_reader(path, *, max_bytes, max_seconds, copy):
    path = Path(os.path.abspath(path))
    sidecars = [Path(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal')]
    def signature():
        if any(os.path.lexists(p) for p in sidecars):
            raise SnapshotBlocked('offline_snapshot_required')
        for part in (path, *path.parents):
            if _unsafe(part.lstat()):
                raise SnapshotBlocked('unsafe_snapshot_path')
        info = path.stat()
        if not path.is_file() or info.st_size > max_bytes:
            raise SnapshotBlocked('snapshot_size_limit')
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    source = snapshot = None
    protection = ExitStack()
    started = time.monotonic()
    def progress(*args):
        if time.monotonic() - started >= max_seconds:
            raise SnapshotBlocked('snapshot_time_limit')
    try:
        before = signature()
        if not copy and os.name == 'nt':
            from prtfm.store.offline_recovery import _open_protected
            protection.enter_context(_open_protected(path))
            if before != signature():
                raise SnapshotBlocked('source_changed')
        source = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        source.execute('PRAGMA query_only=ON')
        source.execute('PRAGMA trusted_schema=OFF')
        if copy:
            snapshot = sqlite3.connect(':memory:')
            source.backup(snapshot, pages=128, progress=progress, sleep=0)
            source.close()
        else:
            # Private, recovered staging only: bound SQLite cache; no second copy.
            snapshot = source
            snapshot.execute('PRAGMA mmap_size=0')
            snapshot.execute('PRAGMA cache_size=-2048')
        source = None
        if before != signature():
            raise SnapshotBlocked('source_changed')
        snapshot.row_factory = sqlite3.Row
        snapshot.execute('PRAGMA query_only=ON')
        snapshot.execute('PRAGMA trusted_schema=OFF')
        snapshot.set_progress_handler(lambda: int(time.monotonic() - started >= max_seconds), 1000)
        # Do not print SQLite details, which can include schema or document data.
        if snapshot.execute('PRAGMA quick_check(1)').fetchone()[0] != 'ok':
            raise SnapshotBlocked('snapshot_integrity_failed')
        yield snapshot
        if before != signature():
            raise SnapshotBlocked('source_changed')
    except (sqlite3.Error, OSError) as exc:
        raise SnapshotBlocked('snapshot_unavailable') from exc
    finally:
        if source is not None:
            source.close()
        if snapshot is not None:
            snapshot.close()
        protection.close()


@contextmanager
def database_snapshot(path, *, max_bytes=DATABASE_LIMIT, max_seconds=2):
    """Snapshot for observational readers; preserves the original small-DB contract."""
    with _database_reader(path, max_bytes=max_bytes, max_seconds=max_seconds, copy=True) as con:
        yield con


@contextmanager
def prepared_database(path, *, max_bytes=PREPARED_DATABASE_LIMIT, max_seconds=30):
    """Private import copy only, under capture/activation lock; never a live source.

    Caller owns the isolated copy and has recovered WAL before entering. Windows
    additionally protects the database against write/delete while reading. This
    does not promise atomic protection against new sidecar creation by old apps.
    """
    with _database_reader(path, max_bytes=max_bytes, max_seconds=max_seconds, copy=False) as con:
        yield con
