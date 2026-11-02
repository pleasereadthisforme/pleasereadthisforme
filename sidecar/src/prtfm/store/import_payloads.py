"""Reuse bounded body/vector validators against snapshot-selected generations."""
import json

from prtfm.store.body_audit import METADATA_LIMIT, _read, inspect_bodies
from prtfm.store.offline_snapshot import SnapshotBlocked, database_snapshot
from prtfm.store.vector_audit import inspect_vectors


def inspect_payloads(root, structure, *, reader=database_snapshot):
    report = {'generations': [], 'findings': [], 'import_available': False,
              'scope': 'active_stored_bodies_and_completed_shared_vectors', 'original_text_skipped': 0}
    for database in structure['databases']:
        if database['kind'] != 'index':
            continue
        path = root / database['path']
        folder = path.parent.name
        try:
            with reader(path) as con:
                rows = con.execute('SELECT id,revision FROM docs WHERE parse_stage>0 LIMIT 2001').fetchall()
            if len(rows) > 2000:
                raise SnapshotBlocked('document_limit')
            for row in rows:
                if type(row['id']) is not int or type(row['revision']) is not int or min(row['id'], row['revision']) < 0:
                    raise SnapshotBlocked('invalid_document_identity')
                directory = path.parent / 'cache' / str(row['id']) / str(row['revision'])
                metadata_path = directory / 'metadata.json'
                if not metadata_path.resolve().is_relative_to(root.resolve()):
                    raise SnapshotBlocked('outside_storage')
                metadata = json.loads(_read(metadata_path, METADATA_LIMIT))
                if not isinstance(metadata, dict):
                    raise SnapshotBlocked('invalid_metadata')
                if metadata.get('source_kind') == 'original_text':
                    report['original_text_skipped'] += 1
                    continue
                report['generations'].append({'kind': 'stored_body', 'folder': folder,
                                              'doc_id': row['id'], 'revision': row['revision']})
        except (SnapshotBlocked, OSError, ValueError, TypeError, OverflowError, RecursionError):
            report['findings'].append({'kind': 'generation_snapshot_unverified', 'folder': folder})
    inspect_bodies(report, root=root)
    if any(db['kind'] == 'catalogue' for db in structure['databases']):
        inspect_vectors(report, root=root, reader=reader)
    else:
        report['vector_checks'] = []
    if any(c['status'] != 'verified' for c in report['vector_checks']):
        report['findings'].append({'kind': 'vector_payloads_incomplete'})
    report['status'] = 'verified_scope' if not report['findings'] else 'needs_review'
    return report
