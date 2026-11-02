from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from prtfm.store.locking import WriterBusy, acquire

_REPLACE_TRIES = 5


def _atomic_write(path: Path, text: str) -> None:
    """임시 파일에 쓴 뒤 교체한다. Windows에서 읽는 쪽이 파일을 잡고 있으면 잠시 뒤 재시도한다."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(_REPLACE_TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == _REPLACE_TRIES - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05 * (attempt + 1))


class TextCache:
    """문서별 추출 텍스트(.md)와 페이지 시작 위치(.json). 인용 원문 표시·grep·2차 패스 교체에 쓴다."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.store = None

    def bind(self, store) -> None:
        self.store = store

    @contextmanager
    def _guard(self):
        deadline = time.monotonic() + 10
        while True:
            try:
                handle = acquire(self.root / '.generations.lock')
                break
            except WriterBusy:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
        try:
            yield
        finally:
            handle.close()

    def _leases(self):
        protected = set()
        directory = self.root / '.leases'
        directory.mkdir(exist_ok=True)
        for path in directory.glob('*.json'):
            lock_path = path.with_suffix('.lock')
            try:
                handle = acquire(lock_path)
            except WriterBusy:
                protected.update(tuple(pair) for pair in json.loads(path.read_text()))
            else:
                handle.close()
                path.unlink()
                lock_path.unlink(missing_ok=True)
        return protected

    @contextmanager
    def pin(self, *, metadata_only=False):
        """Capture active generations; OS locks retain them until exit or process death."""
        if self.store is None:
            raise ValueError('bound store required')
        handle = None
        path = None
        try:
            with self._guard():
                refs = {d.id: d.revision for d in self.store.docs().values() if d.status == 'ok'}
                for doc_id, revision in refs.items():
                    valid = (self.direct_revision(doc_id, revision) or
                             self.metadata_revision(doc_id, revision) is not None) if metadata_only else self.valid_revision(doc_id, revision)
                    if not valid:
                        raise ValueError('snapshot generation missing')
                directory = self.root / '.leases'
                directory.mkdir(exist_ok=True)
                path = directory / (uuid.uuid4().hex + '.json')
                path.write_text(json.dumps(list(refs.items())))
                handle = acquire(path.with_suffix('.lock'))
            yield refs
        finally:
            if handle is not None:
                with self._guard():
                    handle.close()
                    path.unlink(missing_ok=True)
                    path.with_suffix('.lock').unlink(missing_ok=True)

    def prepare(self, doc_id: int, revision: int, text: str, page_starts: list[int],
                metadata: dict | None = None) -> None:
        with self._guard():
            if (doc_id, revision) in self._leases():
                raise ValueError('cannot replace pinned generation')
            self._prepare(doc_id, revision, text, page_starts, metadata)

    def direct_revision(self, doc_id, revision):
        path = self.root / str(doc_id) / str(revision) / 'metadata.json'
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        if not isinstance(meta, dict):
            raise ValueError('invalid source metadata')
        if meta.get('source_kind') != 'original_text':
            return False
        digest = meta.get('source_hash')
        if (meta.get('source_version') != 1 or not isinstance(digest, str) or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest)
                or type(meta.get('create_chunks')) is not bool):
            raise ValueError('invalid original source metadata')
        if self.store is not None:
            doc = self.store.con.execute('SELECT revision,content_hash FROM docs WHERE id=?', (doc_id,)).fetchone()
            if doc is None or doc[0] != revision or doc[1] != digest:
                raise ValueError('original source metadata mismatch')
        return True

    def metadata_revision(self, doc_id, revision):
        """Describe a stored generation without reading its body; not a checksum check."""
        if any(type(v) is not int or v < 1 for v in (doc_id, revision)):
            raise ValueError('invalid generation')
        base = self.root / str(doc_id) / str(revision)
        if not base.exists() or self.direct_revision(doc_id, revision):
            return None
        meta = json.loads((base / 'metadata.json').read_text(encoding='utf-8'))
        digest, starts = meta.get('sha256'), meta.get('page_starts')
        if (not isinstance(digest, str) or len(digest) != 64
                or any(c not in '0123456789abcdef' for c in digest)
                or not isinstance(starts, list)
                or any(type(v) is not int or v < 0 for v in starts)
                or starts != sorted(starts)):
            raise ValueError('invalid generation metadata')
        body = base / 'document.md'
        if not body.is_file():
            return None
        return {'sha256': digest, 'page_starts': starts, 'utf8_bytes': body.stat().st_size}

    def valid_revision(self, doc_id, revision):
        return self.direct_revision(doc_id, revision) or self.read_revision(doc_id, revision) is not None

    def prepare_original(self, doc_id, revision, digest, create_chunks, *, pipeline_version=None):
        self.prepare(doc_id, revision, None, [], {'source_kind': 'original_text', 'source_version': 1,
                                                'source_hash': digest, 'create_chunks': create_chunks,
                                                **({'pipeline_version': pipeline_version} if pipeline_version is not None else {})})

    def _prepare(self, doc_id, revision, text, page_starts, metadata=None):
        parent = self.root / str(doc_id)
        parent.mkdir(exist_ok=True)
        dest = parent / str(revision)
        if dest.exists():
            raise ValueError('generation already exists; recover before retry')
        tmp = Path(tempfile.mkdtemp(prefix=".prepare-", dir=parent))
        try:
            metadata = dict(metadata or {})
            shared_body = metadata.pop("_shared_body", None)
            meta = {**metadata, "page_starts": page_starts,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None}
            if 'blocks' in meta:
                from prtfm.store.block_metadata import compact_blocks

                meta = compact_blocks(text, meta)
            files = [("metadata.json", json.dumps(meta))]
            if shared_body is not None:
                os.link(shared_body, tmp / "document.md")
                if hashlib.sha256((tmp / "document.md").read_bytes()).hexdigest() != meta["sha256"]:
                    raise ValueError("shared body mismatch")
            elif text is not None:
                files.insert(0, ("document.md", text))
            for name, body in files:
                with (tmp / name).open("w", encoding="utf-8") as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(tmp, dest)
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)

    def recover(self) -> None:
        """Keep active and pinned generations; reclaim leases released by dead processes."""
        with self._guard():
            self._recover(self._leases())

    def _recover(self, protected):
        if self.store is None:
            return
        docs = self.store.docs()
        active = {str(d.id): d for d in docs.values() if d.status == "ok"}
        for d in active.values():
            target = self.root / str(d.id) / str(d.revision)
            try:
                if not target.exists():
                    legacy = self._legacy_read(d.id)
                    if legacy:
                        self._prepare(d.id, d.revision, *legacy)
                valid = self.valid_revision(d.id, d.revision)
            except (OSError, ValueError, KeyError):
                valid = False
            with self.store.con:
                self.store.con.execute("UPDATE docs SET repair_required=? WHERE id=?", (not valid, d.id))
        for child in self.root.iterdir():
            if child.name.startswith('.'):
                continue
            try:
                if child.is_dir():
                    d = active.get(child.name)
                    for generation in child.iterdir():
                        if child.name.isdigit() and generation.name.isdigit() and (
                                int(child.name), int(generation.name)) in protected:
                            continue
                        if d is None or generation.name != str(d.revision):
                            if generation.is_dir():
                                shutil.rmtree(generation)
                            else:
                                generation.unlink()
                elif child.suffix in (".md", ".json") and child.stem.isdigit():
                    d = active.get(child.stem)
                    if d is None or self.read(d.id) is not None:
                        child.unlink()
            except (OSError, ValueError, KeyError):
                # 잠긴 과거 파일은 다음 writer 복구에서 다시 정리한다.
                continue

    def write(self, doc_id: int, text: str, page_starts: list[int]) -> None:
        if self.store is not None:
            self.prepare(doc_id, self.store.revision(doc_id), text, page_starts, {'create_chunks': True})
            return
        _atomic_write(self.root / f"{doc_id}.md", text)
        _atomic_write(self.root / f"{doc_id}.json", json.dumps({"page_starts": page_starts}))

    def read(self, doc_id: int) -> tuple[str, list[int]] | None:
        if self.store is not None:
            revision = self.store.revision(doc_id)
            return self.read_revision(doc_id, revision) if revision else None
        return self._legacy_read(doc_id)

    def read_revision(self, doc_id: int, revision: int) -> tuple[str, list[int]] | None:
        if any(type(v) is not int or v < 1 for v in (doc_id, revision)):
            raise ValueError('invalid generation')
        base = self.root / str(doc_id) / str(revision)
        if not base.exists():
            return None
        if self.direct_revision(doc_id, revision):
            return None
        text = (base / 'document.md').read_text(encoding='utf-8')
        meta = json.loads((base / 'metadata.json').read_text(encoding='utf-8'))
        if hashlib.sha256(text.encode('utf-8')).hexdigest() != meta['sha256']:
            raise ValueError('cache checksum mismatch')
        return text, meta['page_starts']

    def _legacy_read(self, doc_id: int) -> tuple[str, list[int]] | None:
        md, meta = self.root / f"{doc_id}.md", self.root / f"{doc_id}.json"
        if not md.exists() or not meta.exists():
            return None
        return md.read_text(encoding="utf-8"), json.loads(meta.read_text(encoding="utf-8"))["page_starts"]

    def delete(self, doc_id: int) -> None:
        # 활성 세대 삭제는 DB 커밋 후 recover에서 한다. 트랜잭션 실패 시 이전 파일을 보존한다.
        if self.store is not None:
            return
        for suffix in (".md", ".json"):
            (self.root / f"{doc_id}{suffix}").unlink(missing_ok=True)
