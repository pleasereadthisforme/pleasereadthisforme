"""Bounded, observational inspection of existing SQLite shapes and shared paths.

Never migrates, checkpoints, creates a DB, reads original documents or approves
an import. WAL/journal files require a later offline snapshot, not recovery here.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import time
from pathlib import Path

from prtfm.store.offline_snapshot import DATABASE_LIMIT
from prtfm.store.ownership import _unsafe

SHAPES = {
    'embedding_usage': {'semantic_usage': {'id', 'job_id', 'kind', 'tokens',
                                         'reserved_nusd', 'actual_nusd', 'state', 'created'}},
    'registry': {'folders': {'id', 'path'}, 'workspaces': {'path'}},
    'catalogue': {'artifacts': {'key', 'ready', 'expected'},
                  'refs': {'path', 'generation', 'key', 'doc_id', 'revision'}},
    'index': {'docs': {'id', 'path', 'revision'}, 'chunks': {'rid', 'doc_id'},
              'app_meta': {'key', 'value'}, 'semantic_files': {'doc_id', 'generation', 'revision'},
              'semantic_staging': {'doc_id', 'generation', 'revision'}},
    'sessions': {'sessions': {'id', 'created'}, 'runs': {'id', 'session_id', 'state'},
                 'turns': {'id', 'session_id', 'question', 'answer'}},
}


def inspect_import_source(root: Path, *, max_entries=20000, max_bytes=DATABASE_LIMIT,
                          max_rows=2000, max_seconds=2):
    root = Path(os.path.abspath(root))
    started = time.monotonic()
    workspace_states, shared_refs = {}, []
    report = {'read_only': True, 'status': 'observed', 'import_available': False,
              'consistency': 'observational_not_atomic', 'databases': [], 'findings': [],
              'entries': 0, 'references_examined': 0, 'payloads_verified': False,
              'remaining_checks': ['exclusive_offline_snapshot', 'full_schema_compatibility',
                                   'workspace_generation_consistency', 'body_and_vector_integrity']}
    def finding(code, path):
        report['findings'].append({'code': code, 'path': str(path.relative_to(root))})
    def check_path(path):
        # Only inspect storage metadata. Do not follow any existing component link.
        try:
            rel = path.relative_to(root)
            current = root
            for component in (None, *rel.parts):
                if component is not None:
                    current /= component
                if _unsafe(current.lstat()):
                    return False
            return True
        except (OSError, ValueError):
            return False
    def limited():
        return report['entries'] >= max_entries or time.monotonic() - started >= max_seconds
    def signature(path):
        s = path.stat(follow_symlinks=False)
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns
    def reference(value, source, kind):
        report['references_examined'] += 1
        if not isinstance(value, str):
            finding('invalid_reference', source)
            return
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts:
            finding('invalid_reference', source)
            return
        try:
            rel = path.relative_to(root)
        except ValueError:
            finding('reference_outside_storage', source)
            return
        parts = rel.parts
        if (len(parts) < 3 or parts[0] != 'folders' or not re.fullmatch('[0-9a-f]{16}', parts[1])
                or parts[2] != kind or (kind == 'vectors' and len(parts) != 3)):
            finding('unexpected_reference_target', source)
            return
        target = path / 'document.md' if kind == 'cache' else path
        if not check_path(target):
            finding('reference_missing_or_linked', source)
    def db_kind(path):
        rel = path.relative_to(root).parts
        if rel == ('registered-folders.db',):
            return 'registry'
        if rel == ('shared-vectors', 'catalogue.db'):
            return 'catalogue'
        if len(rel) == 3 and rel[0] == 'folders' and re.fullmatch('[0-9a-f]{16}', rel[1]):
            return {'index.db': 'index', 'sessions.db': 'sessions', 'embedding-usage.db': 'embedding_usage'}.get(rel[2])
        return None
    def database(path):
        kind = db_kind(path)
        if not kind:
            finding('unknown_database', path)
            return
        sidecars = [Path(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal')]
        if any(os.path.lexists(p) for p in sidecars):
            finding('offline_snapshot_required', path)
            return
        before = signature(path)
        if before[2] > max_bytes:
            finding('size_limit', path)
            return
        con = None
        try:
            # Immutable prevents SQLite creating SHM/WAL or recovering journals.
            # It does NOT make a concurrent writer safe; result never authorizes import.
            con = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=.1)
            con.execute('PRAGMA query_only=ON')
            con.execute('PRAGMA trusted_schema=OFF')
            con.set_progress_handler(lambda: int(time.monotonic() - started >= max_seconds), 1000)
            tables = dict(con.execute("SELECT name,type FROM sqlite_schema WHERE type IN ('table','view') LIMIT 257"))
            if len(tables) > 256:
                finding('schema_limit', path)
                return
            for table, required in SHAPES[kind].items():
                if tables.get(table) != 'table':
                    finding('unrecognized_schema', path)
                    return
                columns = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
                if not required <= columns:
                    finding('unrecognized_schema', path)
                    return
            if kind == 'index':
                versions = con.execute("SELECT value FROM app_meta WHERE key='schema_version' LIMIT 2").fetchall()
                if versions != [(2,)]:
                    finding('unsupported_index_version', path)
                    return
            report['databases'].append({'path': str(path.relative_to(root)), 'kind': kind,
                                        'required_shape_observed': True})
            if kind == 'index':
                states = set()
                for table in ('semantic_files', 'semantic_staging'):
                    rows = con.execute(f'SELECT generation,doc_id,revision FROM {table} WHERE generation IS NOT NULL LIMIT ?', (max_rows + 1,)).fetchall()
                    if len(rows) > max_rows:
                        finding('row_limit', path)
                    for generation, doc_id, revision in rows[:max_rows]:
                        if not isinstance(generation, str) or type(doc_id) is not int or type(revision) is not int:
                            finding('invalid_workspace_generation', path)
                            continue
                        states.add((generation, doc_id, revision))
                        doc = con.execute('SELECT revision FROM docs WHERE id=? LIMIT 1', (doc_id,)).fetchone()
                        if doc is None or doc[0] != revision:
                            finding('generation_document_mismatch', path)
                workspace_states[str(path.parent / 'vectors')] = states
            if kind == 'catalogue':
                rows = con.execute('SELECT path,key,generation,doc_id,revision FROM refs LIMIT ?', (max_rows + 1,)).fetchall()
                if len(rows) > max_rows:
                    finding('row_limit', path)
                for ref, key, generation, doc_id, revision in rows[:max_rows]:
                    if limited():
                        finding('scan_limit', path)
                        break
                    reference(ref, path, 'vectors')
                    if isinstance(ref, str) and isinstance(generation, str) and type(doc_id) is int and type(revision) is int:
                        shared_refs.append((ref, generation, doc_id, revision))
                    else:
                        finding('invalid_shared_generation', path)
                    if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
                        finding('invalid_artifact_key', path)
                        continue
                    if not con.execute('SELECT 1 FROM artifacts WHERE key=? LIMIT 1', (key,)).fetchone():
                        finding('artifact_record_missing', path)
                    if not check_path(root / 'shared-vectors' / 'objects' / key):
                        finding('artifact_object_missing_or_linked', path)
        except sqlite3.Error:
            finding('database_unreadable_or_budget', path)
        finally:
            if con is not None:
                con.close()
            if signature(path) != before or any(os.path.lexists(p) for p in sidecars):
                finding('source_changed', path)
    def walk(directory, depth=0):
        with os.scandir(directory) as entries:
            for entry in entries:
                if limited():
                    finding('scan_limit', directory)
                    return
                report['entries'] += 1
                path = directory / entry.name
                try:
                    info = entry.stat(follow_symlinks=False)
                    if _unsafe(info):
                        finding('linked_entry', path)
                    elif stat.S_ISDIR(info.st_mode):
                        if depth >= 12:
                            finding('depth_limit', path)
                        else:
                            walk(path, depth + 1)
                    elif stat.S_ISREG(info.st_mode):
                        if not check_path(path):
                            finding('path_changed', path)
                        elif path.suffix == '.db':
                            database(path)
                        elif path.parent == root / 'shared-results' and path.suffix == '.json':
                            before = signature(path)
                            if info.st_size > min(max_bytes, 1024**2):
                                finding('size_limit', path)
                                continue
                            with path.open('rb') as handle:
                                raw = handle.read(min(max_bytes, 1024**2) + 1)
                            if len(raw) > min(max_bytes, 1024**2):
                                finding('size_limit', path)
                                continue
                            refs = json.loads(raw)
                            if not isinstance(refs, list):
                                finding('invalid_shared_results', path)
                            else:
                                if len(refs) > max_rows:
                                    finding('row_limit', path)
                                for ref in refs[:max_rows]:
                                    if limited():
                                        finding('scan_limit', path)
                                        break
                                    reference(ref, path, 'cache')
                            if signature(path) != before:
                                finding('source_changed', path)
                    else:
                        finding('special_entry', path)
                except (OSError, ValueError, RecursionError):
                    finding('entry_unreadable', path)
    if not check_path(root) or not root.is_dir():
        report['status'] = 'unavailable'
        return report
    try:
        walk(root)
    except OSError:
        finding('directory_unreadable', root)
    seen = set(shared_refs)
    for ref, generation, doc_id, revision in shared_refs:
        if ref not in workspace_states:
            finding('referenced_workspace_unverified', root / 'shared-vectors' / 'catalogue.db')
        elif (generation, doc_id, revision) not in workspace_states[ref]:
            finding('shared_generation_not_in_workspace', root / 'shared-vectors' / 'catalogue.db')
    for ref, states in workspace_states.items():
        for generation, doc_id, revision in states:
            if (ref, generation, doc_id, revision) not in seen:
                # Legacy folder-local vectors need a separate migration, never silently drop them.
                finding('workspace_generation_without_shared_ref', Path(ref).parent / 'index.db')
    report['generation_comparison_performed'] = True
    if report['findings']:
        report['status'] = 'needs_review'
    return report
