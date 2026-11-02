"""Metadata-only backup planning. Never grants permission to copy or restore."""
import os
import re
import shutil
import stat
import time
from pathlib import Path

from prtfm.config import storage_paths_overlap
from prtfm.store import import_policy
from prtfm.store.lifecycle import StorageBlocked
from prtfm.store.locked_inspection import _inventory
from prtfm.store.ownership import MARKER, _unsafe, inspect_ownership


def classify(parts):
    """Unknown files fail closed; vector payloads require later format validation."""
    if not parts:
        return 'blocked', 'unclassified_entry'
    if parts[:2] == ('runtime', 'search'):
        return 'blocked', 'search_scratch_requires_owner_review'
    if parts == (MARKER,):
        return 'include', 'ownership'
    if parts[0] == 'registered-folders.db' and len(parts) == 1:
        return 'include', 'records'
    if len(parts) == 1 and parts[0] in ('registered-folders.db-wal', 'registered-folders.db-shm'):
        return 'include', 'database_recovery'
    if len(parts) == 3 and parts[:2] == ('analysis-cache', 'vision') and re.fullmatch(r'[0-9a-f]{64}\.json', parts[2]):
        return 'exclude', 'analysis_cache'
    if (parts == ('analysis-cache', 'vision', '.lock')
            or (len(parts) == 2 and parts[0] == 'shared-results'
                and re.fullmatch(r'[0-9a-f]{64}\.lock', parts[1]))):
        return 'exclude', 'known_lock_after_shutdown'
    if len(parts) == 2 and parts[0] == 'shared-results' and parts[1].endswith('.json'):
        return 'include', 'shared_references'
    if len(parts) >= 2 and parts[0] == 'shared-vectors':
        return 'include', 'vector_payload_requires_validation'
    if len(parts) >= 3 and parts[0] == 'folders' and re.fullmatch('[0-9a-f]{16}', parts[1]):
        tail = parts[2:]
        if tail == ('writer.lock',):
            return 'exclude', 'writer_lock'
        if len(tail) == 1 and tail[0] in tuple(n + s for n in ('index.db', 'sessions.db', 'embedding-usage.db') for s in ('', '-wal', '-shm')):
            return 'include', 'records_and_database_recovery'
        if tail[0] == 'vectors' and len(tail) > 1:
            return 'include', 'vector_payload_requires_validation'
        if (len(tail) == 4 and tail[0] == 'cache' and all(p.isdecimal() for p in tail[1:3])
                and tail[3] in ('document.md', 'metadata.json')):
            return 'include', 'processed_document'
        if len(tail) == 2 and tail[0] == 'cache' and re.fullmatch(r'\d+\.(md|json)', tail[1]):
            return 'include', 'legacy_processed_document'
        if len(tail) == 2 and tail[0] in ('vision-quotes', 'semantic-quotes') and tail[1].endswith('.result.json'):
            return 'include', 'cost_result'
        if len(tail) == 2 and tail[0] == 'vision-quotes':
            if re.fullmatch(r'[0-9a-f]{32}\.json', tail[1]):
                # Consumed quotes are diagnostic evidence too; never drop by age.
                return 'include', 'quote_requires_invalidation_on_restore'
            if tail[1].endswith('.tmp'):
                return 'blocked', 'interrupted_cost_write_requires_review'
        if len(tail) == 2 and tail[0] == 'run-leases' and re.fullmatch('[0-9a-f]{32}', tail[1]):
            return 'exclude', 'run_lease_after_shutdown'
        if tail == ('cache', '.generations.lock'):
            return 'exclude', 'generation_lock_after_shutdown'
        if (len(tail) == 3 and tail[:2] == ('cache', '.leases')
                and re.fullmatch(r'[0-9a-f]{32}\.(json|lock)', tail[2])):
            return 'exclude', 'generation_lease_after_shutdown'
        if len(tail) >= 3 and tail[0] == 'cache' and any(p.startswith('.prepare-') for p in tail):
            return 'blocked', 'interrupted_preparation_requires_review'
    return 'blocked', 'unclassified_entry'


def plan_backup(source, destination_parent, *, max_seconds=5):
    """Observe sizes and classification; no lock file, DB open or staging creation."""
    root = Path(os.path.abspath(source))
    parent = Path(os.path.abspath(destination_parent))
    result = {'version': 1, 'status': 'blocked', 'read_only': True,
              'backup_available': False, 'consistency': 'observational_not_atomic',
              'writer_state': 'not_checked', 'entries': [], 'included_bytes': 0,
              'excluded_bytes': 0, 'included_files': 0}
    deadline = time.monotonic() + max_seconds
    try:
        for path in (root, parent):
            for component in (path, *path.parents):
                if _unsafe(component.lstat()):
                    raise StorageBlocked('unsafe_path')
            if not path.is_dir():
                raise StorageBlocked('directory_required')
        if storage_paths_overlap(root, parent):
            raise StorageBlocked('overlapping_backup_paths')
        owner = inspect_ownership(root)
        if owner.state != 'owned':
            raise StorageBlocked('marked_source_required')
        before = _inventory(root, deadline)
        reasons = set()
        for name in sorted(before):
            if time.monotonic() >= deadline:
                raise StorageBlocked('inventory_limit')
            path = root / name
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                continue
            action, reason = classify(Path(name).parts)
            result['entries'].append({'path': name, 'action': action, 'reason': reason, 'bytes': info.st_size})
            if action == 'blocked':
                reasons.add(reason)
            elif action == 'exclude':
                result['excluded_bytes'] += info.st_size
            else:
                # Conservative Windows MAX_PATH policy until every dependency
                # is validated with extended-length paths. Count UTF-16 units.
                projected = parent / ('PRTFM-backup-' + '0' * 12) / 'payload' / name
                if os.name == 'nt' and len(str(projected).encode('utf-16-le')) // 2 >= 260:
                    reasons.add('backup_path_too_long')
                result['included_bytes'] += info.st_size
                result['included_files'] += 1
                if path.suffix == '.db' and info.st_size > import_policy.PREPARED_DATABASE_LIMIT:
                    reasons.add('snapshot_size_limit')
                if path.name == 'document.md' and info.st_size > import_policy.BODY_LIMIT:
                    reasons.add('body_size_limit')
                if path.name == 'metadata.json' and info.st_size > import_policy.METADATA_LIMIT:
                    reasons.add('metadata_size_limit')
        if before != _inventory(root, deadline) or inspect_ownership(root) != owner:
            raise StorageBlocked('source_changed')
        result['protected_files'] = len(result['entries'])
        result['protected_bytes'] = sum(entry['bytes'] for entry in result['entries'])
        if result['protected_bytes'] > import_policy.MAX_BYTES:
            reasons.add('capture_size_limit')
        if result['protected_files'] > import_policy.MAX_FILES:
            reasons.add('capture_file_limit')
        required = result['included_bytes'] * 2 + 64 * 1024**2
        free = shutil.disk_usage(parent).free
        result.update(required_free_bytes=required, free_bytes=free,
                      size_basis='logical_bytes_including_hardlinks')
        if free < required:
            reasons.add('insufficient_disk_space')
        result.update(status='blocked' if reasons else 'planned', reasons=sorted(reasons))
    except StorageBlocked as exc:
        result['reasons'] = [str(exc)]
    except OSError:
        result['reasons'] = ['path_unavailable']
    return result
