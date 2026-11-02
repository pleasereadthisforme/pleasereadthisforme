"""Bounded metadata-only inventory. No workspace opening, recovery or deletion."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from prtfm.config import data_root

MAX_ENTRIES = 20_000
MAX_DEPTH = 32
MAX_SECONDS = 1.0
CATEGORIES = ('processed_documents', 'embeddings', 'records', 'analysis_cache', 'temporary', 'other')


def category(parts):
    first = parts[0]
    if first == 'shared-vectors':
        return 'embeddings'
    if first == 'analysis-cache':
        return 'analysis_cache'
    if first == 'runtime' and len(parts) > 1 and parts[1] == 'search':
        return 'temporary'
    if first == 'shared-results' or first in ('registered-folders.db', 'registered-folders.db-wal', 'registered-folders.db-shm'):
        return 'records'
    if first == 'folders' and len(parts) >= 3:
        name = parts[2]
        if name == 'cache':
            return 'processed_documents'
        if name == 'vectors':
            return 'embeddings'
        if name in ('vision-quotes', 'semantic-quotes', 'run-leases') or name.endswith(('.db', '.db-wal', '.db-shm', '.lock')):
            return 'records'
    return 'other'


def _link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


@contextmanager
def _entries(path, parent_fd=None):
    # POSIX descriptor-relative traversal also rejects directory replacement by symlink.
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            with os.scandir(fd) as entries:
                yield entries, fd
        finally:
            os.close(fd)
    else:
        info = os.lstat(path)
        if _link(info) or not stat.S_ISDIR(info.st_mode):
            raise OSError('unsafe directory')
        with os.scandir(path) as entries:
            yield entries, None


def storage_summary(*, max_entries=MAX_ENTRIES, max_depth=MAX_DEPTH, max_seconds=MAX_SECONDS):
    root = Path(os.path.abspath(data_root()))
    result = {'schema_version': 1, 'path': str(root), 'custom_location': bool(os.environ.get('PRTFM_DATA_DIR')),
              'observed_at': datetime.now(UTC).isoformat(), 'status': 'complete',
              'exists': True, 'consistency': 'observational_not_atomic', 'size_basis': 'file_length_not_allocated',
              'workspaces': [], 'common': {'files': 0, 'logical_bytes': 0}, 'folder_mapping_complete': True,
              'categories': {key: {'files': 0, 'logical_bytes': 0} for key in CATEGORIES},
              'visited_entries': 0, 'skipped_links': 0, 'skipped_special': 0, 'errors': 0,
              'limits_reached': [], 'duplicate_links': 0, 'unique_bytes': None,
              'limits': {'entries': max_entries, 'depth': max_depth, 'seconds': max_seconds}}
    started = time.monotonic()
    seen, unique = set(), 0
    workspace_totals = {}
    workspaces_with_data = set()
    identity_known = os.name == 'posix'  # Windows file-ID behavior needs native validation first.

    def limited():
        for reason, reached in (('entries', result['visited_entries'] >= max_entries),
                                ('time', time.monotonic() - started >= max_seconds)):
            if reached:
                if reason not in result['limits_reached']:
                    result['limits_reached'].append(reason)
                return True
        return False

    def walk(path, parts=(), parent_fd=None):
        nonlocal unique, identity_known
        try:
            with _entries(path, parent_fd) as (entries, fd):
                while not limited():
                    try:
                        entry = next(entries)
                    except StopIteration:
                        break
                    result['visited_entries'] += 1
                    rel = (*parts, entry.name)
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if _link(info):
                            result['skipped_links'] += 1
                        elif stat.S_ISDIR(info.st_mode):
                            if len(rel) >= max_depth:
                                if 'depth' not in result['limits_reached']:
                                    result['limits_reached'].append('depth')
                            else:
                                walk(entry.name if fd is not None else Path(path) / entry.name, rel, fd)
                        elif stat.S_ISREG(info.st_mode):
                            bucket = result['categories'][category(rel)]
                            bucket['files'] += 1
                            bucket['logical_bytes'] += info.st_size
                            if len(rel) >= 3 and rel[0] == 'folders':
                                owner = workspace_totals.setdefault(rel[1], {'key':rel[1], 'files':0, 'logical_bytes':0})
                                # Only the known empty, stable locks left by workspace
                                # cleanup are ignorable. Other empty files still count.
                                known_lock = rel[2:] in (('writer.lock',), ('cache', '.generations.lock'))
                                empty_lock = known_lock and info.st_size == 0
                                if known_lock and info.st_size == 1:
                                    lock_fd = os.open(entry.name if fd is not None else Path(path) / entry.name,
                                                      os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0), dir_fd=fd)
                                    try:
                                        actual = os.fstat(lock_fd)
                                        empty_lock = ((actual.st_dev, actual.st_ino) == (info.st_dev, info.st_ino)
                                                      and os.read(lock_fd, 2) == b'\0')
                                    finally:
                                        os.close(lock_fd)
                                if not empty_lock:
                                    workspaces_with_data.add(rel[1])
                            else:
                                owner = result['common']
                            owner['files'] += 1
                            owner['logical_bytes'] += info.st_size
                            if not info.st_ino:
                                identity_known = False
                            key = (info.st_dev, info.st_ino)
                            if key in seen:
                                result['duplicate_links'] += 1
                            else:
                                seen.add(key)
                                unique += info.st_size
                        else:
                            result['skipped_special'] += 1
                    except OSError:
                        result['errors'] += 1
        except OSError:
            result['errors'] += 1
            if not parts:
                result['status'] = 'unavailable'

    try:
        info = root.lstat()
    except FileNotFoundError:
        result.update(exists=False, unique_bytes=0)
        return result
    except OSError:
        result.update(status='unavailable', errors=1, exists=None)
        return result
    if _link(info) or not stat.S_ISDIR(info.st_mode):
        result.update(status='unavailable', errors=1)
        return result
    walk(root)
    partial = any((result['errors'], result['skipped_links'], result['skipped_special'], result['limits_reached']))
    if result['status'] != 'unavailable':
        result['status'] = 'partial' if partial else 'complete'
    if identity_known and not partial:
        result['unique_bytes'] = unique
    if not identity_known:
        result['duplicate_links'] = None
    mapping, mapping_complete = _folder_mapping(root)
    result['folder_mapping_complete'] = mapping_complete
    for key, value in mapping.items():
        if value['connected']:
            workspace_totals.setdefault(key, {'key':key, 'files':0, 'logical_bytes':0})
    for key, totals in sorted(workspace_totals.items()):
        known = mapping.get(key)
        if not partial and mapping_complete and key not in workspaces_with_data and not (known and known['connected']):
            continue
        result['workspaces'].append({**totals,
            'folder': known['folder'] if known else None,
            'state': ('connected' if known['connected'] else 'disconnected') if known else 'unknown'})
    return result


def _folder_mapping(root):
    """Read registry labels only; do not initialize or migrate any workspace."""
    database = root / 'registered-folders.db'
    if not database.exists():
        return {}, True
    if _link(database.lstat()):
        return {}, False
    try:
        con = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=.1)
        try:
            con.execute('PRAGMA query_only=ON')
            connected = {row[0] for row in con.execute('SELECT path FROM folders LIMIT 20001')}
            paths = {row[0] for row in con.execute('SELECT path FROM workspaces LIMIT 20001')}
            complete = len(connected) <= 20000 and len(paths) <= 20000
            paths.update(connected)
            mapping = {}
            for folder in paths:
                # Registry paths are canonical when registered. Do not probe originals.
                key = hashlib.sha256(folder.encode('utf-8')).hexdigest()[:16]
                mapping[key] = {'folder':folder, 'connected':folder in connected}
            return mapping, complete
        finally:
            con.close()
    except (OSError, sqlite3.Error, TypeError, AttributeError):
        return {}, False
