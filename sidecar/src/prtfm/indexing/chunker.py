from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

MIN_CHARS, MAX_CHARS = 800, 1200
_HEADING = re.compile(r"^(#{1,6})\s+(.*)")
_SENT = re.compile(r"(?<=[.!?。！？])\s+|(?<=다\.)\s*")


@dataclass(frozen=True)
class Chunk:
    heading_trail: list[str] = field(default_factory=list)
    text: str = ""


def _split_long(block: str) -> list[str]:
    parts, cur = [], ""
    for sent in _SENT.split(block):
        if cur and len(sent) > MAX_CHARS:
            # 앞 문장이 긴 문장의 조각 뒤로 밀리지 않게 먼저 내보낸다.
            parts.append(cur)
            cur = ""
        while len(sent) > MAX_CHARS:
            parts.append(sent[:MAX_CHARS])
            sent = sent[MAX_CHARS:]
        if cur and len(cur) + len(sent) + 1 > MAX_CHARS:
            parts.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        parts.append(cur)
    return parts


def chunk_doc(text: str, headings: bool = True) -> list[Chunk]:
    """빈 줄로 나눈 블록을 쌓아 800자 이상이면 끊고 1200자는 넘기지 않는다. 헤딩에서 새 청크를 시작한다."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    trail: list[str] = []
    out: list[Chunk] = []
    cur, cur_trail = "", []

    def flush() -> None:
        nonlocal cur
        if cur.strip():
            out.append(Chunk(list(cur_trail), cur.strip()))
        cur = ""

    for b in blocks:
        m = _HEADING.match(b) if headings else None
        if m:
            trail = trail[: len(m.group(1)) - 1] + [m.group(2).strip()]
            if len(cur) >= MIN_CHARS:
                flush()
            if not cur:
                cur_trail = list(trail)
        for piece in _split_long(b) if len(b) > MAX_CHARS else [b]:
            if cur and len(cur) + len(piece) + 2 > MAX_CHARS:
                flush()
                cur_trail = list(trail)
            cur = f"{cur}\n\n{piece}" if cur else piece
            if len(cur) >= MIN_CHARS:
                flush()
                cur_trail = list(trail)
    flush()
    return out


def assign_pages(text: str, chunks: list[Chunk], page_starts: list[int]) -> list[int]:
    """청크 첫 줄이 원문에서 처음 나오는 위치로 시작 페이지를 정한다. 못 찾으면 직전 청크의 페이지."""
    pages, cursor, last = [], 0, 1
    for c in chunks:
        probe = c.text.split("\n", 1)[0][:40]
        pos = text.find(probe, cursor) if probe else -1
        if pos >= 0:
            cursor = pos + 1
            last = max(1, bisect.bisect_right(page_starts, pos))
        pages.append(last)
    return pages
