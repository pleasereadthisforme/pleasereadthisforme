"""Pinned schema identities for inspection, never migration or import approval."""
import hashlib
import json

from prtfm.store.offline_snapshot import SnapshotBlocked, database_snapshot

# Frozen from empty databases created by the reviewed 2026-10-29 implementation.
# Do not regenerate automatically when a writer changes its schema.
SUPPORTED = {
    # Query embedding ledger created by retrieval.semantic.SemanticSearch.
    'embedding_usage': 'a6856f2ca65b61d6cbbf4bf85eb3f5fdafac06108d289e922e94619b042ed466',
    'index': '1adc086fd401a4ea9bf1650636ab779342cd469fb4730774b04afee8b2969ba4',
    'sessions': '35715c4a971eeffe14ef885db67f381f862ba3d1fc3c3da464fc2101a989dc4c',
    'registry': 'ac3a5030ad58280869a9e72a930de476734da9e77447bbc2ecb33d40a2f530c5',
    'catalogue': '0ffcf2b98e1fd376b9bf3c78e5123f27f3fa1c138e19165220d85a43c2b77953',
}


# Explicitly reviewed sessions.title migration; preserve old backups.
SESSION_IDENTITIES = {SUPPORTED['sessions'],
    'd3f44886cc840f4f0b08d9ca59dec479e0b5f9ec7805005afb2d09f221a1478b'}

def schema_identity(con):
    rows = con.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name LIMIT 257'
    ).fetchall()
    if len(rows) > 256:
        raise SnapshotBlocked('schema_limit')
    # Preserve SQL exactly, including literals, constraints, indexes and triggers.
    # Equivalent but differently serialized schemas require review, not auto-approval.
    value = [con.execute('PRAGMA user_version').fetchone()[0],
             con.execute('PRAGMA application_id').fetchone()[0],
             [list(row) for row in rows]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode()).hexdigest()


def inspect_schemas(root, structure, *, reader=database_snapshot):
    checks = []
    for database in structure['databases']:
        item = {'path': database['path'], 'kind': database['kind'], 'status': 'needs_review'}
        try:
            with reader(root / database['path']) as con:
                identity = schema_identity(con)
            item['identity'] = identity
            if (identity in SESSION_IDENTITIES if database['kind'] == 'sessions'
                    else identity == SUPPORTED.get(database['kind'])):
                item['status'] = 'recognized'
            else:
                item['reason'] = 'unsupported_schema_identity'
        except SnapshotBlocked:
            item['reason'] = 'schema_snapshot_unavailable'
        checks.append(item)
    return {'manifest': '2026-10-29-v1', 'checks': checks, 'import_available': False,
            'status': ('not_present' if not checks else 'recognized'
                       if all(c['status'] == 'recognized' for c in checks) else 'needs_review')}
