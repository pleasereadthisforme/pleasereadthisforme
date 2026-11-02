"""Read-only shared-vector inventory. Findings are not deletion authorization."""
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from prtfm.config import data_root

_KEY = re.compile(r'[0-9a-f]{64}')
_FOLDER = re.compile(r'[0-9a-f]{16}')


@contextmanager
def _database(path):
    # Never use Store/open_workspace: those may migrate or recover paid data.
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.25)
    con.row_factory = sqlite3.Row
    try:
        con.execute('PRAGMA query_only=ON')
        con.execute('BEGIN')
        yield con
    finally:
        con.close()


def _workspace_states(path):
    with _database(path) as con:
        active = list(con.execute('SELECT generation,doc_id,revision FROM semantic_files WHERE generation IS NOT NULL'))
        staging = list(con.execute('SELECT generation,doc_id,revision FROM semantic_staging'))
    return {(r['generation'], r['doc_id'], r['revision']) for r in active + staging}


def audit_shared_vectors():
    """Observe existing paths only; no repair, provider access, or vector loading.

    Each SQLite read has a snapshot, but the whole report is not an atomic
    snapshot of running jobs and the filesystem. Recheck offline before repair.
    """
    base = data_root().resolve()
    directory = base / 'shared-vectors'
    findings = []
    report = {'schema_version': 1, 'scope': 'shared_vectors', 'read_only': True,
              'consistency': 'observational_not_atomic', 'payload_contents_verified': False,
              'artifacts': 0, 'references': 0, 'findings': findings}

    def finding(kind, **details):
        findings.append({'kind': kind, **details})

    def inside(path):
        return path.resolve().is_relative_to(base)

    if not inside(directory):
        finding('outside_data_root', target='shared-vectors')
        return report
    catalogue = directory / 'catalogue.db'
    if not inside(catalogue):
        finding('outside_data_root', target='catalogue')
        return report
    if not catalogue.is_file():
        if directory.exists():
            finding('catalogue_missing')
        return report
    try:
        with _database(catalogue) as con:
            artifacts = list(con.execute('SELECT key,ready FROM artifacts ORDER BY key'))
            references = list(con.execute('SELECT * FROM refs ORDER BY path,generation'))
    except (sqlite3.Error, OSError):
        finding('catalogue_unreadable')
        return report
    report.update(artifacts=len(artifacts), references=len(references))
    keys = {r['key'] for r in artifacts}
    used = {r['key'] for r in references}
    states = {}
    for ref in references:
        try:
            path = Path(ref['path'])
            relative = path.resolve().relative_to(base / 'folders')
            valid_path = (path.is_absolute() and len(relative.parts) == 2
                          and _FOLDER.fullmatch(relative.parts[0]) and relative.parts[1] == 'vectors')
        except (ValueError, TypeError, OSError):
            valid_path = False
        if not valid_path:
            finding('invalid_reference_path', generation=ref['generation'])
            continue
        folder = relative.parts[0]
        if ref['key'] not in keys:
            finding('reference_without_artifact', folder=folder, generation=ref['generation'])
        database = path.resolve().parent / 'index.db'
        if folder not in states:
            if not inside(database):
                states[folder] = None
                finding('outside_data_root', folder=folder, target='workspace_db')
            elif not database.is_file():
                states[folder] = None
                finding('workspace_db_missing', folder=folder)
            else:
                try:
                    states[folder] = _workspace_states(database)
                except (sqlite3.Error, OSError):
                    states[folder] = None
                    finding('workspace_db_unreadable', folder=folder)
        expected = (ref['generation'], ref['doc_id'], ref['revision'])
        if states[folder] is not None and expected not in states[folder]:
            finding('reference_not_in_workspace', folder=folder, generation=ref['generation'])
    for artifact in artifacts:
        key = artifact['key']
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            finding('invalid_artifact_key')
            continue
        payload = directory / 'objects' / key / 'chunks.lance'
        if not inside(payload):
            finding('outside_data_root', key=key, target='payload')
        elif artifact['ready'] and not payload.is_dir():
            finding('ready_payload_missing', key=key)
        # Incomplete staging may legitimately have no payload yet.
        if key not in used:
            finding('artifact_without_reference', key=key)
    objects = directory / 'objects'
    if not inside(objects):
        finding('outside_data_root', target='objects')
    elif objects.is_dir():
        for path in sorted(objects.iterdir()):
            if path.name not in keys:
                finding('untracked_payload_entry', entry=path.name)
    return report


