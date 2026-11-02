"""Read-only ownership inspection; not a lock or an authorization to write.

Callers must recheck while holding the storage lifecycle lock before writing.
No database, migration, directory creation or marker repair happens here.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

MARKER = '.prtfm-storage.json'
IMPORT_PENDING = '.prtfm-import-pending'
MAX_MARKER_BYTES = 4096
CHANNELS = frozenset(('preview', 'stable', 'development'))


@dataclass(frozen=True)
class Ownership:
    state: str
    channel: str | None = None


def _unsafe(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate marker field')
        obj[key] = value
    return obj


def inspect_ownership(root: Path, *, _ignore_import_journal=False) -> Ownership:
    root = Path(root)
    if not _ignore_import_journal and os.path.lexists(root.with_name(f'.{root.name}.prtfm-empty-import.json')):
        return Ownership('import_pending')
    # A moved but not committed import must never start as a normal store.
    if os.path.lexists(root / IMPORT_PENDING):
        return Ownership('import_pending')
    try:
        before = root.lstat()
    except FileNotFoundError:
        return Ownership('missing')
    except OSError:
        return Ownership('unreadable')
    if _unsafe(before) or not stat.S_ISDIR(before.st_mode):
        return Ownership('unsafe')
    try:
        marker = root / MARKER
        try:
            info = marker.lstat()
        except FileNotFoundError:
            with os.scandir(root) as entries:
                # Any existing item, including unknown/hidden files, is legacy.
                result = Ownership('empty' if next(entries, None) is None else 'legacy')
        else:
            if _unsafe(info) or not stat.S_ISREG(info.st_mode):
                return Ownership('unsafe')
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
            fd = os.open(marker, flags)
            with os.fdopen(fd, 'rb') as handle:
                opened = os.fstat(handle.fileno())
                if not stat.S_ISREG(opened.st_mode) or _unsafe(opened):
                    return Ownership('unsafe')
                if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
                    return Ownership('changed')
                raw = handle.read(MAX_MARKER_BYTES + 1)
            if len(raw) > MAX_MARKER_BYTES:
                return Ownership('invalid')
            try:
                data = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
            except (ValueError, UnicodeError, RecursionError):
                return Ownership('invalid')
            if not isinstance(data, dict) or data.get('application') != 'prtfm':
                return Ownership('invalid')
            if type(data.get('format_version')) is not int:
                return Ownership('invalid')
            if data['format_version'] != 1:
                return Ownership('unsupported')
            if set(data) != {'application', 'format_version', 'channel'} or not isinstance(data['channel'], str) or data['channel'] not in CHANNELS:
                return Ownership('invalid')
            result = Ownership('owned', data['channel'])
        after = root.lstat()
        if _unsafe(after) or (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            return Ownership('changed')
        return result
    except OSError:
        return Ownership('unreadable')


def write_precondition(owner: Ownership, channel: str) -> str:
    """Return the next required action. Only `ready` permits a later locked write.

    Unknown, corrupt and incompatible states fail closed. Override paths do not
    bypass this policy. Development is not a wildcard for other owners.
    """
    if channel not in CHANNELS:
        raise ValueError('unsupported storage channel')
    if owner.state == 'owned':
        return 'ready' if owner.channel == channel else 'channel_mismatch'
    if owner.state in ('missing', 'empty'):
        return 'initialization_required'
    if owner.state == 'legacy':
        return 'import_required' if channel == 'preview' else 'adoption_required'
    return 'blocked'
