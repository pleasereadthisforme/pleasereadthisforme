"""어휘 후보는 보존하고 현재 활성 세대의 의미 후보만 덧붙인다."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid

from prtfm.indexing.embedding import digest
from prtfm.indexing.textnorm import normalize
from prtfm.llm.embeddings import Embedder
from prtfm.retrieval.lexical import Hit
from prtfm.retrieval.worker import SearchWorker
from prtfm.store.db import Store
from prtfm.store.vectors import DIMENSIONS, MODEL


def source_range(source: str, candidate: str) -> tuple[int, int] | None:
    """Match chunker whitespace changes, retaining exact source offsets.

    Do not fuzzy-match content or choose arbitrarily between repeated passages.
    The result is a discovery location only, never citation evidence.
    """
    parts = candidate.split()
    if not parts:
        return None
    pattern = re.compile(r'\s+'.join(re.escape(part) for part in parts))
    match = pattern.search(source)
    if match is None or pattern.search(source, match.start() + 1) is not None:
        return None
    return match.span()


def active(store):
    try:
        return {r['generation']: (r['doc_id'], r['revision']) for r in store.con.execute('''
            SELECT s.generation,s.doc_id,s.revision FROM semantic_files s JOIN docs d ON d.id=s.doc_id
            WHERE s.enabled=1 AND s.generation IS NOT NULL AND s.revision=d.revision
            AND d.status='ok' AND d.error IS NULL AND d.repair_required=0 AND d.embedded=1''')}
    except sqlite3.OperationalError:
        return {}


class SemanticSearch:
    def __init__(self, store, directory, price, budget, transport=None, cancel=None):
        self.store, self.directory = store, directory
        self.latest = Store.open_readonly(directory / 'index.db')
        self.con = sqlite3.connect(directory / 'embedding-usage.db', check_same_thread=False)
        self.con.execute('PRAGMA journal_mode=WAL')
        self.con.execute('''CREATE TABLE IF NOT EXISTS semantic_usage(
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL, tokens INTEGER NOT NULL,
            reserved_nusd INTEGER NOT NULL, actual_nusd INTEGER, state TEXT NOT NULL, created REAL NOT NULL)''')
        self.job_id = uuid.uuid4().hex
        self.embedder = Embedder(self.con, self.job_id, price, budget, transport, cancel, kind='query')
        self.vectors = SearchWorker(directory / 'vectors', self.embedder.cancel)
        self.cache = {}
        self.warnings = []

    def search(self, query, lexical):
        try:
            snapshot = active(self.store)
            current = active(self.latest)
            generations = [g for g in snapshot if current.get(g) == snapshot[g]]
            if not generations:
                return lexical
            query = normalize(query)
            if query not in self.cache:
                self.cache[query] = self.embedder.embed([query])[0]
            candidates = self.vectors.search(self.cache[query], generations)
            current = active(self.latest)
            seen, extra = {h.chunk_id for h in lexical}, []
            for r in candidates:
                key = (r['doc_id'], r['revision'])
                if snapshot.get(r['generation']) != key or current.get(r['generation']) != key:
                    continue
                if r['model'] != MODEL or r['dimensions'] != DIMENSIONS:
                    continue
                meta = self.store.chunk_meta(r['doc_id'], r['idx'])
                cid = f"{r['doc_id']}@{r['revision']}#{r['idx']}"
                if (cid in seen or not meta or meta['revision'] != r['revision']
                        or digest(meta['text']) != r['text_hash']):
                    continue
                seen.add(cid)
                extra.append(Hit(cid, r['doc_id'], r['idx'], meta['path'], meta['page'],
                                 json.loads(meta['heading_trail']), meta['text'][:300]))
                if len(extra) == 4:
                    break
            return lexical + extra
        except Exception as exc:
            self.warnings.append(type(exc).__name__)
            return lexical

    def summary(self):
        return {'request_group': self.job_id, **self.embedder.summary(), 'warnings': list(self.warnings)}

    def document_ranges(self, query, documents):
        """Resolve optional vector candidates against pinned full-document text."""
        hits = self.search(query, [])
        current = set(active(self.latest).values())
        ranges = []
        for hit in hits:
            doc = documents.get(hit.doc_path)
            if doc is None or (doc.doc_id, doc.revision) not in current:
                continue
            if doc.doc_id != hit.doc_id:
                continue
            meta = self.store.chunk_meta(hit.doc_id, hit.idx)
            if (not meta or meta['revision'] != doc.revision
                    or meta['content_hash'] != doc.content_hash):
                continue
            location = source_range(doc.text, meta['text'])
            if location is None:
                self.warnings.append('unresolved_source_range')
                continue
            start, end = location
            ranges.append({'path': doc.path, 'revision': doc.revision,
                           'content_hash': doc.content_hash, 'text_hash': doc.text_hash,
                           **doc.location(start, end), 'snippet': doc.text[start:min(end, start + 300)],
                           'page_kind': doc.page_kind})
        return {'hits': ranges, 'warnings': list(self.warnings),
                'status': 'partial' if self.warnings else 'complete'}

    def close(self):
        self.embedder.cancel.set()
        try:
            self.embedder.close()
        finally:
            self.latest.close()
            self.con.close()
