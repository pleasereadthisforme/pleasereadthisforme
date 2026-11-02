"""Shared immutable vector generations and folder-local references.

Key lock covers provider work. Catalogue lock covers short storage operations
and search reads, preventing last-reference deletion during a read.
"""
import hashlib
import json
import shutil
import sqlite3
import time
from contextlib import contextmanager

from prtfm.config import data_root
from prtfm.store.locking import WriterBusy, acquire


def root():
    return data_root() / 'shared-vectors'


@contextmanager
def key_lock(key, cancel=None):
    directory = root() / 'locks'
    directory.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    while True:
        if cancel is not None and cancel.is_set():
            from prtfm.llm.embeddings import Cancelled
            raise Cancelled('cancelled')
        try:
            handle = acquire(directory / (key + '.lock'))
            break
        except WriterBusy:
            if time.monotonic() - start > 1800:
                raise TimeoutError('shared embedding busy') from None
            time.sleep(.05)
    try:
        yield
    finally:
        handle.close()


@contextmanager
def catalogue():
    directory = root()
    directory.mkdir(parents=True, exist_ok=True)
    with key_lock('catalogue'):
        con = sqlite3.connect(directory / 'catalogue.db')
        con.row_factory = sqlite3.Row
        con.executescript('''CREATE TABLE IF NOT EXISTS artifacts(
            key TEXT PRIMARY KEY, ready INTEGER NOT NULL, expected TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS refs(path TEXT NOT NULL,generation TEXT NOT NULL,
            key TEXT NOT NULL,doc_id INTEGER NOT NULL,revision INTEGER NOT NULL,
            PRIMARY KEY(path,generation));''')
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()


def document_key(store, doc):
    source_root = getattr(store, 'source_root', None)
    if source_root is None or len(doc['content_hash'] or '') != 64:
        return None
    from prtfm.indexing.manifest import file_hash
    from prtfm.indexing.scope import Scope
    from prtfm.store.vectors import DIMENSIONS, MODEL
    source = Scope(source_root).resolve(doc['path'])
    if file_hash(source) != doc['content_hash']:
        raise ValueError('source_changed')
    value = [str(source), doc['content_hash'], MODEL, DIMENSIONS, 1,
             [(c['idx'], c['text_hash']) for c in doc['chunks']]]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def expected(doc):
    return {c['idx']: c['text_hash'] for c in doc['chunks']}


def raw(key):
    from prtfm.store.vectors import RawVectors
    return RawVectors(root() / 'objects' / key)


def valid(key, hashes):
    from prtfm.store.vectors import DIMENSIONS, MODEL, validate
    try:
        rows = raw(key).rows(key)
        if len(rows) != len(hashes) or {r['idx']: r['text_hash'] for r in rows} != hashes:
            return False
        for row in rows:
            validate(row['vector'])
            if row['model'] != MODEL or row['dimensions'] != DIMENSIONS:
                return False
        return bool(rows)
    except (OSError, ValueError):
        return False


def available(key, hashes):
    if key is None or not (root() / 'catalogue.db').exists():
        return False
    with catalogue() as con:
        row = con.execute('SELECT ready FROM artifacts WHERE key=?', (key,)).fetchone()
        return bool(row and row['ready'] and valid(key, hashes))


def refs(con, path):
    return {r['generation']: dict(r) for r in con.execute('SELECT * FROM refs WHERE path=?', (str(path.resolve()),))}


def bind(path, generation, key, doc_id, revision, hashes):
    path.mkdir(parents=True, exist_ok=True)
    with catalogue() as con:
        con.execute('INSERT OR IGNORE INTO artifacts VALUES (?,0,?)', (key, json.dumps(hashes, sort_keys=True)))
        row = con.execute('SELECT * FROM artifacts WHERE key=?', (key,)).fetchone()
        if row['expected'] != json.dumps(hashes, sort_keys=True):
            raise ValueError('shared embedding signature mismatch')
        con.execute('INSERT OR REPLACE INTO refs VALUES (?,?,?,?,?)',
                    (str(path.resolve()), generation, key, doc_id, revision))
        return bool(row['ready'] and valid(key, hashes))


def complete(key, hashes):
    with catalogue() as con:
        if not valid(key, hashes):
            raise ValueError('incomplete shared embedding')
        con.execute('UPDATE artifacts SET ready=1 WHERE key=?', (key,))