def audit_storage():
    """Extend the shared report with conservative workspace/generation observations."""
    import json

    report = audit_shared_vectors()
    report.update(schema_version=2, scope='storage_inventory', workspaces=0,
                  lease_state_verified=False, generations=[], leases=[])
    base = data_root().resolve()
    findings = report['findings']

    def issue(kind, **details):
        value = {'kind': kind, **details}
        if value not in findings:
            findings.append(value)

    def safe(path):
        if not path.resolve().is_relative_to(base):
            issue('outside_data_root', target=str(path.relative_to(base)))
            return False
        return True

    shared = base / 'shared-vectors'
    catalogue = shared / 'catalogue.db'
    refs = None
    if safe(catalogue) and catalogue.is_file():
        try:
            with _database(catalogue) as con:
                refs = {(str(Path(r['path']).resolve()), r['generation'], r['doc_id'], r['revision'])
                        for r in con.execute('SELECT * FROM refs')}
        except (sqlite3.Error, OSError, TypeError, ValueError):
            pass  # Unknown catalogue must not be treated as an empty catalogue.
    objects = shared / 'objects'
    if refs is None and safe(objects) and objects.is_dir():
        for path in sorted(objects.iterdir()):
            issue('payload_ownership_unknown', entry=path.name)
    folders = base / 'folders'
    if not safe(folders) or not folders.is_dir():
        return report
    for folder in sorted(folders.iterdir()):
        if not _FOLDER.fullmatch(folder.name) or not safe(folder) or not folder.is_dir():
            continue
        report['workspaces'] += 1
        database = folder / 'index.db'
        states, docs = None, None
        if safe(database):
            if not database.is_file():
                issue('workspace_db_missing', folder=folder.name)
            else:
                try:
                    states = _workspace_states(database)
                    with _database(database) as con:
                        docs = list(con.execute("SELECT id,revision FROM docs WHERE status='ok'"))
                except (sqlite3.Error, OSError):
                    issue('workspace_db_unreadable', folder=folder.name)
        vectors = folder / 'vectors'
        local = vectors / 'chunks.lance'
        local_present = safe(local) and local.is_dir()
        if states is not None:
            for generation, doc_id, revision in sorted(states):
                identity = (str(vectors.resolve()), generation, doc_id, revision)
                if refs is not None and identity in refs:
                    continue
                if refs is None:
                    kind = 'workspace_vector_reference_unknown'
                elif local_present:
                    # Local legacy storage may own it; don't guess from directory existence.
                    kind = 'local_vector_generation_unverified'
                else:
                    kind = 'workspace_vector_reference_missing'
                issue(kind, folder=folder.name, generation=generation)
        cache = folder / 'cache'
        if not safe(cache):
            continue
        protected = set()
        lease_dir = cache / '.leases'
        if safe(lease_dir) and lease_dir.is_dir():
            for lease in sorted(lease_dir.glob('*.json')):
                if not safe(lease):
                    continue
                try:
                    pairs = json.loads(lease.read_text())
                    if not isinstance(pairs, list) or any(
                        not isinstance(pair, list) or len(pair) != 2 or
                        any(type(n) is not int or n <= 0 for n in pair) for pair in pairs
                    ):
                        raise ValueError('invalid lease')
                    protected.update(tuple(pair) for pair in pairs)
                    report['leases'].append({'folder': folder.name, 'entry': lease.name,
                                             'state': 'recorded_liveness_unknown', 'generations': pairs})
                except (OSError, ValueError):
                    issue('lease_unreadable', folder=folder.name, entry=lease.name)
        active = None if docs is None else {(d['id'], d['revision']) for d in docs}
        if active is not None:
            for doc_id, revision in sorted(active):
                if type(doc_id) is not int or type(revision) is not int or min(doc_id, revision) <= 0:
                    issue('invalid_generation_identity', folder=folder.name)
                    continue
                generation = cache / str(doc_id) / str(revision)
                metadata = generation / 'metadata.json'
                if not safe(metadata):
                    continue
                try:
                    meta = json.loads(metadata.read_text())
                    if not isinstance(meta, dict):
                        raise ValueError('invalid metadata')
                except FileNotFoundError:
                    legacy = cache / f'{doc_id}.md'
                    kind = 'legacy_generation_unverified' if safe(legacy) and legacy.is_file() else 'generation_metadata_missing'
                    issue(kind, folder=folder.name, doc_id=doc_id, revision=revision)
                    continue
                except (OSError, ValueError):
                    issue('generation_metadata_unreadable', folder=folder.name, doc_id=doc_id, revision=revision)
                    continue
                if meta.get('source_kind') == 'original_text':
                    kind = 'original_metadata_only'
                else:
                    kind = 'stored_body'
                    body = generation / 'document.md'
                    if safe(body) and not body.is_file():
                        issue('generation_body_missing', folder=folder.name, doc_id=doc_id, revision=revision)
                report['generations'].append({'folder': folder.name, 'doc_id': doc_id, 'revision': revision,
                                              'kind': kind, 'lease_recorded': (doc_id, revision) in protected})
        # Observe past generations; never infer that unlisted/unleased means deletable.
        if cache.is_dir():
            for document in sorted(cache.iterdir()):
                if not document.name.isdigit() or not safe(document) or not document.is_dir():
                    continue
                for generation in sorted(document.iterdir()):
                    if not generation.name.isdigit() or not safe(generation) or not generation.is_dir():
                        continue
                    identity = (int(document.name), int(generation.name))
                    if active is None or identity not in active:
                        report['generations'].append({'folder': folder.name, 'doc_id': identity[0],
                            'revision': identity[1], 'kind': 'nonactive_or_unknown',
                            'lease_recorded': identity in protected})
    return report
