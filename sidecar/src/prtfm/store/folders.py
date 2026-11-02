"""Local folder bookmarks only; removing one never deletes workspace data."""
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from prtfm.config import data_root


@contextmanager
def registry():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(root / 'registered-folders.db', timeout=5)
    con.row_factory = sqlite3.Row
    try:
        con.execute('CREATE TABLE IF NOT EXISTS folders (id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL)')
        con.execute('CREATE TABLE IF NOT EXISTS workspaces (path TEXT PRIMARY KEY)')
        con.execute('INSERT OR IGNORE INTO workspaces SELECT path FROM folders')
        with con:
            yield con
    finally:
        con.close()


def list_folders():
    with registry() as con:
        rows = [dict(r) for r in con.execute('SELECT id,path FROM folders ORDER BY path')]
    for row in rows:
        try:
            row['available'] = Path(row['path']).is_dir()
        except OSError:
            row['available'] = False
    return rows


def register_folder(path):
    with registry() as con:
        con.execute('INSERT OR IGNORE INTO folders VALUES (?,?)', (uuid.uuid4().hex, str(path)))
        return dict(con.execute('SELECT id,path FROM folders WHERE path=?', (str(path),)).fetchone())


def unregister_folder(folder_id):
    with registry() as con:
        con.execute('DELETE FROM folders WHERE id=?', (folder_id,))


def remember_workspace(path):
    with registry() as con:
        con.execute('INSERT OR IGNORE INTO workspaces VALUES (?)', (str(Path(path).resolve()),))


def all_sessions(before=None):
    """Merge known workspace histories without changing their session databases."""
    import json

    from prtfm.config import workspace_dir
    boundary = None
    if before is not None:
        try:
            boundary = json.loads(before)
            if (not isinstance(boundary, list) or len(boundary) != 3
                    or not isinstance(boundary[0], (int, float))
                    or not all(isinstance(x, str) for x in boundary[1:])):
                raise ValueError('invalid cursor')
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError('invalid cursor') from exc
    with registry() as con:
        paths = [row[0] for row in con.execute('SELECT path FROM workspaces')]
    results = []
    for folder in paths:
        db = workspace_dir(Path(folder)) / 'sessions.db'
        if not db.is_file():
            continue
        con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
        try:
            title_column = "s.title" if "title" in {r[1] for r in con.execute("PRAGMA table_info(sessions)")} else "NULL"
            query = """SELECT s.id,s.created,
                (SELECT question FROM turns WHERE session_id=s.id ORDER BY id LIMIT 1),
                (SELECT COUNT(*) FROM turns WHERE session_id=s.id), """ + title_column + " FROM sessions s"
            params = ()
            if boundary is not None:
                query += ' WHERE (s.created,?,s.id) < (?,?,?)'
                params = (folder, *boundary)
            rows = con.execute(query + ' ORDER BY s.created DESC,s.id DESC LIMIT 51', params).fetchall()
            results.extend({'id':row[0], 'created':row[1], 'question':row[2], 'turns':row[3], 'title':row[4], 'folder':folder}
                           for row in rows)
        finally:
            con.close()
    results.sort(key=lambda item:(item['created'],item['folder'],item['id']), reverse=True)
    page = results[:50]
    cursor = json.dumps([page[-1]['created'],page[-1]['folder'],page[-1]['id']]) if len(results)>50 else None
    return {'sessions':page, 'limited':len(results)>50, 'next_cursor':cursor}


def semantic_catalog(root):
    """Read known embedding registrations below the navigation root, without opening caches."""
    from prtfm.config import workspace_dir
    root = Path(root).resolve()
    with registry() as con:
        paths = [row[0] for row in con.execute('SELECT path FROM workspaces ORDER BY path')]
    results, unavailable = [], 0
    for folder in paths:
        scope = Path(folder).resolve()
        if not scope.is_relative_to(root):
            continue
        db = workspace_dir(scope) / 'index.db'
        if not db.is_file():
            continue
        try:
            con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)
            try:
                rows = con.execute('''SELECT d.id,d.path,d.embedded,s.enabled,s.state
                    FROM docs d JOIN semantic_files s ON s.doc_id=d.id
                    WHERE s.enabled=1 OR s.state='delete_failed' ORDER BY d.path''').fetchall()
                for doc_id, path, embedded, enabled, state in rows:
                    file = (scope / path).resolve()
                    if not file.is_relative_to(root) or not file.is_relative_to(scope):
                        continue
                    results.append(dict(id=doc_id, path=path, folder=str(scope),
                        relative=str(file.relative_to(root)), embedded=embedded, enabled=enabled,
                        state=state, available=file.is_file()))
            finally:
                con.close()
        except (sqlite3.Error, OSError):
            unavailable += 1
    return {'root':str(root), 'files':results, 'unavailable_workspaces':unavailable}