def release(path, *, doc_id=None, keep=None):
    if not (root() / 'catalogue.db').exists():
        return
    with catalogue() as con:
        for generation, ref in refs(con, path).items():
            if (doc_id is not None and ref['doc_id'] == doc_id) or (keep is not None and generation not in keep):
                con.execute('DELETE FROM refs WHERE path=? AND generation=?', (str(path.resolve()), generation))
        unused = list(con.execute('SELECT key FROM artifacts WHERE key NOT IN (SELECT key FROM refs)'))
        for row in unused:
            # Delete payload before its catalogue entry. An interrupted cleanup is retryable.
            shutil.rmtree(root() / 'objects' / row['key'], ignore_errors=False) if (root() / 'objects' / row['key']).exists() else None
            con.execute('DELETE FROM artifacts WHERE key=?', (row['key'],))


def translated(row, ref):
    return {**row, 'generation': ref['generation'], 'doc_id': ref['doc_id'], 'revision': ref['revision']}


def promote_legacy(store, path, doc_ids=None):
    """Called under the owning workspace writer lock; no provider requests."""
    from datetime import timedelta

    from prtfm.indexing.embedding import documents
    from prtfm.store.vectors import RawVectors, Vectors
    local = RawVectors(path)
    if not (path / 'chunks.lance').exists():
        return []
    migrated = []
    states = list(store.con.execute('SELECT * FROM semantic_files WHERE enabled=1 AND generation IS NOT NULL'))
    for state in states:
        if doc_ids is not None and state['doc_id'] not in doc_ids:
            continue
        docs, errors = documents(store, [state['doc_id']])
        if errors or not docs:
            continue
        doc = docs[0]
        key = document_key(store, doc)
        if key is None:
            continue
        with key_lock(key):
            rows = local.rows(state['generation'])
            if not rows:
                continue
            from prtfm.store.vectors import DIMENSIONS, MODEL, validate
            hashes = expected(doc)
            if (len(rows) != len(hashes) or {r['idx']: r['text_hash'] for r in rows} != hashes
                    or any(r['doc_id'] != doc['doc_id'] or r['revision'] != doc['revision']
                           or r['model'] != MODEL or r['dimensions'] != DIMENSIONS for r in rows)):
                raise ValueError('invalid legacy vectors')
            for row in rows:
                validate(row['vector'])
            pending = 'promote-' + state['generation']
            ready = bind(path, pending, key, doc['doc_id'], doc['revision'], hashes)
            if not ready:
                # Interrupted promotion may already have persisted part/all of the rows.
                existing = {r['idx']: r['text_hash'] for r in raw(key).rows(key)}
                if any(hashes.get(idx) != value for idx, value in existing.items()):
                    raise ValueError('invalid shared staging')
                Vectors(path).add([{**r, 'generation': pending} for r in rows if r['idx'] not in existing])
                complete(key, hashes)
            bind(path, state['generation'], key, doc['doc_id'], doc['revision'], hashes)
            keep = [r[0] for r in store.con.execute('SELECT generation FROM semantic_files WHERE generation IS NOT NULL')]
            keep += [r[0] for r in store.con.execute('SELECT generation FROM semantic_staging')]
            release(path, keep=set(keep))
            # Switch reads before removing legacy rows. Catalogue lock also protects readers.
            with catalogue():
                local._table().delete(f"doc_id = {doc['doc_id']}")
                local._table().optimize(cleanup_older_than=timedelta(0))
            migrated.append(doc['doc_id'])
    return migrated


def adopt_overlapping_legacy(store, doc, key):
    """Discover a paid legacy vector set for this exact source, before estimating.

    Donor writer locks are nonblocking: competing folder operations retry rather
    than waiting on each other's workspace lock. No provider calls occur here.
    """
    if key is None or available(key, expected(doc)):
        return
    from prtfm.config import workspace_dir
    from prtfm.indexing.embedding import documents
    from prtfm.indexing.scope import Scope
    from prtfm.store.db import Store
    source = Scope(store.source_root).resolve(doc['path'])
    for parent in source.parents:
        directory = workspace_dir(parent)
        if directory == workspace_dir(store.source_root) or not (directory / 'vectors/chunks.lance').exists():
            continue
        donor = Store.open_readonly(directory / 'index.db')
        try:
            match = donor.docs().get(str(source.relative_to(parent)))
            if not match or match.content_hash != doc['content_hash'] or not match.embedded:
                continue
            donor.source_root = parent
            candidates, errors = documents(donor, [match.id])
            if errors or not candidates or document_key(donor, candidates[0]) != key:
                continue
        finally:
            donor.close()
        handle = acquire(directory / 'writer.lock')
        try:
            donor = Store.open_readonly(directory / 'index.db')
            donor.source_root = parent
            try:
                promote_legacy(donor, directory / 'vectors', doc_ids={match.id})
            finally:
                donor.close()
        finally:
            handle.close()
        if available(key, expected(doc)):
            return
