"""PDF 읽기 순서에 표와 원문 위치를 결합한다."""
from __future__ import annotations

import re

from prtfm.indexing.blocks import Block

_CAPTION = re.compile(r'^\s*(?:[-*#]+\s*)?(?:Table\s+\d+[.:]|표\s*\d+[.:]|第[\d-]+表)', re.I)


def link_captions(blocks: list[Block]) -> None:
    """동일 페이지·열의 가까운 캡션 후보를 좌표로 연결. 의미상 소속을 확정하지 않는다."""
    for table in blocks:
        if table.kind != 'table' or not table.bbox:
            continue
        candidates = []
        x0, y0, x1, y1 = table.bbox
        for i, caption in enumerate(blocks):
            if not caption.bbox or caption.page != table.page or not _CAPTION.match(caption.text):
                continue
            # 표까지 포함된 list 컨테이너를 캡션으로 취급하지 않는다.
            if '|' in caption.text:
                continue
            a, b, c, d = caption.bbox
            width = min(x1 - x0, c - a)
            if width <= 0 or max(0, min(x1, c) - max(x0, a)) / width < 0.6:
                continue
            gap = b - y1 if b >= y1 else y0 - d if d <= y0 else -1
            if not 0 <= gap <= 36:
                continue
            candidates.append({'block_index': i, 'distance_points': gap,
                               'direction': 'after' if b >= y1 else 'before',
                               'basis': 'same_page_geometry', 'attachment': 'unverified'})
        table.caption_candidates = sorted(candidates, key=lambda c: c['distance_points'])
    # 파서가 동일 표를 여러 행 묶음으로 쪼갠 경우, 거의 맞닿은 동일 열 조각만 묶는다.
    groups = []
    for table in sorted((b for b in blocks if b.kind == 'table' and b.bbox),
                        key=lambda b: (b.page, b.bbox[1])):
        group = next((g for g in groups if g[-1].page == table.page
                      and abs(g[-1].bbox[0] - table.bbox[0]) <= 3
                      and abs(g[-1].bbox[2] - table.bbox[2]) <= 3
                      and 0 <= table.bbox[1] - g[-1].bbox[3] <= 4), None)
        if group is None:
            groups.append([table])
        else:
            group.append(table)
    for group in groups:
        if len(group) < 2:
            continue
        links = {c['block_index']: c for b in group for c in b.caption_candidates}
        for table in group:
            table.caption_candidates = [dict(c, basis='same_page_geometry_fragment_group')
                                        for c in links.values()]


def expand_caption_lists(elements):
    """캡션과 다음 표를 같은 list item으로 오인한 컨테이너의 원래 좌표를 복구한다."""
    for element in elements:
        items = element.get('list items') or []
        if element.get('type') == 'list' and items and all(
                _CAPTION.match(item.get('content', '')) for item in items):
            for item in items:
                yield {**item, 'type': 'caption', 'kids': []}
                yield from expand_caption_lists(item.get('kids') or [])
        else:
            yield element


def markdown_table(rows: list[list[str | None]], flatten: bool = True) -> str:
    if not rows:
        return ""
    width = max(map(len, rows))
    if not width:
        return ""
    def cell_text(cell):
        text = str(cell or "").replace("|", "\\|")
        return text.replace("\n", " ").strip() if flatten else text

    cleaned = [[cell_text(cell) for cell in row]
               + [""] * (width - len(row)) for row in rows]
    cleaned.insert(1, ["---"] * width)
    return "\n".join("|" + "|".join(row) + "|" for row in cleaned)


def serialize(element: dict) -> str:
    kind = element.get("type")
    if kind in ("image", "picture", "figure"):
        return ""
    if kind == "heading":
        return "#" * min(6, max(1, int(element.get("heading level") or 1))) + " " + element.get("content", "").strip()
    if kind == "list":
        lines = []
        for item in element.get("list items") or []:
            lines.append("- " + item.get("content", "").strip())
            for child in item.get("kids") or []:
                value = serialize(child)
                if value:
                    lines.append("\n".join("  " + line for line in value.splitlines()))
        return "\n".join(lines)
    if kind == "table":
        rows = element.get("rows") or []
        if not rows:
            return ""
        width = element.get("number of columns") or max(len(r.get("cells", [])) for r in rows)
        grid = []
        for row in rows:
            cells = [""] * width
            for cell in row.get("cells", []):
                col = (cell.get("column number") or 1) - 1
                if 0 <= col < width:
                    cells[col] = "<br>".join(v for child in cell.get("kids") or []
                                            if (v := serialize(child).strip()))
            grid.append(cells)
        return markdown_table(grid, flatten=False)
    if element.get("content"):
        return str(element["content"]).strip()
    return "\n\n".join(value for child in element.get("kids") or [] if (value := serialize(child)))


def eligible(rows) -> bool:
    cells = [cell for row in rows for cell in row]
    return (len(rows) >= 2 and max(map(len, rows), default=0) >= 2 and bool(cells)
            and sum(bool(c and str(c).strip()) for c in cells) / len(cells) >= 0.5)


def merge(elements: list[dict], tables: list, height: float, page: int,
          *, geometry_verified: bool = True) -> list[Block]:
    targets = [(list(box), rows) for box, rows in tables if geometry_verified and eligible(rows)]
    used, blocks = set(), []
    for element in expand_caption_lists(elements):
        raw = element.get("bounding box")
        box = [raw[0], height - raw[3], raw[2], height - raw[1]] if raw and geometry_verified else None
        hit = None
        if box:
            area = max(0, box[2] - box[0]) * max(0, box[3] - box[1])
            for i, (table_box, _) in enumerate(targets):
                overlap = (max(0, min(box[2], table_box[2]) - max(box[0], table_box[0]))
                           * max(0, min(box[3], table_box[3]) - max(box[1], table_box[1])))
                if area and overlap / area >= 0.5:
                    hit = i
                    break
        if hit is not None:
            if hit not in used:
                table_box, rows = targets[hit]
                blocks.append(Block(markdown_table(rows), page, "pdfplumber", table_box, "table"))
                used.add(hit)
        elif text := serialize(element):
            blocks.append(Block(text, page, "odl", box, element.get("type", "text")))
    for i, (box, rows) in enumerate(targets):
        if i not in used:
            blocks.append(Block(markdown_table(rows), page, "pdfplumber", box, "table"))
    link_captions(blocks)
    return blocks
