"""Prepare one disposable capture; never publish it or open product writers."""
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from prtfm.store.import_capture import captured_store
from prtfm.store.import_inspection import inspect_import_source
from prtfm.store.import_payloads import inspect_payloads
from prtfm.store.import_schema import inspect_schemas
from prtfm.store.offline_recovery import _recover_copy, _validate_wal
from prtfm.store.offline_snapshot import PREPARED_DATABASE_LIMIT, SnapshotBlocked, prepared_database


def _rewrite_references(stage, source, destination, deadline):
    """Only rewrite reviewed internal reference fields in a private staging copy."""
    def budget():
        if time.monotonic() >= deadline:
            raise SnapshotBlocked("staging_time_limit")
    def relocate(value, kind):
        if not isinstance(value, str):
            raise SnapshotBlocked('invalid_internal_reference')
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts:
            raise SnapshotBlocked('invalid_internal_reference')
        try:
            relative = path.relative_to(source)
        except ValueError:
            raise SnapshotBlocked('reference_outside_source') from None
        parts = relative.parts
        if (len(parts) < 3 or parts[0] != 'folders'
                or not re.fullmatch('[0-9a-f]{16}', parts[1]) or parts[2] != kind
                or (kind == 'vectors' and len(parts) != 3)
                or (kind == 'cache' and (len(parts) != 5 or not all(p.isdecimal() for p in parts[3:])))):
            raise SnapshotBlocked('unexpected_internal_reference')
        return str(destination / relative)

    rewritten = 0
    for record in (stage / 'shared-results').glob('*.json'):
        budget()
        if record.stat().st_size > 1024**2:
            raise SnapshotBlocked('reference_size_limit')
        values = json.loads(record.read_bytes())
        if not isinstance(values, list) or len(values) > 2000:
            raise SnapshotBlocked('invalid_shared_results')
        updated = [relocate(value, 'cache') for value in values]
        # Atomic replacement also prevents altering any other staging hardlink.
        temporary = record.with_suffix('.rewrite')
        with temporary.open('x', encoding='utf-8') as out:
            json.dump(updated, out)
        temporary.replace(record)
        rewritten += len(updated)
    catalogue = stage / 'shared-vectors/catalogue.db'
    if catalogue.exists():
        con = sqlite3.connect(catalogue)
        try:
            con.execute('PRAGMA trusted_schema=OFF')
            con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            rows = con.execute('SELECT DISTINCT path FROM refs LIMIT 2001').fetchall()
            if len(rows) > 2000:
                raise SnapshotBlocked('reference_row_limit')
            with con:
                for (old,) in rows:
                    budget()
                    con.execute('UPDATE refs SET path=? WHERE path=?', (relocate(old, 'vectors'), old))
                    rewritten += 1
        finally:
            con.close()
    return rewritten


def _prepare(stage, source, deadline):
    """Private: stage must be a disposable copy owned by captured_store."""
    def budget():
        if time.monotonic() >= deadline:
            raise SnapshotBlocked('staging_time_limit')

    databases = list(stage.rglob('*.db'))
    for sidecar in stage.rglob('*'):
        budget()
        if sidecar.name.endswith('-journal'):
            raise SnapshotBlocked('rollback_journal_requires_review')
        if sidecar.name.endswith(('-wal', '-shm')) and not Path(str(sidecar)[:-4]).is_file():
            raise SnapshotBlocked('orphan_database_sidecar')
    for db in databases:
        budget()
        if db.stat().st_size > PREPARED_DATABASE_LIMIT:
            raise SnapshotBlocked('snapshot_size_limit')
        wal = Path(str(db) + '-wal')
        if wal.exists():
            with wal.open('rb') as stream:
                _validate_wal(stream, budget)
        _recover_copy(db, deadline)
    structure = inspect_import_source(stage, max_bytes=PREPARED_DATABASE_LIMIT, max_seconds=30)
    # Old absolute references naturally fail here. All other findings must stop.
    permitted = {'reference_outside_storage', 'referenced_workspace_unverified',
                 'workspace_generation_without_shared_ref'}
    if (structure['status'] not in ('observed', 'needs_review')
            or any(f['code'] not in permitted for f in structure['findings'])):
        raise SnapshotBlocked('staging_structure_unverified')
    schemas = inspect_schemas(stage, structure, reader=prepared_database)
    if schemas['status'] != 'recognized':
        raise SnapshotBlocked('staging_schema_unverified')
    rewritten = _rewrite_references(stage, source, stage, deadline)
    budget()
    structure = inspect_import_source(stage, max_bytes=PREPARED_DATABASE_LIMIT, max_seconds=30)
    if structure['status'] != 'observed':
        raise SnapshotBlocked('staging_references_unverified')
    payloads = inspect_payloads(stage, structure, reader=prepared_database)
    if payloads['status'] != 'verified_scope':
        raise SnapshotBlocked('staging_payloads_unverified')
    budget()
    return {'schema_inspection': schemas, 'payload_inspection': payloads,
            'references_rewritten': True, 'references_changed': rewritten,
            'import_available': False, 'status': 'verified_staging_scope'}


@contextmanager
def prepared_store(root, staging_parent, *, max_bytes, max_seconds, max_files=2000, source_kind="marked"):
    deadline = time.monotonic() + max_seconds
    with captured_store(root, staging_parent, max_bytes=max_bytes,
                        max_seconds=max_seconds, max_files=max_files, source_kind=source_kind) as capture:
        try:
            report = _prepare(capture['path'], Path(root).absolute(), deadline)
            if source_kind == 'legacy':
                from prtfm.store.ownership import MARKER
                # Only the fully validated private copy acquires a Preview owner.
                with (capture['path'] / MARKER).open('x', encoding='utf-8') as handle:
                    json.dump({'application': 'prtfm', 'format_version': 1, 'channel': 'preview'}, handle)
            yield {**capture, **report}
        except (OSError, ValueError, sqlite3.Error, RecursionError) as exc:
            raise SnapshotBlocked('staging_preparation_failed') from exc
