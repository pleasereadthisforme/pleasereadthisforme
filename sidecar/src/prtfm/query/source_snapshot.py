"""Read evidence bound to a stable document source context."""
import uuid
from contextlib import contextmanager, nullcontext

from prtfm.query.sources import Source as Source
from prtfm.query.sources import SourceReader, StoredSourceReader


class SourceEvidence:
    def __init__(self, documents, errors):
        self.id = uuid.uuid4().hex
        self.docs = {doc.path: doc for doc in documents}
        self.state = {'evidence': {}, 'errors': errors}

    def resolve_citation(self, evidence_id):
        e = self.state['evidence'][evidence_id]
        doc = self.docs[e['path']]
        # Keep one verified body through all integrity checks, even above the RAM limit.
        hold = getattr(doc, 'hold', None)
        with hold() if hold else nullcontext():
            if (not 0 <= e['start'] < e['end'] <= len(doc.text)
                    or e['text'] != doc.text[e['start']:e['end']]
                    or e['revision'] != doc.revision or e['content_hash'] != doc.content_hash
                    or e['text_hash'] != doc.text_hash or e['page_kind'] != doc.page_kind
                    or any(e[k] != v for k, v in doc.location(e['start'], e['end']).items())):
                raise ValueError('citation integrity failure')
            return {**e, 'schema_version': 2, 'kind': 'document_range',
                    'doc_id': doc.doc_id, 'doc': doc.path}


@contextmanager
def open_source_snapshot(store=None, cache=None, *, reader: SourceReader | None = None):
    if reader is None:
        if store is None or cache is None:
            raise ValueError("store and cache or a source reader required")
        reader = StoredSourceReader(store, cache)
    with reader.open() as batch:
        yield SourceEvidence(batch.documents, batch.errors)
