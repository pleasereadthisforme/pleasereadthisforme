"""정규화된 블록의 원문 위치를 청크에 전달한다."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from prtfm.indexing.chunker import chunk_doc
from prtfm.indexing.textnorm import normalize
from prtfm.indexing.tokenize import tokenize_doc
from prtfm.store.db import ChunkRow


@dataclass
class Block:
    text: str
    page: int
    source: str
    bbox: list[float] | None = None
    kind: str = "text"
    caption_candidates: list[dict] = field(default_factory=list)


def build_document(blocks: list[Block], *, create_chunks: bool = True) -> tuple[str, list[int], list[ChunkRow], list[dict]]:
    parts, located, starts = [], [], {}
    pos = 0
    trail = []
    for block in blocks:
        body = normalize(block.text).strip()
        heading = re.match(r"^(#{1,6})\s+([^\n]*)", body)
        if heading:
            trail = trail[:len(heading[1]) - 1] + [heading[2].strip()]
        starts.setdefault(block.page, pos)
        located.append({**asdict(block), "block_id": len(located), "text": body,
                        "start": pos, "end": pos + len(body),
                        "heading_trail": list(trail)})
        parts.append(body)
        pos += len(body) + 2
    text = "\n\n".join(parts)
    if not create_chunks:
        return text, [starts[p] for p in sorted(starts)], [], located
    rows, cursor = [], 0
    for idx, chunk in enumerate(chunk_doc(text)):
        begin = None
        for char in chunk.text:
            if char.isspace():
                continue
            while cursor < len(text) and text[cursor].isspace():
                cursor += 1
            if cursor >= len(text) or text[cursor] != char:
                raise ValueError("chunk source order mismatch")
            if begin is None:
                begin = cursor
            cursor += 1
        begin = cursor if begin is None else begin
        spans = [{"page": b["page"], "bbox": b["bbox"], "source": b["source"], "block_id": b['block_id'],
                  "kind": b["kind"], "start": max(begin, b["start"]), "end": min(cursor, b["end"])}
                 for b in located if b["start"] < cursor and b["end"] > begin]
        pages = [s["page"] for s in spans]
        rows.append(ChunkRow(idx, min(pages) if pages else None, chunk.heading_trail, chunk.text,
                             " ".join(tokenize_doc(chunk.text)), max(pages) if pages else None, spans))
    # merge가 생성한 페이지 내 블록 인덱스를 실제 문서 청크 범위로 변환한다.
    page_blocks = {}
    block_chunks = {}
    for b in located:
        page_blocks.setdefault(b['page'], []).append(b)
    for row in rows:
        for span in row.spans:
            block_chunks.setdefault(span['block_id'], []).append(row.idx)
    for row in rows:
        for span in row.spans:
            b = located[span['block_id']]
            links = []
            for candidate in b['caption_candidates']:
                caption = page_blocks[b['page']][candidate['block_index']]
                indices = block_chunks.get(caption['block_id'], [])
                links.append({k: v for k, v in candidate.items() if k != 'block_index'} |
                             {'caption_chunk_indices': indices, 'caption_bbox': caption['bbox'],
                              'caption_start': caption['start'], 'caption_end': caption['end']})
            if links:
                span['table_caption_candidates'] = links
    return text, [starts[p] for p in sorted(starts)], rows, located
