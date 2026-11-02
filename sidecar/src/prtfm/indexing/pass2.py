"""단일 writer가 문서별 정밀 결과를 준비하고 활성화한다."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

from prtfm.indexing.manifest import file_hash
from prtfm.indexing.pdf_refine import runtime_dir
from prtfm.indexing.processes import Cancelled, run_worker
from prtfm.indexing.result_validation import validate_result
from prtfm.store.db import ChunkRow

PASS2_VERSION = 4


def record(store, doc, state, code=None, details=None):
    with store.con:
        store.con.execute(
            "INSERT OR REPLACE INTO refinement VALUES (?,?,?,?,?,?)",
            (doc.id, doc.content_hash, PASS2_VERSION, state, code, json.dumps(details or {})))


def candidates(store):
    states = {r["doc_id"]: r for r in store.con.execute("SELECT * FROM refinement")}
    return [d for d in store.docs().values() if Path(d.path).suffix.lower() == ".pdf" and d.status == "ok"
            and not (d.parse_stage == 2 and (state := states.get(d.id)) is not None
                     and state["input_hash"] == d.content_hash and state["version"] == PASS2_VERSION
                     and state["state"] in ("done", "degraded"))]


def run_pass2(scope, store, cache, cancel=None, on_progress=None, timeout=900, runner=run_worker, paths=None):
    cancel = cancel or threading.Event()
    cache.bind(store)
    cache.recover()
    with store.con:
        store.con.execute("UPDATE refinement SET state='interrupted' WHERE state='running'")
    docs = [d for d in candidates(store) if paths is None or d.path in paths]
    mode = store.con.execute("SELECT value FROM app_meta WHERE key='chunk_policy'").fetchone()
    selected = mode is not None and mode[0] == 1
    enabled = {r[0] for r in store.con.execute('SELECT doc_id FROM semantic_files WHERE enabled=1')}
    report = {"total": len(docs), "done": 0, "failed_docs": [], "degraded_pages": 0, "cancelled": cancel.is_set()}
    if on_progress:
        on_progress(0, len(docs), None)
    for doc in docs:
        if cancel.is_set():
            report["cancelled"] = True
            break
        if on_progress:
            on_progress(report["done"], len(docs), doc.path)
        record(store, doc, "running")
        started = time.monotonic()
        try:
            source = scope.resolve(doc.path)
            with tempfile.TemporaryDirectory(prefix="refine-") as tmp:
                work = Path(tmp)
                copy = work / "source.pdf"
                shutil.copyfile(source, copy)
                if file_hash(copy) != doc.content_hash:
                    raise ValueError("source_changed")
                output = work / "result.json"
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("document deadline")
                create_chunks = not selected or doc.id in enabled
                command = [sys.executable, "-B", "-m", "prtfm.indexing.pdf_refine", str(copy), str(output),
                           str(runtime_dir())]
                if not create_chunks:
                    command.append('--no-chunks')
                runner(command, cancel, remaining)
                if cancel.is_set():
                    raise Cancelled
                result = json.loads(output.read_text(encoding="utf-8"))
                if "error" in result:
                    raise RuntimeError(result["error"])
                source = scope.resolve(doc.path)
                if file_hash(source) != doc.content_hash:
                    raise ValueError("source_changed")
                if cancel.is_set():
                    raise Cancelled
                if time.monotonic() - started >= timeout:
                    raise TimeoutError("document deadline")
                validate_result(result, create_chunks=create_chunks)
                rows = [ChunkRow(**r) for r in result["rows"]] if create_chunks else []
                detail = {k: result[k] for k in ("fallback_pages", "empty_pages", "warnings", "attempts")}
                degraded = set(detail["fallback_pages"]) | set(detail["empty_pages"])
                state = "degraded" if degraded or detail["warnings"] else "done"

                def prepare(doc_id, result=result, detail=detail, state=state, create_chunks=create_chunks):
                    cache.prepare(doc_id, store.revision(doc_id), result["text"], result["page_starts"],
                                  {"blocks": result["blocks"], 'create_chunks': create_chunks, **detail})
                    if cancel.is_set():
                        raise Cancelled
                    # write_doc의 트랜잭션 안에서 상태도 함께 교체한다.
                    store.con.execute("UPDATE refinement SET state=?,failure_code=NULL,details=? WHERE doc_id=?",
                                      (state, json.dumps(detail), doc_id))

                store.write_doc(doc.path, doc.size, doc.mtime_ns, doc.content_hash, "ok", None, result["pages"],
                                rows, 2, before_commit=prepare)
                report["degraded_pages"] += len(degraded)
        except Cancelled:
            record(store, doc, "cancelled")
            report["cancelled"] = True
            break
        except Exception as exc:
            code = "source_changed" if isinstance(exc, ValueError) and str(exc) == "source_changed" else type(exc).__name__
            record(store, doc, "failed", code)
            report["failed_docs"].append({"doc": doc.path, "code": code})
        report["done"] += 1
        cache.recover()
        if on_progress:
            on_progress(report["done"], len(docs), None)
    return report
