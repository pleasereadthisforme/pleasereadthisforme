from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from prtfm.config import workspace_dir
from prtfm.store.cache import TextCache
from prtfm.store.db import Store
from prtfm.store.locking import acquire


@dataclass
class Workspace:
    dir: Path
    store: Store
    cache: TextCache
    writer_lock: object = None

    def close(self) -> None:
        try:
            self.store.close()
        finally:
            if self.writer_lock:
                self.writer_lock.close()


def open_workspace(scope_root: Path, readonly: bool = False) -> Workspace:
    d = workspace_dir(scope_root)
    if readonly:
        store, cache = Store.open_readonly(d / "index.db"), TextCache(d / "cache")
        store.source_root = scope_root
        cache.bind(store)
        return Workspace(d, store, cache)
    d.mkdir(parents=True, exist_ok=True)
    lock = acquire(d / "writer.lock")
    store = None
    try:
        store, cache = Store(d / "index.db"), TextCache(d / "cache")
        store.source_root = scope_root
        cache.bind(store)
        cache.recover()
        if (d / 'vectors').exists():
            from prtfm.indexing.embedding import recover
            from prtfm.store.vectors import Vectors

            try:
                recover(store, Vectors(d / 'vectors'))
                from prtfm.store.shared_vectors import promote_legacy
                promote_legacy(store, d / 'vectors')
            except Exception as exc:
                import logging
                logging.getLogger('prtfm.semantic').warning('vector recovery failed: %s', type(exc).__name__)
        return Workspace(d, store, cache, lock)
    except BaseException:
        if store:
            store.close()
        lock.close()
        raise
