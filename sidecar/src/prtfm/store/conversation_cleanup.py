"""Explicit deletion of conversation records across local app workspaces."""
import re
import sqlite3
import stat
from contextlib import ExitStack

from prtfm.config import data_root
from prtfm.store.locking import acquire
from prtfm.store.summary import _link

TABLES = ('run_events', 'run_snapshots', 'runs', 'citation_snapshots',
          'range_citation_snapshots', 'usage_log', 'embedding_turn_usage',
          'turn_runs', 'turns', 'sessions')


def _safe(path, directory=False):
    info = path.lstat()
    if (_link(info) or (not stat.S_ISDIR(info.st_mode) if directory else
                       not stat.S_ISREG(info.st_mode) or info.st_nlink != 1)):
        raise ValueError('redirected conversation storage')


def clear_conversations():
    root = data_root()
    report = {'removed': 0, 'skipped': 0, 'supported': True, 'workspaces': 0}
    if not root.exists():
        return report
    _safe(root, True)
    folders = root / 'folders'
    if not folders.exists():
        return report
    _safe(folders, True)
    # Hold every database write transaction and active-run lease before deleting
    # anything. Busy or unsafe storage therefore fails without partial deletion.
    with ExitStack() as stack:
        databases = []
        for directory in sorted(folders.iterdir()):
            if not re.fullmatch('[0-9a-f]{16}', directory.name):
                continue
            _safe(directory, True)
            path = directory / 'sessions.db'
            if not path.exists() and not path.is_symlink():
                continue
            for candidate in (path, directory / 'sessions.db-wal', directory / 'sessions.db-shm'):
                if candidate.exists() or candidate.is_symlink():
                    _safe(candidate)
            leases = directory / 'run-leases'
            if leases.exists() or leases.is_symlink():
                _safe(leases, True)
                for lease in sorted(leases.iterdir()):
                    if not re.fullmatch('[0-9a-f]{32}', lease.name):
                        raise ValueError('unknown run lease')
                    _safe(lease)
                    stack.enter_context(acquire(lease))
            con = sqlite3.connect(f'{path.as_uri()}?mode=rw', uri=True, timeout=0)
            stack.callback(con.close)
            con.execute('BEGIN IMMEDIATE')
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not set(TABLES).issubset(tables):
                raise ValueError('unsupported conversation schema')
            if con.execute("SELECT 1 FROM runs WHERE state='running' LIMIT 1").fetchone():
                raise ValueError('unfinished run; recover before cleanup')
            count = con.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
            databases.append((con, count))
        for con, count in databases:
            for table in TABLES:
                con.execute(f'DELETE FROM {table}')
        for con, count in databases:
            con.commit()
            report['removed'] += count
            report['workspaces'] += 1
    return report
