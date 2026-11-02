"""Remove only unused references owned by one healthy workspace, never payloads."""
import sqlite3
from contextlib import ExitStack

from prtfm.config import data_root, workspace_dir
from prtfm.store.audit import _database
from prtfm.store.locking import WriterBusy, acquire


def _candidates(database, con, vectors):
    with _database(database) as owner:
        if owner.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('workspace_db_unhealthy')
        active = owner.execute('SELECT generation FROM semantic_files WHERE generation IS NOT NULL').fetchall()
        staging = owner.execute('SELECT generation FROM semantic_staging').fetchall()
        keep = {r[0] for r in active + staging}
    # Keep any live generation even if its doc/revision fields are inconsistent.
    return [dict(r) for r in con.execute('SELECT * FROM refs WHERE path=? ORDER BY generation', (str(vectors),))
            if r['generation'] not in keep]


def prune_references(folder, *, apply=False):
    base = data_root().resolve()
    directory = workspace_dir(folder).absolute()
    shared = base / 'shared-vectors'
    database, catalogue = directory / 'index.db', shared / 'catalogue.db'
    vectors = directory / 'vectors'
    result = {'mode': 'apply' if apply else 'preview', 'state': 'blocked',
              'candidates': [], 'removed': [], 'payloads_deleted': 0}

    def check_paths():
        paths = [directory, database, vectors, directory / 'writer.lock', directory / 'cache',
                 directory / 'cache/.generations.lock', directory / 'cache/.leases',
                 shared, catalogue, shared / 'locks', shared / 'locks/catalogue.lock']
        if any(not p.resolve().is_relative_to(base) for p in paths):
            raise ValueError('outside_data_root')
        if not database.is_file() or not catalogue.is_file() or not (directory / 'cache').is_dir():
            raise ValueError('storage_missing')

    try:
        check_paths()
        with ExitStack() as stack:
            if apply:
                # All acquisitions are nonblocking. Never wait for a workspace while
                # holding catalogue; no key lock is needed because no payload changes.
                stack.enter_context(acquire(directory / 'writer.lock'))
                stack.enter_context(acquire(directory / 'cache/.generations.lock'))
                check_paths()
                # Don't reap leases here. Any record (including malformed/stale) blocks
                # cleanup until normal lease recovery or an explicit diagnosis.
                if any((directory / 'cache/.leases').glob('*.json')):
                    raise ValueError('leases_present')
                (shared / 'locks').mkdir(exist_ok=True)
                stack.enter_context(acquire(shared / 'locks/catalogue.lock'))
                check_paths()
            mode = 'rw' if apply else 'ro'
            con = sqlite3.connect(catalogue.resolve().as_uri() + '?mode=' + mode, uri=True, timeout=.25)
            stack.callback(con.close)
            con.row_factory = sqlite3.Row
            if not apply:
                con.execute('PRAGMA query_only=ON')
            with con:
                con.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
                if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('catalogue_unhealthy')
                candidates = _candidates(database.resolve(), con, vectors.resolve())
                result['candidates'] = candidates
                if apply:
                    for row in candidates:
                        con.execute('DELETE FROM refs WHERE path=? AND generation=?',
                                    (row['path'], row['generation']))
                # Transaction commit must succeed before reporting any removed refs.
            result['removed'] = candidates if apply else []
            result['state'] = 'done' if apply else 'preview'
    except WriterBusy:
        result['reason'] = 'storage_busy'
    except (OSError, sqlite3.Error, ValueError) as exc:
        result['reason'] = str(exc) if isinstance(exc, ValueError) else 'storage_unreadable_or_transaction_failed'
    return result
