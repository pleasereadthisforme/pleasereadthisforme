"""문서 한 건의 정밀 파싱. 별도 worker 프로세스 안에서 실행한다."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from prtfm.credentials.process_env import child_environment
from prtfm.indexing.blocks import Block, build_document
from prtfm.indexing.table_merge import merge


def runtime_dir() -> Path:
    return Path(os.environ.get("PRTFM_RUNTIME_DIR", Path(__file__).resolve().parents[4] / "runtime/build"))


def validate_runtime(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text())
    for filename, key in (("parser.jar", "jar_sha256"), ("app-cds.jsa", "cds_sha256")):
        if hashlib.sha256((root / filename).read_bytes()).hexdigest() != manifest[key]:
            raise ValueError("runtime fingerprint mismatch")


def odl(pdf: Path, output: Path, timeout: int, runtime: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    java = runtime / "jre/bin" / ("java.exe" if os.name == "nt" else "java")
    command = [str(java), "-Djava.awt.headless=true", "-Xmx1536m",
               f"-XX:SharedArchiveFile={runtime / 'app-cds.jsa'}", "-jar", str(runtime / "parser.jar"),
               str(pdf), "--output-dir", str(output), "--format", "json", "--image-output", "off", "--quiet"]
    subprocess.run(command, env=child_environment(), check=True, timeout=timeout, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files = list(output.glob("*.json"))
    if len(files) != 1:
        raise ValueError("missing parser output")
    data = json.loads(files[0].read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("kids"), list):
        raise ValueError("invalid parser output")
    return data


def remap(element, pages):
    if isinstance(element, dict):
        result = {}
        for key, value in element.items():
            if key == "page number" and value is not None:
                if not isinstance(value, int) or not 1 <= value <= len(pages):
                    raise ValueError("invalid page number")
                result[key] = pages[value - 1]
            else:
                result[key] = remap(value, pages)
        return result
    if isinstance(element, list):
        return [remap(value, pages) for value in element]
    return element


def parse(pdf: Path, work: Path, runtime: Path, *, create_chunks: bool = True) -> dict:
    import pdfplumber
    import pypdfium2 as pdfium
    from pypdf import PdfReader, PdfWriter

    validate_runtime(runtime)
    # ODL can lose rows on pages with display rotation. Parse a copy in the
    # original content coordinate system; retain the input for fallback and
    # page provenance. Coordinates from this copy must never reach the UI.
    source_reader = PdfReader(str(pdf))
    parser_pdf = pdf
    if any(p.rotation for p in source_reader.pages):
        writer = PdfWriter()
        for source_page in source_reader.pages:
            copied = writer.add_page(source_page)
            copied.rotation = 0
        parser_pdf = work / 'rotation-normalized.pdf'
        writer.write(str(parser_pdf))
    elements, fallback, warnings = {}, set(), []
    attempts = 0
    with pdfium.PdfDocument(str(pdf)) as document:
        count = len(document)
        pending = [list(range(1, count + 1))]
        reader = None
        while pending:
            pages = pending.pop()
            if not pages:
                continue
            attempts += 1
            candidate = parser_pdf
            if len(pages) != count:
                if reader is None:
                    reader = PdfReader(str(parser_pdf))
                writer = PdfWriter()
                for page in pages:
                    writer.add_page(reader.pages[page - 1])
                candidate = work / f"segment-{attempts}.pdf"
                writer.write(str(candidate))
            try:
                result = odl(candidate, work / f"odl-{attempts}", 30 if len(pages) == 1 else 300, runtime)
                mapped = remap(result["kids"], pages)
                staged = {}
                for el in mapped:
                    page = el.get("page number")
                    if page not in pages:
                        raise ValueError("missing page number")
                    staged.setdefault(page, []).append(el)
                elements.update(staged)
            except (subprocess.SubprocessError, OSError, ValueError, TypeError):
                if len(pages) == 1:
                    fallback.add(pages[0])
                else:
                    mid = len(pages) // 2
                    pending.extend([pages[mid:], pages[:mid]])

        blocks, empty_pages = [], []
        with pdfplumber.open(str(pdf)) as plumber:
            for number in range(1, count + 1):
                with closing(document[number - 1]) as page:
                    with closing(page.get_textpage()) as textpage:
                        plain = textpage.get_text_bounded()
                    if number not in elements and plain.strip():
                        fallback.add(number)
                    if number in fallback:
                        current = [Block(plain, number, "pypdfium2")]
                    else:
                        tables = []
                        pl_page = plumber.pages[number - 1]
                        geometry_verified = not pl_page.rotation and (
                            tuple(pl_page.cropbox or pl_page.mediabox) == tuple(pl_page.mediabox))
                        try:
                            # 미검증 회전·CropBox 좌표에 표 교체를 적용하지 않는다.
                            if not geometry_verified:
                                warnings.append({"page": number, "code": "unsupported_table_coordinates"})
                            elif any(page.get_objects(filter=[2], max_depth=3)):
                                tables = [(t.bbox, t.extract()) for t in pl_page.find_tables()]
                        except Exception:
                            warnings.append({"page": number, "code": "table_detection_failed"})
                        current = merge(elements.get(number, []), tables, float(pl_page.height), number,
                                        geometry_verified=geometry_verified)
                        if not current and plain.strip():
                            fallback.add(number)
                            current = [Block(plain, number, "pypdfium2")]
                    if not current or not any(b.text.strip() for b in current):
                        empty_pages.append(number)
                        current = [Block("", number, "unprocessed")]
                    blocks.extend(current)
        text, starts, rows, metadata = build_document(blocks, create_chunks=create_chunks)
    return {"text": text, "page_starts": starts, "rows": [asdict(r) for r in rows], "blocks": metadata,
            "pages": count, "fallback_pages": sorted(fallback), "empty_pages": empty_pages,
            "warnings": warnings, "attempts": attempts}


def main():
    # 부모가 프로세스 트리 관리에 등록하기 전에는 JVM을 시작하지 않는다.
    if sys.stdin.readline().strip() != "go":
        return 2
    no_chunks = sys.argv[-1:] == ["--no-chunks"]
    pdf, output, runtime = map(Path, sys.argv[1:-1] if no_chunks else sys.argv[1:])
    try:
        result = parse(pdf, output.parent, runtime, create_chunks=not no_chunks)
    except Exception as exc:
        result = {"error": type(exc).__name__}
    if os.name != "nt":
        import resource

        unit = 1 if sys.platform == "darwin" else 1024
        result["memory"] = {
            "worker_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * unit,
            "child_peak_rss_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * unit,
        }
    else:
        result["memory"] = None
    output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
