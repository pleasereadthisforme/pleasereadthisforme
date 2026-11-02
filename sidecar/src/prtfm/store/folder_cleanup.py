"""Explicit removal of one registered workspace, never its source directory."""
import hashlib
import shutil
from contextlib import ExitStack
from pathlib import Path

from prtfm.config import data_root, workspace_dir
from prtfm.store.folders import registry
from prtfm.store.locking import WriterBusy, acquire
from prtfm.store.summary import _link


def disconnect(folder_id, delete_data=False):
    with registry() as con:
        row = con.execute('SELECT path FROM folders WHERE id=?', (folder_id,)).fetchone()
    if row is None:
        raise ValueError('folder not registered')
    return _remove_workspace(row[0], delete_data)


def delete_workspace(key):
    # Resolve only known app workspaces, never a client-supplied filesystem path.
    with registry() as con:
        paths = con.execute('SELECT path FROM workspaces UNION SELECT path FROM folders').fetchall()
    matches = [row[0] for row in paths if hashlib.sha256(row[0].encode('utf-8')).hexdigest()[:16] == key]
    if len(matches) != 1:
        raise ValueError('workspace unavailable')
    return _remove_workspace(matches[0], True)


def _remove_workspace(folder, delete_data):
    if delete_data:
        if Path(folder).resolve() != Path(folder):
            raise ValueError('source path redirected')
        directory = workspace_dir(Path(folder))
        for path in (data_root(), data_root() / 'folders', directory):
            if path.exists() and _link(path.lstat()):
                raise ValueError('redirected storage')
        if directory.exists():
            # Reject redirected entries before opening any lock or removing data.
            if any(_link(path.lstat()) for path in directory.rglob('*')):
                raise ValueError('redirected workspace entry')
            with ExitStack() as locks:
                locks.enter_context(acquire(directory / 'writer.lock'))
                cache = directory / 'cache'
                if cache.exists():
                    locks.enter_context(acquire(cache / '.generations.lock'))
                    if any((cache / '.leases').glob('*.json')):
                        raise WriterBusy('reader leases present')
                run_leases = directory / 'run-leases'
                if run_leases.exists():
                    for lease in run_leases.iterdir():
                        if not lease.is_file():
                            raise ValueError('unknown run lease')
                        locks.enter_context(acquire(lease))
                from prtfm.store.shared_vectors import release
                release(directory / 'vectors', keep=set())
                # Locks stay at stable paths so another process cannot acquire a
                # newly created lock while these handles still protect the workspace.
                for path in directory.iterdir():
                    if path.name in ('writer.lock', 'run-leases'):
                        continue
                    if path == cache:
                        for entry in cache.iterdir():
                            if entry.name == '.generations.lock':
                                continue
                            _remove(entry)
                    else:
                        _remove(path)
            from prtfm.store.shared_results import prune_catalogue
            prune_catalogue()
    # Keep the registry entry on failure, allowing an explicit retry.
    with registry() as con:
        con.execute('DELETE FROM folders WHERE path=?', (folder,))
        if delete_data:
            con.execute('DELETE FROM workspaces WHERE path=?', (folder,))
    return {'removed': True, 'data_deleted': delete_data, 'folder': folder}


def _remove(path):
    if _link(path.lstat()):
        raise ValueError('redirected entry')
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
