"""Transient Windows capture of existing files, not import authorization.

All existing files are held against write/delete together before copying. New
entries are detected by inventory comparisons, not prevented by a directory lock.
Internal absolute references remain source-relative and MUST NOT be used yet.
"""
import os
import stat
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from prtfm.store.lifecycle import StorageBlocked, _lock
from prtfm.store.locked_inspection import _inventory
from prtfm.store.offline_recovery import _open_protected
from prtfm.store.offline_snapshot import SnapshotBlocked
from prtfm.store.ownership import MARKER, _unsafe, inspect_ownership


@contextmanager
def _captured_store(root, staging_parent, *, max_bytes, max_seconds, max_files=2000, source_kind="marked", backup=False, backup_payload=False):
    """Copy once into a temporary sibling of the future target; always remove it.

    Caller supplies disk/time budgets. No publication, path rewrite, or recovery.
    An unknown top-level entry is refused rather than copying possible secrets.
    """
    if os.name != 'nt':
        raise SnapshotBlocked('windows_capture_required')
    root = Path(os.path.abspath(root))
    parent = Path(os.path.abspath(staging_parent))
    deadline = time.monotonic() + max_seconds
    def check_time():
        if time.monotonic() >= deadline:
            raise SnapshotBlocked('capture_time_limit')
    try:
        for path in (root, parent):
            for part in (path, *path.parents):
                if _unsafe(part.lstat()):
                    raise SnapshotBlocked('unsafe_capture_path')
        # A sibling destination normally shares the source's parent (LOCALAPPDATA).
        # Only staging inside the source is unsafe; mkdtemp creates a new sibling.
        if parent.is_relative_to(root):
            raise SnapshotBlocked('overlapping_capture_paths')
        owner = inspect_ownership(root)
        if source_kind not in ('marked', 'legacy'):
            raise SnapshotBlocked('invalid_source_kind')
        if owner.state != ('legacy' if source_kind == 'legacy' else 'owned'):
            raise SnapshotBlocked('marked_source_required')
        with _lock(root.parent if backup_payload else root), ExitStack() as handles:
            before = _inventory(root, deadline)
            allowed = {MARKER, 'registered-folders.db', 'registered-folders.db-wal',
                       'registered-folders.db-shm', 'folders', 'shared-results', 'shared-vectors'}
            if not backup and any(Path(name).parts[0] not in allowed for name in before if name != '.'):
                raise SnapshotBlocked('unknown_storage_entry')
            files, directories, total = {}, [], 0
            selected, excluded = set(), 0
            for name in before:
                check_time()
                path = root / name
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    directories.append(name)
                    continue
                if not stat.S_ISREG(info.st_mode):
                    raise SnapshotBlocked('special_storage_entry')
                if len(files) >= max_files:
                    raise SnapshotBlocked('capture_file_limit')
                if backup:
                    from prtfm.store.backup_plan import classify
                    action, reason = classify(Path(name).parts)
                    if action == 'blocked':
                        raise SnapshotBlocked(reason)
                    if action == 'include':
                        selected.add(name)
                    else:
                        excluded += 1
                else:
                    selected.add(name)
                # Bound all protected handles/bytes, including excluded entries.
                total += info.st_size
                if total > max_bytes:
                    raise SnapshotBlocked('capture_size_limit')
                stream = handles.enter_context(_open_protected(path))
                current = os.fstat(stream.fileno())
                # Python/Windows path stat and CRT handle stat expose different
                # ctime semantics. Compare identity/size/mtime across APIs;
                # inventory-to-inventory checks below still include ctime.
                signature = (current.st_dev, current.st_ino, current.st_size,
                             current.st_mtime_ns)
                if signature != before[name][:4]:
                    raise SnapshotBlocked('source_changed')
                files[name] = stream
            # All existing files now coexist under protection, before any copy.
            if before != _inventory(root, deadline) or inspect_ownership(root) != owner:
                raise SnapshotBlocked('source_changed')
            if source_kind == 'legacy':
                # Review under the existing-file protection, not a cached approval.
                from prtfm.store.legacy_review import review_legacy_source
                if review_legacy_source(root)['status'] != 'recognized_candidate':
                    raise SnapshotBlocked('legacy_review_required')
                if before != _inventory(root, deadline) or inspect_ownership(root) != owner:
                    raise SnapshotBlocked('source_changed')
            with TemporaryDirectory(prefix='.prtfm-capture-', dir=parent) as temporary:
                target = Path(temporary)
                if not backup:
                    for name in directories:
                        (target / name).mkdir(parents=True, exist_ok=True)
                if backup:
                    # Shared-vector references can point to an empty workspace
                    # vectors directory: it is a scope identity, not scratch.
                    import re
                    for name in directories:
                        parts = Path(name).parts
                        if (len(parts) == 3 and parts[0] == 'folders'
                                and re.fullmatch('[0-9a-f]{16}', parts[1]) and parts[2] == 'vectors'):
                            (target / name).mkdir(parents=True, exist_ok=True)
                linked = {}
                for name, stream in files.items():
                    check_time()
                    if name not in selected:
                        continue
                    identity = before[name][:2]
                    outpath = target / name
                    outpath.parent.mkdir(parents=True, exist_ok=True)
                    if identity in linked:
                        # Preserve body sharing inside staging; never link to source.
                        os.link(linked[identity], outpath)
                        continue
                    with outpath.open('xb') as out:
                        remaining = before[name][2]
                        while block := stream.read(128 * 1024):
                            check_time()
                            remaining -= len(block)
                            if remaining < 0:
                                raise SnapshotBlocked('source_changed')
                            out.write(block)
                        if remaining:
                            raise SnapshotBlocked('source_changed')
                    linked[identity] = outpath
                if before != _inventory(root, deadline):
                    raise SnapshotBlocked('source_changed')
                yield {'path': target, 'files': len(selected), 'excluded_files': excluded, 'copied_objects': len(linked),
                       'import_available': False, 'references_rewritten': False,
                       'consistency': 'existing_files_protected_new_entries_observed'}
                if before != _inventory(root, deadline):
                    raise SnapshotBlocked('source_changed')
    except StorageBlocked as exc:
        raise SnapshotBlocked(str(exc)) from exc
    except OSError as exc:
        raise SnapshotBlocked('capture_io_error') from exc


@contextmanager
def captured_store(root, staging_parent, *, max_bytes, max_seconds, max_files=2000, source_kind="marked"):
    """Existing import contract: no backup exclusions or caller-supplied filter."""
    with _captured_store(root, staging_parent, max_bytes=max_bytes, max_seconds=max_seconds,
                         max_files=max_files, source_kind=source_kind) as result:
        yield result


@contextmanager
def captured_backup(root, staging_parent, *, max_bytes, max_seconds, max_files=2000):
    """Temporary backup selection, freshly classified under lock; not published.

    Includes file protection for excluded files too. Caller must stop all workers.
    No cached plan, legacy bypass, arbitrary filter or final backup is accepted.
    """
    with _captured_store(root, staging_parent, max_bytes=max_bytes, max_seconds=max_seconds,
                         max_files=max_files, backup=True) as result:
        yield result


@contextmanager
def captured_backup_payload(root, staging_parent, *, max_bytes, max_seconds, max_files=2000):
    """Restore-only archive capture; coordination lock stays outside the archive."""
    root = Path(root)
    if root.name != 'payload' or not (root.parent / 'backup.json').is_file():
        raise SnapshotBlocked('backup_archive_required')
    with _captured_store(root, staging_parent, max_bytes=max_bytes, max_seconds=max_seconds,
                         max_files=max_files, backup=True, backup_payload=True) as result:
        yield result
