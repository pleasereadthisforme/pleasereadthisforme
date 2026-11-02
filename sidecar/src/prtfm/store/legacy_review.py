"""Observational legacy review. Never adopts, locks, migrates or authorizes import."""
import time
from pathlib import Path

from prtfm.store.import_inspection import inspect_import_source
from prtfm.store.import_payloads import inspect_payloads
from prtfm.store.import_schema import inspect_schemas
from prtfm.store.lifecycle import StorageBlocked
from prtfm.store.locked_inspection import _inventory
from prtfm.store.ownership import inspect_ownership


def review_legacy_source(root):
    root = Path(root).absolute()
    report = {'read_only': True, 'import_available': False, 'status': 'blocked',
              'consistency': 'observational_not_atomic', 'reason': None,
              'remaining_checks': ['explicit_consent', 'protected_capture', 'execution_revalidation']}
    try:
        owner = inspect_ownership(root)
        if owner.state != 'legacy':
            report['reason'] = 'unmarked_source_required'
            return report
        before = _inventory(root, time.monotonic() + 2)
        allowed = {'registered-folders.db', 'registered-folders.db-wal',
                   'registered-folders.db-shm', 'folders', 'shared-results', 'shared-vectors'}
        if any(Path(name).parts[0] not in allowed for name in before if name != '.'):
            report['reason'] = 'unknown_storage_entry'
            return report
        structure = inspect_import_source(root)
        report['structure'] = structure
        report['status'] = 'needs_review'
        if structure['status'] != 'observed':
            report['reason'] = 'structure_unverified'
        elif not structure['databases']:
            report['status'] = 'blocked'
            report['reason'] = 'recognized_database_required'
        else:
            # Keep the source snapshot reader and its 16MiB bound. The larger
            # prepared_database reader is only for protected private import copies.
            schemas = inspect_schemas(root, structure)
            report['schemas'] = schemas
            if schemas['status'] != 'recognized':
                report['reason'] = 'schema_unverified'
            else:
                payloads = inspect_payloads(root, structure)
                report['payloads'] = payloads
                if payloads['status'] != 'verified_scope':
                    report['reason'] = 'payloads_unverified'
                else:
                    report['status'] = 'recognized_candidate'
                    report['reason'] = 'protected_execution_required'
        if before != _inventory(root, time.monotonic() + 2) or inspect_ownership(root) != owner:
            report['status'] = 'blocked'
            report['reason'] = 'source_changed'
    except (StorageBlocked, OSError, ValueError, TypeError, RecursionError):
        report['status'] = 'blocked'
        report['reason'] = 'source_unavailable_or_changed'
    return report
