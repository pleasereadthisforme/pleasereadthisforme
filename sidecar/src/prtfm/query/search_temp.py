"""Owned search scratch trees. Linux flock is inherited by command children."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager

from prtfm.config import data_root
from prtfm.store.locking import WriterBusy, acquire

SUPPORTED = sys.platform.startswith('linux')
MARKER = {'kind': 'prtfm-search', 'version': 1}


def _private(path, *, directory=True):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    return kind(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o077


def _root(create=False):
    base = data_root().resolve()
    root = base / 'runtime' / 'search'
    if create:
        base.mkdir(parents=True, exist_ok=True)
    for path in (base / 'runtime', root):
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        if not _private(path):
            raise ValueError('unsafe search runtime directory')
    return root


@contextmanager
def _guard(root, *, wait=False):
    path = root / 'guard.lock'
    if path.exists() or path.is_symlink():
        if not _private(path, directory=False):
            raise ValueError('unsafe search guard')
    deadline = time.monotonic() + (5 if wait else 0)
    while True:
        try:
            # Exclusive creation permissions are independent of process umask.
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            handle = acquire(path)
            break
        except WriterBusy:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.01)
    try:
        yield
    finally:
        handle.close()


def _remove(root, job):
    if not re.fullmatch('[0-9a-f]{32}', job.name) or not _private(job):
        return False
    marker, lease, tree = job / 'owner.json', job / 'lease.lock', job / 'tree'
    if not _private(marker, directory=False) or not _private(lease, directory=False):
        return False
    if tree.exists() or tree.is_symlink():
        if not _private(tree):
            return False
    if marker.stat().st_size > 1024:
        return False
    if json.loads(marker.read_text()) != {**MARKER, 'id': job.name}:
        return False
    if not set(p.name for p in job.iterdir()) <= {'owner.json', 'lease.lock', 'tree'}:
        return False
    identity = job.stat()
    handle = acquire(lease)  # Nonblocking; inherited fd keeps this busy after parent death.
    try:
        current = job.lstat()
        if (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino):
            return False
        # Remove body first: marker/lease survive interrupted body cleanup.
        if tree.exists():
            shutil.rmtree(tree)
        if set(p.name for p in job.iterdir()) != {'owner.json', 'lease.lock'}:
            return False
        marker.unlink()
        lease.unlink()
        job.rmdir()
        return True
    finally:
        handle.close()


def cleanup_search_temps(limit=256):
    """Startup-only, bounded, best effort. Unknown/legacy/busy entries survive."""
    report = {'removed': 0, 'skipped': 0, 'supported': SUPPORTED}
    if not SUPPORTED:
        return report
    try:
        root = _root()
        with _guard(root):
            with os.scandir(root) as entries:
                for index, entry in enumerate(entries):
                    if index >= limit:
                        break
                    if entry.name == 'guard.lock':
                        continue
                    try:
                        removed = _remove(root, root / entry.name)
                        report['removed' if removed else 'skipped'] += 1
                    except (OSError, ValueError, WriterBusy):
                        report['skipped'] += 1
    except FileNotFoundError:
        pass
    except (OSError, ValueError, WriterBusy):
        report['skipped'] += 1
    return report


@contextmanager
def search_temp():
    if not SUPPORTED:
        # No unverified automatic reclamation on other platforms.
        with tempfile.TemporaryDirectory(prefix='prtfm-search-') as directory:
            yield directory, ()
        return
    root = _root(create=True)
    handle = None
    with _guard(root, wait=True):
        job = root / uuid.uuid4().hex
        job.mkdir(mode=0o700)
        tree = job / 'tree'
        tree.mkdir(mode=0o700)
        lease = job / 'lease.lock'
        fd = os.open(lease, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        handle = acquire(lease)
        try:
            fd = os.open(job / 'owner.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump({**MARKER, 'id': job.name}, stream)
        except BaseException:
            handle.close()
            raise
    try:
        yield tree, (handle.fileno(),)
    finally:
        handle.close()
        try:
            with _guard(root, wait=True):
                _remove(root, job)
        except (OSError, ValueError, WriterBusy):
            pass  # Preserve for next startup, including a still-running child.
