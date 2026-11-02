"""Opt-in channel lifecycle gate for server/CLI entry points.

The lock is held for the whole command/server lifetime. Old applications and
library calls do not participate; this is not a security boundary against them.
"""
from __future__ import annotations

import json
import os
import stat
from contextlib import contextmanager

from prtfm.config import resolve_storage_location
from prtfm.store.ownership import MARKER, inspect_ownership, write_precondition


class StorageBlocked(RuntimeError):
    pass


@contextmanager
def _lock(root):
    # A sibling lock avoids making an otherwise empty store look like legacy.
    # Never unlink the lock: deleting it would allow two different locked inodes.
    root.parent.mkdir(parents=True, exist_ok=True)
    path = root.with_name(f'.{root.name}.prtfm-lifecycle.lock')
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    if path.is_symlink():
        raise StorageBlocked('unsafe_lock')
    fd = os.open(path, flags, 0o600)
    handle = os.fdopen(fd, 'r+b')
    try:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise StorageBlocked('unsafe_lock')
        try:
            if os.name == 'nt':
                import msvcrt
                if info.st_size == 0:
                    handle.write(b'\0')
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise StorageBlocked('storage_busy') from exc
        yield
    finally:
        handle.close()


@contextmanager
def storage_session(channel):
    location = resolve_storage_location(channel)
    # Inspect before resolving aliases: a linked root must never be adopted.
    initial = inspect_ownership(location.path)
    action = write_precondition(initial, channel)
    if action not in ('ready', 'initialization_required'):
        raise StorageBlocked(action)
    root = location.path.resolve()
    previous = os.environ.get('PRTFM_DATA_DIR')
    try:
        with _lock(root):
            # Repeat after acquiring the lock; another process may have claimed it.
            if location.path.resolve() != root:
                raise StorageBlocked('storage_changed')
            action = write_precondition(inspect_ownership(location.path), channel)
            if action == 'initialization_required':
                root.mkdir(exist_ok=True)
                # Exclusive creation: interrupted writes stay invalid, never repaired
                # or overwritten automatically on the next startup.
                payload = {'application': 'prtfm', 'format_version': 1, 'channel': channel}
                with (root / MARKER).open('x', encoding='utf-8') as handle:
                    json.dump(payload, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
            elif action != 'ready':
                raise StorageBlocked(action)
            # Workers inherit the same concrete root; the parent retains the gate.
            os.environ['PRTFM_DATA_DIR'] = str(root)
            yield root
    except OSError as exc:
        raise StorageBlocked('storage_io_error') from exc
    finally:
        if previous is None:
            os.environ.pop('PRTFM_DATA_DIR', None)
        else:
            os.environ['PRTFM_DATA_DIR'] = previous
