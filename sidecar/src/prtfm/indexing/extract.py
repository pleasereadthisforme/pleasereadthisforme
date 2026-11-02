from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from pathlib import Path

from prtfm.indexing.textnorm import normalize

DOC_EXTS = frozenset({".pdf", ".docx", ".pptx", ".md", ".markdown", ".txt", ".rst"})
CODE_EXTS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".rb", ".php", ".swift", ".sql", ".sh", ".ps1", ".json", ".yaml", ".yml", ".toml", ".xml", ".html",
    ".css", ".ini", ".cfg",
})
SUPPORTED = DOC_EXTS | CODE_EXTS
IMAGE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"})
VLM_EXTS = IMAGE_EXTS | {".pdf"}
ENCODINGS = ("utf-8-sig", "cp949", "cp932")
NUL_RATIO_LIMIT = 100  # 글자 100개당 NUL 1개를 넘으면 잘못된 인코딩으로 본다
# 긴 BOM을 먼저 본다(UTF-32LE BOM은 UTF-16LE BOM으로 시작한다)
_BOMS = ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"),
         (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16"))


class ExtractError(Exception):
    pass


class TooManyPages(ExtractError):
    pass


@dataclass(frozen=True)
class Extracted:
    text: str
    page_starts: list[int]
    pages: int
    is_code: bool = False


def _join_pages(page_texts: list[str]) -> Extracted:
    """페이지마다 정규화한 뒤 이어 붙여, 정규화 후 기준의 페이지 시작 위치를 기록한다."""
    parts, starts, pos = [], [], 0
    for t in page_texts:
        t = normalize(t).strip()
        starts.append(pos)
        parts.append(t)
        pos += len(t) + 2
    return Extracted("\n\n".join(parts), starts, len(page_texts))


def _pdf(path: Path, max_pages: int) -> Extracted:
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        raise ExtractError(f"cannot open pdf: {e}") from e
    try:
        n = len(doc)
        if n > max_pages:
            raise TooManyPages(f"{n} pages > {max_pages}")
        texts = []
        for i in range(n):
            page = doc[i]
            texts.append(page.get_textpage().get_text_bounded())
            page.close()
    except pdfium.PdfiumError as e:  # 페이지 트리가 손상되면 열린 뒤에도 실패한다
        raise ExtractError(f"cannot read pdf: {e}") from e
    finally:
        doc.close()
    return _join_pages(texts)


def _docx(path: Path) -> Extracted:
    from docx import Document

    try:
        d = Document(str(path))
    except Exception as e:  # python-docx는 손상 파일에 여러 예외를 낸다
        raise ExtractError(f"cannot open docx: {e}") from e
    try:
        return _join_pages(["\n\n".join(_docx_blocks(d))])
    except Exception as e:  # 열린 뒤 구조가 깨진 문서도 이 파일만 실패로 처리
        raise ExtractError(f"cannot read docx: {e}") from e


def _docx_blocks(d) -> list[str]:
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    blocks: list[str] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, d)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name if para.style is not None else "") or ""
            if style.startswith("Heading ") and style[8:].isdigit():
                text = "#" * min(int(style[8:]), 6) + " " + text
            elif style == "Title":
                text = "# " + text
            blocks.append(text)
        elif tag == "tbl":
            rows = [" | ".join(c.text.strip() for c in r.cells) for r in Table(child, d).rows]
            blocks.append("\n".join(rows))
    return blocks


def _pptx(path: Path, max_pages: int) -> Extracted:
    from pptx import Presentation

    try:
        prs = Presentation(str(path))
    except Exception as e:
        raise ExtractError(f"cannot open pptx: {e}") from e
    try:
        slides = list(prs.slides)
    except Exception as e:
        raise ExtractError(f"cannot read pptx: {e}") from e
    if len(slides) > max_pages:
        raise TooManyPages(f"{len(slides)} slides > {max_pages}")
    try:
        return _join_pages([_slide_text(s) for s in slides])
    except Exception as e:  # 열린 뒤 구조가 깨진 슬라이드도 이 파일만 실패로 처리
        raise ExtractError(f"cannot read pptx: {e}") from e


def _slide_text(slide) -> str:
    title = slide.shapes.title
    title_id = title.shape_id if title is not None else None
    blocks = []
    if title is not None and title.text_frame.text.strip():
        blocks.append("# " + title.text_frame.text.strip())
    for sh in slide.shapes:
        if sh.shape_id == title_id:
            continue
        if sh.has_text_frame and sh.text_frame.text.strip():
            blocks.append(sh.text_frame.text.strip())
        elif getattr(sh, "has_table", False) and sh.has_table:
            blocks.append("\n".join(" | ".join(c.text.strip() for c in r.cells) for r in sh.table.rows))
    return "\n\n".join(blocks)


def _try_decode(raw: bytes, enc: str) -> str | None:
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError:
        return None
    # BOM 없는 UTF-16을 잘못 읽으면 ASCII 글자마다 NUL이 생긴다. 추출 도구가 남긴 드문 NUL은 허용한다
    return None if text.count("\x00") * NUL_RATIO_LIMIT > len(text) else text


_FULL_KANA = re.compile("[\u3040-\u30ff]")
_HALF_KANA = re.compile("[\uff61-\uff9f]")


def _looks_japanese(text: str) -> bool:
    """전각 가나가 있고 반각 가나가 없으면 일본어로 본다.

    cp949는 Shift-JIS 바이트열 대부분을 (엉뚱한) 한글로 받아들이고,
    거꾸로 cp949 한국어를 cp932로 읽으면 반각 가나(U+FF61–U+FF9F)가 나온다.
    """
    return _FULL_KANA.search(text) is not None and _HALF_KANA.search(text) is None


def _decode(raw: bytes) -> str:
    for bom, enc in _BOMS:
        if raw.startswith(bom):
            text = _try_decode(raw, enc)
            if text is None:
                raise ExtractError(f"invalid {enc} text")
            return text
    utf8, korean, japanese = ENCODINGS
    text = _try_decode(raw, utf8)
    if text is not None:
        return text
    ko, ja = _try_decode(raw, korean), _try_decode(raw, japanese)
    if ja is not None and (ko is None or _looks_japanese(ja)):
        return ja
    if ko is not None:
        return ko
    raise ExtractError(f"unknown text encoding (tried {', '.join(ENCODINGS)})")


def extract_text(raw: bytes, is_code: bool = False) -> Extracted:
    ex = _join_pages([_decode(raw)])
    return Extracted(ex.text, ex.page_starts, ex.pages, is_code)


def extract(path: Path, max_pages: int) -> Extracted:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf(path, max_pages)
    if ext == ".docx":
        return _docx(path)
    if ext == ".pptx":
        return _pptx(path, max_pages)
    if ext in DOC_EXTS:
        return extract_text(path.read_bytes(), is_code=False)
    if ext in CODE_EXTS:
        return extract_text(path.read_bytes(), is_code=True)
    raise ExtractError(f"unsupported extension: {ext}")
