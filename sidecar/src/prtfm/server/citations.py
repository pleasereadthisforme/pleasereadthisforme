"""Compare saved evidence with index metadata without reading source files."""


def index_versions(store, citations):
    paths = {c.get('doc') or c.get('path') for c in citations if c.get('evidence_id')}
    paths.discard(None)
    if not paths:
        return {}
    rows = store.con.execute(
        'SELECT id,path,revision,content_hash,status,error,repair_required FROM docs WHERE path IN (' +
        ','.join('?' for _ in paths) + ')', tuple(paths)).fetchall()
    documents = {row['path']: row for row in rows}
    result = {}
    for citation in citations:
        evidence_id = citation.get('evidence_id')
        if not evidence_id:
            continue
        doc = documents.get(citation.get('doc') or citation.get('path'))
        if not all(citation.get(key) is not None for key in ('doc_id', 'revision', 'content_hash')):
            state = 'unknown'
        elif doc is None:
            state = 'not_indexed'
        elif doc['status'] != 'ok' or doc['error'] is not None or doc['repair_required']:
            state = 'unavailable'
        elif (citation['doc_id'], citation['revision'], citation['content_hash']) == (
            doc['id'], doc['revision'], doc['content_hash']
        ):
            state = 'same'
        else:
            state = 'changed'
        result[evidence_id] = {'state': state, 'revision': doc['revision'] if doc is not None else None}
    return result
