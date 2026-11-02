"""Shared processing catalogue; bodies are hard-linked, never copied.

Each workspace owns a link. Removing the last workspace link frees the body;
there is no extra catalogue-owned body requiring a second retention policy.
Lock order: result key -> workspace cache generation guard.
"""
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from prtfm.config import data_root, workspace_dir
from prtfm.store.locking import WriterBusy, acquire


def identity(source, digest, recipe):
    return hashlib.sha256(json.dumps([str(Path(source).resolve()), digest, recipe],
                                    sort_keys=True).encode()).hexdigest()


class SharedResult:
    def __init__(self, source, digest, recipe):
        self.source, self.digest, self.recipe = Path(source).resolve(), digest, recipe
        self.key = identity(source, digest, recipe)
        self.root = data_root() / 'shared-results'
        self.record = self.root / (self.key + '.json')

    @contextmanager
    def lock(self, cancel=None):
        self.root.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        while True:
            if cancel is not None and cancel.is_set():
                raise ValueError('cancelled')
            try:
                handle = acquire(self.root / (self.key + '.lock'))
                break
            except WriterBusy:
                if time.monotonic() - started > 1800:
                    raise TimeoutError('shared result busy') from None
                time.sleep(.05)
        try:
            yield
        finally:
            handle.close()

    def _paths(self):
        try:
            return json.loads(self.record.read_text())
        except (OSError, ValueError):
            return []

    def _save(self, paths):
        temporary = self.record.with_suffix('.tmp')
        temporary.write_text(json.dumps(paths))
        temporary.replace(self.record)

    def register(self, base):
        paths = [p for p in self._paths() if Path(p).joinpath('document.md').is_file()]
        path = str(base.resolve())
        if path not in paths:
            paths.append(path)
        self._save(paths)

    def _read(self, base):
        meta = json.loads((base / 'metadata.json').read_text())
        raw = (base / 'document.md').read_bytes()
        if hashlib.sha256(raw).hexdigest() != meta['sha256']:
            return None
        text = raw.decode('utf-8')
        if 'blocks' in meta:
            from prtfm.store.block_metadata import restore_blocks
            restore_blocks(text, meta)
        return {'base': base, 'text': text, 'starts': meta['page_starts'], 'meta': meta}

    def find(self):
        for path in self._paths():
            base = Path(path)
            try:
                if not base.joinpath('document.md').is_file():
                    # Chunking advances a workspace revision without changing its body.
                    from prtfm.store.db import Store
                    store = Store.open_readonly(base.parent.parent.parent / 'index.db')
                    try:
                        revision = store.revision(int(base.parent.name))
                        base = base.parent / str(revision)
                    finally:
                        store.close()
                if not base.resolve().is_relative_to(data_root().resolve()):
                    continue
                result = self._read(base)
                if result and result['meta'].get('shared_key') == self.key:
                    return result
            except (OSError, ValueError, KeyError, TypeError):
                continue
        # Bootstrap already paid VLM results from an overlapping ancestor root.
        # Only the exact same canonical source/hash and known vision version qualify.
        if self.recipe.get('kind') == 'vision':
            from prtfm.store.db import Store
            for parent in self.source.parents:
                directory = workspace_dir(parent)
                if not (directory / 'index.db').is_file():
                    continue
                store = Store.open_readonly(directory / 'index.db')
                try:
                    doc = store.docs().get(str(self.source.relative_to(parent)))
                    if not doc or doc.status != 'ok' or doc.error is not None or doc.repair_required or doc.content_hash != self.digest:
                        continue
                    base = directory / 'cache' / str(doc.id) / str(doc.revision)
                    result = self._read(base)
                    if (result and result['meta'].get('vision_version') == self.recipe['version']
                            and result['meta'].get('shared_key', self.key) == self.key):
                        return result
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                finally:
                    store.close()
        return None

    def metadata(self, metadata=None):
        return {**(metadata or {}), 'shared_key': self.key}


def prune_catalogue():
    """Drop dead pointers only; never delete a body or unlink lock files."""
    root = data_root() / 'shared-results'
    if not root.exists():
        return
    for record in root.glob('*.json'):
        try:
            handle = acquire(record.with_suffix('.lock'))
        except WriterBusy:
            continue
        try:
            paths = json.loads(record.read_text())
            live = []
            for path in paths:
                base = Path(path)
                if not base.resolve().is_relative_to(data_root().resolve()):
                    continue
                if not base.joinpath('document.md').is_file():
                    from prtfm.store.db import Store
                    try:
                        store = Store.open_readonly(base.parent.parent.parent / 'index.db')
                        try:
                            base = base.parent / str(store.revision(int(base.parent.name)))
                        finally:
                            store.close()
                    except (OSError, ValueError, sqlite3.Error):
                        continue
                try:
                    meta = json.loads((base / 'metadata.json').read_text())
                    if meta.get('shared_key') == record.stem and (base / 'document.md').is_file():
                        live.append(str(base))
                except (OSError, ValueError):
                    continue
            live = list(dict.fromkeys(live))
            if not live:
                record.unlink(missing_ok=True)
            elif live != paths:
                temporary = record.with_suffix('.tmp')
                temporary.write_text(json.dumps(live))
                temporary.replace(record)
        except (OSError, ValueError, TypeError):
            continue
        finally:
            handle.close()
