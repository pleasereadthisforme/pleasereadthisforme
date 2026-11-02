"""Windows DB/WAL capture, with recovery confined to temporary files.

Not a whole-store offline gate. Never use this alone to authorize an import.
"""
import os
import sqlite3
import struct
import time
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from prtfm.store.offline_snapshot import SnapshotBlocked, database_snapshot
from prtfm.store.ownership import _unsafe


def _validate_wal(stream, budget):
    """Reject damage that SQLite could otherwise treat as an empty WAL."""
    header = stream.read(32)
    if len(header) != 32 or header[:4] not in (b'7\x7f\x06\x82', b'7\x7f\x06\x83'):
        raise SnapshotBlocked('invalid_wal_header')
    endian = '<' if header[3] == 0x82 else '>'
    def checksum(data, state=(0, 0)):
        a, b = state
        for x, y in struct.iter_unpack(endian + 'II', data):
            a = (a + x + b) & 0xffffffff
            b = (b + y + a) & 0xffffffff
        return a, b
    version, page_size = struct.unpack('>II', header[4:12])
    state = checksum(header[:24])
    if (version != 3007000 or page_size < 512 or page_size > 65536
            or page_size & (page_size - 1) or state != struct.unpack('>II', header[24:])):
        raise SnapshotBlocked('invalid_wal_header')
    while frame := stream.read(24 + page_size):
        budget()
        if (len(frame) != 24 + page_size or frame[8:16] != header[16:24]
                or struct.unpack('>I', frame[:4])[0] == 0):
            raise SnapshotBlocked('invalid_wal_frame')
        state = checksum(frame[:8] + frame[24:], state)
        if state != struct.unpack('>II', frame[16:24]):
            raise SnapshotBlocked('invalid_wal_checksum')
    stream.seek(0)


def _open_protected(path):
    """Open an existing Windows file, denying write/delete even to old apps."""
    if os.name != 'nt':
        raise SnapshotBlocked('windows_capture_required')
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ, FILE_SHARE_READ only, OPEN_EXISTING, OPEN_REPARSE_POINT.
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise SnapshotBlocked('source_busy_or_unreadable')
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close(handle)
        raise
    stream = os.fdopen(fd, 'rb')
    if _unsafe(os.fstat(stream.fileno())):
        stream.close()
        raise SnapshotBlocked('unsafe_source')
    return stream


def _recover_copy(target, deadline):
    """Only call on a private disposable copy, never a source database."""
    con = sqlite3.connect(target, timeout=0)
    try:
        con.execute('PRAGMA trusted_schema=OFF')
        con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if con.execute('PRAGMA quick_check(1)').fetchone()[0] != 'ok':
            raise SnapshotBlocked('recovery_integrity_failed')
        if con.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] != 0:
            raise SnapshotBlocked('recovery_busy')
        con.execute('PRAGMA journal_mode=DELETE')
    finally:
        con.close()


@contextmanager
def recovered_database(path, *, max_bytes=16 * 1024**2, max_seconds=2):
    """Yield a readonly memory DB; source files remain protected until exit.

    The temporary DB/WAL pair is deleted on success and failure. Hot rollback
    journals and WAL without a valid header are refused, never silently dropped.
    """
    if os.name != 'nt':
        raise SnapshotBlocked('windows_capture_required')
    path = Path(os.path.abspath(path))
    streams = []
    started = time.monotonic()
    def budget():
        if time.monotonic() - started >= max_seconds:
            raise SnapshotBlocked('capture_time_limit')
    try:
        for part in (path, *path.parents):
            if _unsafe(part.lstat()):
                raise SnapshotBlocked('unsafe_source')
        # Protect the DB first: an ordinary SQLite read/write opener cannot
        # start while this handle is held. Existing writers cause open failure.
        streams.append(('', _open_protected(path)))
        journal = Path(str(path) + '-journal')
        if os.path.lexists(journal):
            raise SnapshotBlocked('rollback_journal_requires_review')
        for suffix in ('-wal', '-shm'):
            sidecar = Path(str(path) + suffix)
            if os.path.lexists(sidecar):
                if _unsafe(sidecar.lstat()):
                    raise SnapshotBlocked('unsafe_source')
                streams.append((suffix, _open_protected(sidecar)))
        if sum(os.fstat(s.fileno()).st_size for _, s in streams) > max_bytes:
            raise SnapshotBlocked('capture_size_limit')
        with TemporaryDirectory(prefix='prtfm-db-recovery-') as temporary:
            target = Path(temporary) / 'source.db'
            for suffix, stream in streams:
                budget()
                if suffix == '-shm':
                    continue  # SQLite rebuilds its WAL index inside staging.
                if suffix == '-wal':
                    _validate_wal(stream, budget)
                with Path(str(target) + suffix).open('xb') as out:
                    while block := stream.read(128 * 1024):
                        budget()
                        out.write(block)
            # Only the isolated copy is opened normally and checkpointed.
            _recover_copy(target, started + max_seconds)
            budget()
            with database_snapshot(target, max_bytes=max_bytes,
                                   max_seconds=max_seconds - (time.monotonic() - started)) as snapshot:
                yield snapshot
    except (OSError, sqlite3.Error) as exc:
        raise SnapshotBlocked('capture_or_recovery_failed') from exc
    finally:
        for _, stream in reversed(streams):
            stream.close()
