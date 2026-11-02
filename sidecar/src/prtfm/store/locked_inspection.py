"""Inspection under the new lifecycle gate; old writers are NOT excluded."""
import os
import time
from pathlib import Path

from prtfm.store.import_inspection import inspect_import_source
from prtfm.store.lifecycle import StorageBlocked, _lock
from prtfm.store.ownership import _unsafe, inspect_ownership


def _inventory(root, deadline, limit=20000):
    entries = {}
    def walk(path, depth=0):
        if depth > 12 or time.monotonic() >= deadline or len(entries) >= limit:
            raise StorageBlocked('inventory_limit')
        info = path.lstat()
        if _unsafe(info):
            raise StorageBlocked('unsafe_source')
        entries[str(path.relative_to(root))] = (info.st_dev, info.st_ino, info.st_size,
                                               info.st_mtime_ns, info.st_ctime_ns)
        if path.is_dir():
            with os.scandir(path) as children:
                for child in children:
                    walk(path / child.name, depth + 1)
    walk(root)
    return entries


def inspect_locked_source(root, *, check_payloads=False):
    root = Path(os.path.abspath(root))
    result = {'read_only_data': True, 'import_available': False, 'status': 'blocked',
              'consistency': 'cooperating_writers_only', 'reason': None}
    try:
        owner = inspect_ownership(root)
        if owner.state != 'owned':
            raise StorageBlocked('marked_source_required')
        canonical = root.resolve()
        with _lock(canonical):
            if root.resolve() != canonical or inspect_ownership(root) != owner:
                raise StorageBlocked('storage_changed')
            before = _inventory(root, time.monotonic() + 2)
            report = inspect_import_source(root)
            if report['status'] == 'observed':
                from prtfm.store.import_schema import inspect_schemas
                result['schema_inspection'] = inspect_schemas(root, report)
                if result['schema_inspection']['status'] == 'needs_review':
                    report['status'] = 'needs_review'
            if check_payloads and report['status'] == 'observed':
                from prtfm.store.import_payloads import inspect_payloads
                result['payload_inspection'] = inspect_payloads(root, report)
                if result['payload_inspection']['status'] != 'verified_scope':
                    report['status'] = 'needs_review'
            after = _inventory(root, time.monotonic() + 2)
            result['inspection'] = report
            result['entries_observed'] = len(before)
            if before != after:
                raise StorageBlocked('source_changed')
            result['status'] = report['status']
            result['reason'] = 'offline_compatibility_and_payload_checks_required'
    except StorageBlocked as exc:
        result['status'] = 'blocked'
        result['reason'] = str(exc)
    except OSError:
        result['status'] = 'blocked'
        result['reason'] = 'source_unreadable'
    return result
