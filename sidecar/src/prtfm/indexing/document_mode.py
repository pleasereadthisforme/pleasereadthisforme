"""Atomic, non-destructive legacy policy transition under the workspace writer lock."""
from __future__ import annotations

import hashlib

from prtfm.indexing.pass1 import workspace_chunk_policy
from prtfm.query.source_snapshot import open_source_snapshot

_JOURNAL = 'document_mode_transition'


def _fingerprint(store):
    digest = hashlib.sha256()
    tables = store.con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for name, sql in tables:
        if name == _JOURNAL or name.startswith('sqlite_'):
            continue
        quoted = '"' + name.replace('"', '""') + '"'
        rows = store.con.execute(f'SELECT * FROM {quoted}').fetchall()
        values = sorted(repr(tuple(row)) for row in rows
                        if not (name == 'app_meta' and row[0] == 'chunk_policy'))
        digest.update(repr((name, sql, values)).encode())
    return digest.hexdigest()


def _journal(store):
    exists = store.con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_JOURNAL,)).fetchone()
    return store.con.execute(f'SELECT source_policy, fingerprint FROM {_JOURNAL} WHERE id=1').fetchone() if exists else None


def status(ws):
    saved = _journal(ws.store)
    mode = workspace_chunk_policy(ws.store)
    return {'policy': mode, 'transition_recorded': saved is not None,
            'rollback_available': bool(saved and mode == 'selected' and saved[1] == _fingerprint(ws.store))}


def apply(ws):
    if ws.writer_lock is None:
        raise ValueError('workspace writer lock required')
    if workspace_chunk_policy(ws.store) == 'selected':
        return status(ws)
    docs = ws.store.docs()
    if any(d.status != 'ok' or d.repair_required for d in docs.values()):
        raise ValueError('repair failed documents before switching document mode')
    # Validate all current full texts and page maps before changing any policy.
    with open_source_snapshot(ws.store, ws.cache) as snapshot:
        # Mode migration must verify bodies before changing policy, unlike lazy search.
        for source in snapshot.docs.values():
            _ = source.text
        fingerprint = _fingerprint(ws.store)
        original = ws.store.con.execute("SELECT value FROM app_meta WHERE key='chunk_policy'").fetchone()
        with ws.store.con:
            ws.store.con.execute(f'CREATE TABLE IF NOT EXISTS {_JOURNAL} '
                                 '(id INTEGER PRIMARY KEY CHECK(id=1), source_policy INTEGER, fingerprint TEXT NOT NULL)')
            ws.store.con.execute(f'INSERT OR REPLACE INTO {_JOURNAL} VALUES (1, ?, ?)',
                                 (original[0] if original else None, fingerprint))
            ws.store.con.execute("INSERT INTO app_meta VALUES ('chunk_policy', 1) "
                                 "ON CONFLICT(key) DO UPDATE SET value=1")
    return status(ws)


def rollback(ws):
    if ws.writer_lock is None:
        raise ValueError('workspace writer lock required')
    saved = _journal(ws.store)
    if not saved:
        raise ValueError('no legacy policy transition to roll back')
    if not status(ws)['rollback_available']:
        raise ValueError('index changed after transition; rollback refused to preserve current data')
    # No cache, chunks, embeddings or conversations are restored/deleted.
    with ws.store.con:
        if saved[0] is None:
            ws.store.con.execute("DELETE FROM app_meta WHERE key='chunk_policy'")
        else:
            ws.store.con.execute("UPDATE app_meta SET value=? WHERE key='chunk_policy'", (saved[0],))
        ws.store.con.execute(f'DELETE FROM {_JOURNAL}')
    return status(ws)
