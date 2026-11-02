from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from prtfm.indexing.scope import FileEntry
from prtfm.store.db import DocRow


@dataclass(frozen=True)
class Todo:
    entry: FileEntry
    doc_id: int | None
    prev_hash: str | None


@dataclass
class Diff:
    todo: list[Todo] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)
    unchanged: int = 0


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def diff(entries: list[FileEntry], docs: dict[str, DocRow]) -> Diff:
    """실패 파일은 다시 처리한다. 나머지는 메타와 해시로 변경 여부를 판단한다."""
    out = Diff()
    seen = set()
    for e in entries:
        seen.add(e.rel)
        d = docs.get(e.rel)
        if d is not None and (d.status == "error" or (d.status == "ok" and d.error is not None) or d.repair_required):
            # 실패 때 저장된 해시가 같아도 추출·청킹을 다시 시도해야 한다.
            out.todo.append(Todo(e, d.id, None))
        elif d is not None and d.size == e.size and d.mtime_ns == e.mtime_ns:
            out.unchanged += 1
        else:
            out.todo.append(Todo(e, d.id if d else None, d.content_hash if d else None))
    out.deleted = [d.id for rel, d in docs.items() if rel not in seen]
    return out
