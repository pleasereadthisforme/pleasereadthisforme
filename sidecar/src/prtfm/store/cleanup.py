"""Explicit cleanup of disposable app data, never document or database trees."""
import os
import re
import stat

from prtfm.config import data_root
from prtfm.indexing.analysis_cache import AnalysisCache
from prtfm.store.summary import _entries, _link


def clear_analysis_cache():
    root = data_root()
    report = {'removed': 0, 'skipped': 0, 'supported': True}
    # Reject redirected directories and lock files before acquiring the cache lock.
    for path in (root, root / 'analysis-cache', root / 'analysis-cache/vision'):
        try:
            info = path.lstat()
        except FileNotFoundError:
            return report
        if _link(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError('unsafe analysis cache directory')
    lock = root / 'analysis-cache/vision/.lock'
    if lock.exists() or lock.is_symlink():
        info = lock.lstat()
        if _link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('unsafe analysis cache lock')
    cache = AnalysisCache()
    with cache.guard(), _entries(cache.root) as (entries, fd):
        for index, entry in enumerate(entries):
            if index >= 20000:
                report['skipped'] += 1
                break
            if entry.name == '.lock':
                continue
            info = entry.stat(follow_symlinks=False)
            if (not re.fullmatch(r'[0-9a-f]{64}\.json', entry.name)
                    or _link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
                report['skipped'] += 1
                continue
            try:
                if fd is not None:
                    os.unlink(entry.name, dir_fd=fd)
                else:
                    (cache.root / entry.name).unlink()
                report['removed'] += 1
            except OSError:
                report['skipped'] += 1
    return report
