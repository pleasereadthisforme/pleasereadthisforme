"""견적에 고정된 문서만 임베딩하고 완성된 세대만 활성화한다."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path

from prtfm.config import SEMANTIC_LIMITS
from prtfm.indexing.maintenance import VectorMaintenance
from prtfm.indexing.quotes import TTL, QuoteUnavailable, prune_semantic
from prtfm.llm.embeddings import Cancelled, Embedder, EmbeddingError, Price, nusd, token_count
from prtfm.store.vectors import DIMENSIONS, MODEL, Vectors


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def toggle(store, doc_id: int, enabled: bool):
    with store.con:
        if not store.con.execute('SELECT 1 FROM docs WHERE id=?', (doc_id,)).fetchone():
            raise ValueError('unknown document')
        store.con.execute('''INSERT INTO semantic_files(doc_id,enabled,state) VALUES (?,?,?)
            ON CONFLICT(doc_id) DO UPDATE SET enabled=excluded.enabled,
            generation=CASE WHEN excluded.enabled=0 THEN NULL ELSE generation END,
            state=CASE WHEN excluded.enabled=0 THEN 'off' WHEN generation IS NULL THEN 'needs_estimate' ELSE state END''',
                          (doc_id, int(enabled), 'needs_estimate' if enabled else 'off'))
        if not enabled:
            store.con.execute('UPDATE docs SET embedded=0 WHERE id=?', (doc_id,))
            store.con.execute('DELETE FROM semantic_staging WHERE doc_id=?', (doc_id,))
    return status(store)


def status(store):
    return [dict(r) for r in store.con.execute('''SELECT d.id,d.path,d.revision,d.embedded,
        COALESCE(s.enabled,0) enabled,COALESCE(s.state,'off') state
        FROM docs d LEFT JOIN semantic_files s ON s.doc_id=d.id ORDER BY d.id''')]


def documents(store, ids, *, verified_tokens=None):
    docs, excluded = [], []
    for doc_id in sorted(set(ids)):
        row = store.con.execute('SELECT * FROM docs WHERE id=?', (doc_id,)).fetchone()
        setting = store.con.execute('SELECT enabled FROM semantic_files WHERE doc_id=?', (doc_id,)).fetchone()
        reason, refinement_details = None, None
        if row is None:
            reason = 'missing'
        elif not setting or not setting[0]:
            reason = 'disabled'
        elif row['status'] != 'ok' or row['error'] is not None or row['repair_required'] or row['parse_stage'] < 1:
            reason = 'not_ready'
        elif Path(row['path']).suffix.lower() == '.pdf':
            refinement = store.con.execute('SELECT * FROM refinement WHERE doc_id=?', (doc_id,)).fetchone()
            if (row['parse_stage'] != 2 or not refinement or refinement['state'] not in ('done', 'degraded')
                    or refinement['input_hash'] != row['content_hash']):
                reason = 'pdf_not_refined'
            else:
                refinement_details = json.loads(refinement['details'])
        if reason:
            excluded.append({'doc_id': doc_id, 'reason': reason})
            continue
        chunks = []
        for r in store.con.execute('SELECT idx,text FROM chunks WHERE doc_id=? ORDER BY idx', (doc_id,)):
            text_hash = digest(r['text'])
            # Reuse only counts verified in this run; still hash the current body.
            tokens = (verified_tokens.get((doc_id, r['idx'], text_hash))
                      if verified_tokens is not None else None)
            if tokens is None:
                tokens = token_count(r['text'])
            chunks.append({'idx': r['idx'], 'text_hash': text_hash, 'tokens': tokens})
        if not chunks or any(c['tokens'] == 0 or c['tokens'] > 8000 for c in chunks):
            mode = store.con.execute("SELECT value FROM app_meta WHERE key='chunk_policy'").fetchone()
            reason = 'scan_required' if not chunks and mode and mode[0] == 1 else 'invalid_chunks'
            excluded.append({'doc_id': doc_id, 'reason': reason})
            continue
        docs.append({'doc_id': doc_id, 'revision': row['revision'], 'content_hash': row['content_hash'],
                     'path': row['path'], 'chunks': chunks, 'refinement': refinement_details})
    return docs, excluded


def estimate(store, ids, price: Price | None):
    prune_semantic(store, time.time())
    docs, excluded = documents(store, ids)
    selected = {d['doc_id'] for d in docs}
    active = reserved_chunks(store, selected)
    count = sum(len(d['chunks']) for d in docs)
    from prtfm.store import shared_vectors as shared
    shared_keys = {str(d['doc_id']): shared.document_key(store, d) for d in docs}
    for doc in docs:
        shared.adopt_overlapping_legacy(store, doc, shared_keys[str(doc['doc_id'])])
    reusable = [d['doc_id'] for d in docs if shared.available(shared_keys[str(d['doc_id'])], shared.expected(d))]
    tokens = sum(c['tokens'] for d in docs if d['doc_id'] not in reusable for c in d['chunks'])
    payload = {'documents': docs, 'excluded': excluded, 'model': MODEL, 'dimensions': DIMENSIONS,
               'chunks': count, 'total_active_chunks': active + count, 'tokens': tokens,
               'shared_keys': shared_keys, 'reusable_doc_ids': reusable,
               'price': asdict(price) if price else None,
               'estimated_usd': price.cost(tokens) / 1e9 if price else None}
    qid = uuid.uuid4().hex
    with store.con:
        store.con.execute('INSERT INTO semantic_quotes(id,payload,created) VALUES (?,?,?)',
                          (qid, json.dumps(payload), time.time()))
    return {'quote_id': qid, **payload, 'expires_in': TTL,
            'runnable': bool(docs and price and active + count <= 10_000)}


def reserved_chunks(store, selected):
    return sum(r['n'] for r in store.con.execute('''SELECT c.doc_id,COUNT(*) n FROM chunks c WHERE c.doc_id IN (
        SELECT doc_id FROM semantic_files WHERE generation IS NOT NULL
        UNION SELECT doc_id FROM semantic_staging) GROUP BY c.doc_id''') if r['doc_id'] not in selected)


def recover(store, vectors):
    with store.con:
        store.con.execute("UPDATE semantic_jobs SET state='interrupted' WHERE state='running'")
        store.con.execute("UPDATE semantic_usage SET state='unknown' WHERE state='reserved'")
        store.con.execute("UPDATE semantic_files SET state='needs_estimate' WHERE state='embedding'")
        store.con.execute('''DELETE FROM semantic_staging WHERE doc_id NOT IN (
            SELECT s.doc_id FROM semantic_files s JOIN docs d ON d.id=s.doc_id
            JOIN semantic_staging t ON t.doc_id=d.id
            WHERE s.enabled=1 AND t.revision=d.revision AND d.status='ok' AND d.error IS NULL AND d.repair_required=0)''')
    for state in list(store.con.execute('SELECT * FROM semantic_files WHERE generation IS NOT NULL')):
        rows = vectors.rows(state['generation'])
        expected = {r['idx']: digest(r['text']) for r in store.con.execute(
            'SELECT idx,text FROM chunks WHERE doc_id=?', (state['doc_id'],))}
        if (len(rows) != len(expected) or {r['idx']: r['text_hash'] for r in rows} != expected
                or any(r['doc_id'] != state['doc_id'] or r['revision'] != state['revision']
                       or r['model'] != MODEL or r['dimensions'] != DIMENSIONS for r in rows)):
            with store.con:
                store.con.execute("UPDATE semantic_files SET generation=NULL,state='needs_estimate' WHERE doc_id=?",
                                  (state['doc_id'],))
                store.con.execute('UPDATE docs SET embedded=0 WHERE id=?', (state['doc_id'],))
    generations = [r[0] for r in store.con.execute('SELECT generation FROM semantic_files WHERE generation IS NOT NULL')]
    generations += [r[0] for r in store.con.execute('SELECT generation FROM semantic_staging')]
    warnings = []
    try:
        vectors.recover(generations)
    except Exception as exc:
        warnings.append(type(exc).__name__)
    store._semantic_recovery_warnings = warnings
    return warnings


def run(store, vector_path, quote_id, budget, price, cancel=None, transport=None, control=None, progress=None):
    prune_semantic(store, time.time())
    cancel = cancel or threading.Event()
    vectors = Vectors(vector_path)
    if not hasattr(store, '_semantic_recovery_warnings'):
        recover(store, vectors)
    row = store.con.execute('SELECT payload,state FROM semantic_quotes WHERE id=?', (quote_id,)).fetchone()
    if not row or row['state'] != 'quoted':
        raise QuoteUnavailable('unknown or consumed quote: 견적이 만료되었거나 이미 실행되었습니다. 비용을 다시 확인하세요.')
    quote = json.loads(row['payload'])
    if price is None or quote['price'] != asdict(price):
        raise ValueError('price unknown or changed')
    docs, excluded = documents(store, [d['doc_id'] for d in quote['documents']])
    if docs != quote['documents'] or excluded or not docs:
        raise ValueError('quote is stale')
    selected = {d['doc_id'] for d in docs}
    active = reserved_chunks(store, selected)
    if active + sum(len(d['chunks']) for d in docs) > 10_000:
        raise ValueError('chunk capacity exceeded')
    if nusd(budget) < price.cost(quote['tokens']):
        raise ValueError('budget below estimate')
    job_id = uuid.uuid4().hex
    with store.con:
        store.con.execute("UPDATE semantic_quotes SET state='consumed' WHERE id=?", (quote_id,))
        store.con.execute('INSERT INTO semantic_jobs(id,state,budget_nusd,quote_id) VALUES (?,?,?,?)',
                          (job_id, 'running', nusd(budget), quote_id))
        for doc in docs:
            store.con.execute('INSERT OR IGNORE INTO semantic_staging VALUES (?,?,?)',
                              (doc['doc_id'], doc['revision'], uuid.uuid4().hex))
    embedder = Embedder(store.con, job_id, price, budget, transport, cancel)
    warnings, done = list(store._semantic_recovery_warnings), 0
    maintenance = VectorMaintenance(vectors, warnings)

    def checkpoint():
        if control:
            control(store)
        if cancel.is_set():
            raise Cancelled('cancelled')

    try:
        for doc in docs:
            # Bounded to this document, built from fresh startup validation, not
            # the persisted quote. No body copies or cross-job cache.
            verified_tokens = {(doc['doc_id'], c['idx'], c['text_hash']): c['tokens']
                               for c in doc['chunks']}
            from prtfm.store import shared_vectors as shared
            key = shared.document_key(store, doc)
            if key != quote.get('shared_keys', {}).get(str(doc['doc_id'])):
                raise ValueError('shared source changed')
            with shared.key_lock(key, cancel) if key else nullcontext():
                checkpoint()
                doc_id = doc['doc_id']
                staging = store.con.execute('SELECT generation FROM semantic_staging WHERE doc_id=? AND revision=?',
                                            (doc_id, doc['revision'])).fetchone()
                generation = staging[0] if staging else uuid.uuid4().hex
                if key:
                    reusable = shared.bind(vector_path, generation, key, doc_id, doc['revision'], shared.expected(doc))
                    if doc_id in quote.get('reusable_doc_ids', []) and not reusable:
                        raise ValueError('shared vectors unavailable; estimate again')
                cached = {r['idx']: r for r in vectors.rows(generation)}
                expected = {c['idx']: c['text_hash'] for c in doc['chunks']}
                if any(r['idx'] not in expected or r['text_hash'] != expected[r['idx']]
                       or r['model'] != MODEL or r['dimensions'] != DIMENSIONS
                       or r['revision'] != doc['revision'] or r['doc_id'] != doc_id for r in cached.values()):
                    generation, cached = uuid.uuid4().hex, {}
                    if key:
                        raise ValueError('invalid shared vector payload')
                with store.con:
                    store.con.execute('INSERT OR REPLACE INTO semantic_staging VALUES (?,?,?)',
                                      (doc_id, doc['revision'], generation))
                    store.con.execute("UPDATE semantic_files SET state='embedding' WHERE doc_id=?", (doc_id,))
                chunks = list(store.con.execute('SELECT idx,text FROM chunks WHERE doc_id=? ORDER BY idx', (doc_id,)))
                batch, batch_tokens = [], 0

                def flush(batch, doc=doc, doc_id=doc_id, generation=generation, verified_tokens=verified_tokens):
                    nonlocal done
                    checkpoint()
                    current, errors = documents(store, [doc_id], verified_tokens=verified_tokens)
                    if errors or current != [doc]:
                        raise EmbeddingError('document changed or disabled')
                    embedded = embedder.embed([r['text'] for r in batch])
                    checkpoint()
                    maintenance.add([{'generation': generation, 'doc_id': doc_id, 'revision': doc['revision'],
                                  'idx': r['idx'], 'text_hash': digest(r['text']), 'model': MODEL,
                                  'dimensions': DIMENSIONS, 'vector': v} for r, v in zip(batch, embedded, strict=True)])
                    done += len(batch)
                    if progress:
                        progress(done, quote['chunks'])

                for chunk in chunks:
                    previous = cached.get(chunk['idx'])
                    if (previous and previous['text_hash'] == digest(chunk['text'])
                            and previous['model'] == MODEL and previous['dimensions'] == DIMENSIONS
                            and previous['revision'] == doc['revision'] and previous['doc_id'] == doc_id):
                        from prtfm.store.vectors import validate
                        validate(previous['vector'])
                        done += 1
                        continue
                    n = token_count(chunk['text'])
                    if batch and (len(batch) == SEMANTIC_LIMITS.batch_chunks
                                  or batch_tokens + n > min(SEMANTIC_LIMITS.batch_tokens, embedder.pacer.limit)):
                        flush(batch)
                        batch, batch_tokens = [], 0
                    batch.append(chunk)
                    batch_tokens += n
                if batch:
                    flush(batch)
                checkpoint()
                current, errors = documents(store, [doc_id], verified_tokens=verified_tokens)
                if errors or current != [doc]:
                    raise EmbeddingError('document changed or disabled')
                if key:
                    shared.complete(key, shared.expected(doc))
                with store.con:
                    store.con.execute("UPDATE semantic_files SET generation=?,revision=?,state='ready' WHERE doc_id=?",
                                      (generation, doc['revision'], doc_id))
                    store.con.execute('UPDATE docs SET embedded=1 WHERE id=?', (doc_id,))
                    store.con.execute('DELETE FROM semantic_staging WHERE doc_id=?', (doc_id,))
        state, error = 'done', None
    except Exception as exc:
        state = 'cancelled' if isinstance(exc, Cancelled) else 'error'
        error = type(exc).__name__
    finally:
        try:
            embedder.close()
        finally:
            maintenance.finish()
    with store.con:
        store.con.execute("UPDATE semantic_files SET state='needs_estimate' WHERE state='embedding'")
        store.con.execute('UPDATE semantic_jobs SET state=?,error=?,warnings=? WHERE id=?',
                          (state, error, json.dumps(warnings), job_id))
    if any(quote.get('shared_keys', {}).values()):
        generations = [r[0] for r in store.con.execute('SELECT generation FROM semantic_files WHERE generation IS NOT NULL')]
        generations += [r[0] for r in store.con.execute('SELECT generation FROM semantic_staging')]
        try:
            vectors.recover(generations)
        except Exception as exc:
            warnings.append(type(exc).__name__)
    return {'job_id': job_id, 'state': state, 'error': error, 'done': done, 'warnings': warnings, **embedder.summary()}


def delete_document_embeddings(store, vectors_path, doc_id):
    # Commit exclusion before vector deletion, so concurrent readers cannot use stale generations.
    # Failure is retryable; original text and historical citations are untouched.
    toggle(store, doc_id, False)
    from prtfm.store.vectors import Vectors
    try:
        Vectors(vectors_path).delete_document(doc_id)
    except Exception:
        with store.con:
            store.con.execute("UPDATE semantic_files SET state='delete_failed' WHERE doc_id=?", (doc_id,))
        raise
    return status(store)
