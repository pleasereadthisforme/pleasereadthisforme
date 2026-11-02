"""Disposable page classification only; never owns document results or cost records."""
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager

from prtfm.config import data_root
from prtfm.store.locking import WriterBusy, acquire

MAX_BYTES = 128 * 1024 * 1024
MAX_AGE = 30 * 86400


class AnalysisCache:
    def __init__(self):
        self.root = data_root() / 'analysis-cache' / 'vision'
        self.root.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def guard(self):
        deadline = time.monotonic() + 5
        while True:
            try:
                handle = acquire(self.root / '.lock')
                break
            except WriterBusy:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.02)
        try:
            yield
        finally:
            handle.close()

    def path(self, source):
        key = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
        return self.root / (key + '.json')

    def get(self, source, digest, version):
        with self.guard():
            path = self.path(source)
            try:
                if path.stat().st_mtime < time.time() - MAX_AGE:
                    path.unlink()
                    return None
                value = json.loads(path.read_text())
                if value['hash'] != digest or value['version'] != version:
                    return None
                pages = value['pages']
                if not isinstance(pages, list) or not all(
                    isinstance(p, dict) and type(p.get('vision')) is bool and isinstance(p.get('reasons'), list)
                    for p in pages
                ):
                    return None
                os.utime(path, None)
                return pages
            except (OSError, ValueError, KeyError, TypeError):
                return None

    def put(self, source, digest, version, pages):
        # No extracted text, prices, rendered images, or execution records.
        payload = {'hash': digest, 'version': version,
                   'pages': [{'vision': p['vision'], 'reasons': p['reasons']} for p in pages]}
        with self.guard():
            path = self.path(source)
            temporary = self.root / (uuid.uuid4().hex + '.tmp')
            try:
                temporary.write_text(json.dumps(payload), encoding='utf-8')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            self._prune()

    def prune(self):
        with self.guard():
            self._prune()

    def _prune(self):
        entries = []
        for path in self.root.glob('*.json'):
            stat = path.stat()
            if stat.st_mtime < time.time() - MAX_AGE:
                path.unlink()
            else:
                entries.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _, size, _ in entries)
        for _, size, path in sorted(entries):
            if total <= MAX_BYTES:
                break
            path.unlink()
            total -= size
