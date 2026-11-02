"""Bounded read-only checks of completed shared vector artifacts."""
import json
import re

from prtfm.config import data_root
from prtfm.store.audit import _database
from prtfm.store.vectors import DIMENSIONS, MODEL, RawVectors, validate

MAX_ROWS = 10000
BATCH_ROWS = 128
_HASH = re.compile(r'[0-9a-f]{64}')


def _artifacts(path, reader=_database):
    with reader(path) as con:
        return {r['key']: dict(r) for r in con.execute('SELECT key,ready,expected FROM artifacts')}


def _artifact(path, key, reader=_database):
    with reader(path) as con:
        row = con.execute('SELECT key,ready,expected FROM artifacts WHERE key=?', (key,)).fetchone()
        return dict(row) if row else None


def inspect_vectors(report, *, root=None, reader=_database):
    root = (data_root() if root is None else root).resolve()
    catalogue = root / 'shared-vectors/catalogue.db'
    checks = []
    report['vector_checks'] = checks
    report['vector_check_scope'] = 'completed_shared_artifacts_against_catalogue'
    report['vector_check_limits'] = {'max_rows_per_artifact': MAX_ROWS, 'batch_rows': BATCH_ROWS}
    if not catalogue.resolve().is_relative_to(root):
        report['findings'].append({'kind': 'vector_catalogue_unavailable'})
        return report
    try:
        artifacts = _artifacts(catalogue, reader)
    except Exception:
        report['findings'].append({'kind': 'vector_catalogue_unavailable'})
        return report
    for key, artifact in artifacts.items():
        status, reason, count = 'verified', None, 0
        table, version = None, None
        phase = 'metadata'
        try:
            if not isinstance(key, str) or not _HASH.fullmatch(key):
                raise ValueError('invalid key')
            if not artifact['ready']:
                checks.append({'key': key, 'status': 'skipped', 'reason': 'incomplete_staging', 'rows_checked': 0})
                continue
            if len(artifact['expected']) > 2 * 1024 * 1024:
                raise OverflowError('metadata_limit')
            expected = json.loads(artifact['expected'])
            if (not isinstance(expected, dict) or not expected or
                    any(not str(i).isdigit() or str(int(i)) != i or not isinstance(h, str) or
                        not _HASH.fullmatch(h) for i, h in expected.items())):
                raise ValueError('invalid expected chunks')
            if len(expected) > MAX_ROWS:
                raise OverflowError('row_limit')
            path = root / 'shared-vectors/objects' / key
            if not (path / 'chunks.lance').resolve().is_relative_to(root):
                raise ValueError('outside data root')
            if not (path / 'chunks.lance').is_dir():
                raise ValueError('payload missing')
            phase = 'table'
            table = RawVectors(path)._table()  # existing-only; no create/add/recover
            version = table.version
            table.checkout(version)
            if table.count_rows() > MAX_ROWS:
                raise OverflowError('row_limit')
            if table.count_rows() != len(expected):
                raise ValueError('row count mismatch')
            seen = set()
            phase = 'rows'
            for offset in range(0, len(expected), BATCH_ROWS):
                rows = table.search().offset(offset).limit(min(BATCH_ROWS, len(expected) - offset)).to_list()
                for row in rows:
                    idx = row['idx']
                    if (type(idx) is not int or idx in seen or expected.get(str(idx)) != row['text_hash']
                            or row['generation'] != key or row['doc_id'] != 0 or row['revision'] != 0
                            or row['model'] != MODEL or row['dimensions'] != DIMENSIONS):
                        raise ValueError('row metadata mismatch')
                    validate(row['vector'])
                    seen.add(idx)
                    count += 1
                del rows
            if len(seen) != len(expected):
                raise ValueError('incomplete rows')
        except OverflowError as exc:
            status, reason = 'skipped', str(exc)
        except Exception:
            # Diagnostic failures should not expose document/vector contents.
            status, reason = 'failed', phase + '_failed'
        finally:
            if version is not None:
                try:
                    table.checkout_latest()
                    if table.version != version:
                        status, reason = 'changed', 'table_changed_during_check'
                except Exception:
                    status, reason = 'changed', 'table_unavailable_after_check'
        try:
            if _artifact(catalogue, key, reader) != artifact:
                status, reason = 'changed', 'catalogue_changed_during_check'
        except Exception:
            status, reason = 'changed', 'catalogue_unavailable_after_check'
        checks.append({'key': key, 'status': status, 'reason': reason, 'rows_checked': count})
        if status != 'verified':
            report['findings'].append({'kind': 'shared_vector_check_' + status, 'key': key, 'reason': reason})
    return report
