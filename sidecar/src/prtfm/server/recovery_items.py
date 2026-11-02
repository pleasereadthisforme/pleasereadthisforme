from prtfm.localization import text as _ui_text
"""Inspect unfinished records and explicitly remove abandoned work, never cost evidence."""
import json
import re
import sqlite3
import stat
from pathlib import Path

from prtfm.config import data_root, workspace_dir
from prtfm.server.recovery import vision_ledger_state
from prtfm.store.folders import registry
from prtfm.store.locking import acquire
from prtfm.store.summary import _link

LIMIT = 200


def _safe(path):
    for candidate in (data_root(), data_root() / 'folders', *path.parents, path):
        if candidate.exists() or candidate.is_symlink():
            info = candidate.lstat()
            if (_link(info) or (not stat.S_ISDIR(info.st_mode) and
                    (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
                raise ValueError('redirected storage')
    return path


def _db(path):
    _safe(path)
    for suffix in ('-wal', '-shm', '-journal'):
        _safe(Path(str(path) + suffix))
    return sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)


def _folders():
    with registry() as con:
        return {workspace_dir(Path(r[0])).name: r[0] for r in con.execute('SELECT path FROM workspaces UNION SELECT path FROM folders')}


def inspect_records():
    folders = _folders()
    result = {'items': [], 'unreadable': 0, 'limited': False}
    def add(key, folder, kind, identifier, title):
        if len(result['items']) >= LIMIT:
            result['limited'] = True
            return
        result['items'].append({'workspace': key, 'folder': folder, 'kind': kind,
                                'id': identifier, 'title': title})
    root = _safe(data_root() / 'folders')
    if not root.exists():
        return result
    for directory in root.iterdir():
        if not re.fullmatch('[0-9a-f]{16}', directory.name):
            continue
        key, folder = directory.name, folders.get(directory.name)
        try:
            _safe(directory)
            path = directory / 'sessions.db'
            if path.exists():
                con = _db(path)
                try:
                    columns = {r[1] for r in con.execute('PRAGMA table_info(sessions)')}
                    title = 's.title' if 'title' in columns else 'NULL'
                    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'runs' in tables:
                        for sid, name, question in con.execute(f'''SELECT s.id,{title},
                            (SELECT question FROM turns WHERE session_id=s.id ORDER BY id LIMIT 1)
                            FROM sessions s WHERE EXISTS(SELECT 1 FROM runs WHERE session_id=s.id AND state='interrupted') LIMIT ?''', (LIMIT + 1,)):
                            add(key, folder, 'chat', sid, name or (question or _ui_text('중단된 대화'))[:48])
                finally:
                    con.close()
            for filename in ('index.db', 'embedding-usage.db'):
                path = directory / filename
                if not path.exists():
                    continue
                con = _db(path)
                try:
                    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if filename == 'index.db' and 'semantic_jobs' in tables:
                        for (identifier,) in con.execute("SELECT id FROM semantic_jobs WHERE state IN ('running','interrupted') LIMIT ?", (LIMIT + 1,)):
                            add(key, folder, 'embedding', identifier, _ui_text('임베딩 작업 · ') + identifier[:8])
                    if 'semantic_usage' in tables:
                        for (identifier,) in con.execute("SELECT id FROM semantic_usage WHERE state IN ('reserved','unknown') LIMIT ?", (LIMIT + 1,)):
                            add(key, folder, 'embedding_cost', filename + ':' + identifier, _ui_text('임베딩 비용 기록 · ') + identifier[:8])
                finally:
                    con.close()
            quotes = _safe(directory / 'vision-quotes')
            for path in quotes.glob('*.result.json'):
                ledger = json.loads(_safe(path).read_text(encoding='utf-8'))
                identifier = path.name.removesuffix('.result.json')
                if vision_ledger_state(ledger) == 'running':
                    add(key, folder, 'vision', identifier, _ui_text('문서 준비 작업 · ') + identifier[:8])
                if ledger.get('unknown_usage'):
                    add(key, folder, 'vision_cost', identifier, _ui_text('문서 준비 비용 기록 · ') + identifier[:8])
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            result['unreadable'] += 1
    return result


def remove_record(workspace, kind, identifier):
    if not re.fullmatch('[0-9a-f]{16}', workspace) or not re.fullmatch('[0-9a-f]{32}', identifier):
        raise ValueError('invalid record')
    if kind not in ('chat', 'embedding', 'vision'):
        raise ValueError('cost records cannot be deleted here')
    directory = _safe(data_root() / 'folders' / workspace)
    if not directory.is_dir():
        raise ValueError('record no longer exists')
    # Validate all entries before acquiring a lock or opening a write connection.
    for path in directory.rglob('*'):
        _safe(path)
    with acquire(directory / 'writer.lock'):
        for path in directory.rglob('*'):
            _safe(path)
        if kind == 'chat':
            from prtfm.store.sessions import SessionStore
            if not (directory / 'sessions.db').is_file():
                raise ValueError('record no longer exists')
            store = SessionStore(directory / 'sessions.db')
            try:
                if not store.con.execute("SELECT 1 FROM runs WHERE session_id=? AND state='interrupted'", (identifier,)).fetchone():
                    raise ValueError('record changed')
                store.delete_session(identifier)
            finally:
                store.close()
        elif kind == 'embedding':
            path = directory / 'index.db'
            con = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=1)
            try:
                with con:
                    cursor = con.execute("DELETE FROM semantic_jobs WHERE id=? AND state IN ('running','interrupted')", (identifier,))
                    if not cursor.rowcount:
                        raise ValueError('record changed')
            finally:
                con.close()
        else:
            # This file also owns billing evidence and blocks replay: preserve it.
            path = directory / 'vision-quotes' / (identifier + '.result.json')
            ledger = json.loads(path.read_text(encoding='utf-8'))
            if ledger.get('state') != 'running':
                raise ValueError('record changed')
            ledger['state'] = 'cancelled'
            from prtfm.indexing.vision import _write
            _write(path, ledger)
    return {'removed': True}
