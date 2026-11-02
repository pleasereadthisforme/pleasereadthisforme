from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, as_completed, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path

from prtfm.config import Limits
from prtfm.indexing.chunker import assign_pages, chunk_doc
from prtfm.indexing.extract import CODE_EXTS, DOC_EXTS, SUPPORTED, ExtractError, TooManyPages, extract, extract_text
from prtfm.indexing.manifest import Todo, diff, file_hash
from prtfm.indexing.scope import Scope
from prtfm.indexing.tokenize import tokenize_doc
from prtfm.store.cache import TextCache
from prtfm.store.db import ChunkRow, Store

_DEFAULT_LIMITS = Limits()
# 정규화·추출·청킹·토큰화·사전이 바뀌어 같은 파일의 색인 결과가 달라지면 올린다.
# 저장된 버전과 다르면 바뀌지 않은 파일도 모두 다시 처리한다.
PIPELINE_VERSION = 2


@dataclass
class WorkResult:
    rel: str
    hash: str | None
    create_chunks: bool = True
    same: bool = False
    status: str = "ok"
    error: str | None = None
    pages: int | None = None
    text: str = ""
    page_starts: list[int] = field(default_factory=list)
    rows: list[ChunkRow] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class Report:
    files: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pages: int = 0
    chunks: int = 0
    seconds: float = 0.0
    cancelled: bool = False


def _completed(futures, executor, cancel):
    if cancel is None:
        yield from as_completed(futures)
        return
    pending = set(futures)
    while pending:
        if cancel.is_set():
            # Python 3.12에는 terminate_workers가 없어 보유한 작업자들을 직접 정리한다.
            processes = list(executor._processes.values())
            for process in processes:
                process.terminate()
            deadline = time.monotonic() + 5
            for process in processes:
                process.join(max(0, deadline - time.monotonic()))
                if process.is_alive():
                    process.kill()
            return
        done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
        yield from done


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


def process_file(path_str: str, rel: str, max_pages: int, prev_hash: str | None, create_chunks: bool = True) -> WorkResult:
    """작업자 프로세스에서 실행: 해시 → 추출 → 청킹 → 토큰화. 저장소에는 쓰지 않는다."""
    path = Path(path_str)
    h = None
    try:
        # Text is decoded from the exact bytes hashed; binary extractors receive
        # one private capture, never a second opening of the mutable original.
        before = path.stat()
        if path.suffix.lower() not in {'.pdf', '.docx', '.pptx'}:
            raw = path.read_bytes()
            h = hashlib.sha256(raw).hexdigest()
            ex = extract_text(raw, path.suffix.lower() in CODE_EXTS) if h != prev_hash else None
        else:
            with tempfile.TemporaryDirectory(prefix='prtfm-extract-') as tmp:
                captured = Path(tmp) / ('source' + path.suffix.lower())
                with path.open('rb') as source, captured.open('wb') as dest:
                    shutil.copyfileobj(source, dest, 1 << 20)
                h = file_hash(captured)
                ex = extract(captured, max_pages) if h != prev_hash else None
        after = path.stat()
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or file_hash(path) != h):
            raise ExtractError('source_changed')
        if h == prev_hash:
            return WorkResult(rel, h, same=True)
    except TooManyPages as e:
        return WorkResult(rel, h, status="skipped_pages", error=str(e))
    except (ExtractError, OSError) as e:
        return WorkResult(rel, h, status="error", error=str(e))
    except Exception as e:
        return WorkResult(rel, h, status="error", error=_err(e))
    try:
        chunks = chunk_doc(ex.text, headings=not ex.is_code) if create_chunks else []
        pages = assign_pages(ex.text, chunks, ex.page_starts)
        rows = [ChunkRow(i, pg, c.heading_trail, c.text, " ".join(tokenize_doc(c.text)))
                for i, (c, pg) in enumerate(zip(chunks, pages, strict=True))]
    except Exception as e:  # 한 파일의 예기치 못한 실패가 전체 실행을 멈추지 않게 한다
        return WorkResult(rel, h, status="error", error=_err(e))
    return WorkResult(rel, h, create_chunks=create_chunks, pages=ex.pages, text=ex.text, page_starts=ex.page_starts, rows=rows)


def _apply(res: WorkResult, todo: Todo, store: Store, cache: TextCache, rep: Report, *, original_text=False) -> None:
    """결과를 저장소에 반영한다. 저장(캐시·sqlite) 실패는 이 파일만 오류로 남기고 실행은 계속한다."""
    e = todo.entry
    try:
        if (res.same or res.status == 'ok') and file_hash(e.path) != res.hash:
            res = WorkResult(e.rel, res.hash, status='error', error='source_changed')
        previous = store.con.execute('SELECT id, status FROM docs WHERE path=?', (e.rel,)).fetchone()
        if res.same:
            store.touch_doc(todo.doc_id, e.size, e.mtime_ns)
            with store.con:
                store.con.execute('UPDATE docs SET error=NULL WHERE id=?', (todo.doc_id,))
        elif res.status == "ok":
            store.write_doc(e.rel, e.size, e.mtime_ns, res.hash, res.status, res.error, res.pages, res.rows,
                            parse_stage=1, before_commit=lambda i: (
                                cache.prepare_original(i, store.revision(i), res.hash, res.create_chunks, pipeline_version=PIPELINE_VERSION) if original_text else
                                cache.prepare(i, store.revision(i), res.text, res.page_starts,
                                              {**res.metadata, "create_chunks": res.create_chunks, "pipeline_version": PIPELINE_VERSION})))
        elif previous is not None and previous['status'] == 'ok':
            # Keep the last valid generation, identity and vector references.
            # The source reader independently rejects a changed source. error
            # records the failed attempt and makes the next scan retry it.
            with store.con:
                store.con.execute('UPDATE docs SET error=? WHERE id=?',
                                  (res.error or res.status, previous['id']))
        else:
            store.write_doc(e.rel, e.size, e.mtime_ns, res.hash, res.status, res.error, res.pages, None,
                            parse_stage=0, before_commit=cache.delete)
    except Exception as ex:
        rep.errors.append((e.rel, _err(ex)))
        return
    if res.same:
        rep.unchanged += 1
        return
    if todo.doc_id is None:
        rep.new += 1
    else:
        rep.changed += 1
    if res.status == "ok":
        rep.pages += res.pages or 0
        rep.chunks += len(res.rows)
    else:
        if res.status.startswith("skipped"):
            rep.skipped.append((e.rel, res.status))
        else:
            rep.errors.append((e.rel, res.error or ""))


def workspace_chunk_policy(store: Store) -> str:
    """Product entry points: preserve existing policy; new folders use full documents."""
    saved = store.con.execute("SELECT value FROM app_meta WHERE key='chunk_policy'").fetchone()
    if saved is not None:
        if saved[0] not in (0, 1):
            raise ValueError('unknown stored chunk policy')
        return 'selected' if saved[0] == 1 else 'legacy'
    # Pre-policy workspaces can have no remaining files after a completed scan.
    if store.docs() or store.get_pipeline_version() != 0:
        return 'legacy'
    return 'selected'


def run_pass1(scope: Scope, store: Store, cache: TextCache, limits: Limits = _DEFAULT_LIMITS,
              workers: int | None = None, on_progress: Callable[[int, int], None] | None = None,
              cancel=None, chunk_policy="legacy", paths=None, excluded_exts=frozenset()) -> Report:
    if chunk_policy not in ('legacy', 'selected'):
        raise ValueError('unknown chunk policy')
    mode = int(chunk_policy == 'selected')
    saved = store.con.execute("SELECT value FROM app_meta WHERE key='chunk_policy'").fetchone()
    if (saved is not None and saved[0] != mode) or (saved is None and mode and store.docs()):
        raise ValueError('chunk policy migration requires a separate workspace')
    with store.con:
        store.con.execute("INSERT OR IGNORE INTO app_meta VALUES ('chunk_policy', ?)", (mode,))
    enabled = {r[0] for r in store.con.execute('SELECT doc_id FROM semantic_files WHERE enabled=1')}
    t0 = time.perf_counter()
    cache.bind(store)
    cache.recover()
    rep = Report()
    entries = list(scope.walk(SUPPORTED - excluded_exts, errors=rep.errors))
    if paths is not None:
        entries = [e for e in entries if e.rel in paths]
        if {e.rel for e in entries} != set(paths):
            raise ValueError('selected file unavailable')
    rep.files = len(entries)
    total_bytes = sum(e.size for e in entries)
    if rep.files > limits.warn_files or total_bytes > limits.warn_total_bytes:
        rep.warnings.append(f"large folder: {rep.files} files, {total_bytes / 1024**3:.1f} GB — consider a subfolder")

    docs = {k: v for k, v in store.docs().items() if Path(k).suffix.lower() not in excluded_exts}
    if paths is not None:
        docs = {k: v for k, v in docs.items() if k in paths}
    small = []
    for e in entries:
        if e.size > limits.skip_file_bytes:
            d = docs.get(e.rel)
            if d is not None and d.status == 'ok':
                with store.con:
                    store.con.execute("UPDATE docs SET error='skipped_size' WHERE id=?", (d.id,))
            else:
                store.upsert_doc(e.rel, e.size, e.mtime_ns, None, "skipped_size")
            rep.skipped.append((e.rel, "skipped_size"))
        else:
            small.append(e)
    skipped_rels = {r for r, _ in rep.skipped}
    kept = {k: v for k, v in docs.items() if k not in skipped_rels}
    plan = diff(small, kept)
    # Absence in an incomplete enumeration is not evidence of deletion.
    failed_paths = [Path(rel) for rel, _ in rep.errors]
    protected = {d.id for rel, d in kept.items()
                 if any(p == Path('.') or p == Path(rel) or p in Path(rel).parents
                        for p in failed_paths)}
    plan.deleted = [i for i in plan.deleted if i not in protected]
    stale = store.get_pipeline_version() != PIPELINE_VERSION
    # Partial runs cannot mark the entire workspace current. Each committed
    # generation records its version, so already-current files can be reused.
    pending = {t.entry.rel for t in plan.todo}
    for entry in small:
        doc = kept.get(entry.rel)
        if doc is None:
            continue
        try:
            metadata = json.loads((cache.root / str(doc.id) / str(doc.revision) / 'metadata.json').read_text())
            version = metadata.get('pipeline_version')
        except (OSError, ValueError, AttributeError):
            version = None
        current = type(version) is int and version == PIPELINE_VERSION
        legacy_current = version is None and not stale
        if not current and not legacy_current:
            plan.todo = [t for t in plan.todo if t.entry.rel != entry.rel]
            plan.todo.append(Todo(entry, doc.id, None))
            if entry.rel not in pending:
                plan.unchanged -= 1
    if chunk_policy == 'selected':
        pending = {t.entry.rel for t in plan.todo}
        for entry in small:
            doc = kept.get(entry.rel)
            if doc is None:
                continue
            meta_path = cache.root / str(doc.id) / str(doc.revision) / 'metadata.json'
            try:
                previous = json.loads(meta_path.read_text()).get('create_chunks')
            except (OSError, ValueError):
                previous = None
            if previous != (doc.id in enabled):
                plan.todo = [t for t in plan.todo if t.entry.rel != entry.rel]
                plan.todo.append(Todo(entry, doc.id, None))
                if entry.rel not in pending:
                    plan.unchanged -= 1
    rel_of = {d.id: rel for rel, d in docs.items()}
    for doc_id in plan.deleted:
        try:
            store.delete_doc(doc_id)
            cache.delete(doc_id)
        except Exception as e:
            rep.errors.append((rel_of[doc_id], _err(e)))
            continue
        rep.deleted += 1
    rep.unchanged = plan.unchanged

    def original_text(todo):
        if chunk_policy != 'selected' or Path(todo.entry.rel).suffix.lower() not in ((DOC_EXTS | CODE_EXTS) - {'.pdf', '.docx', '.pptx'}):
            return False
        if todo.doc_id is None:
            return True
        return cache.direct_revision(todo.doc_id, store.revision(todo.doc_id))

    # Converted local formats share a physical body. Hold the key through commit,
    # so a second workspace cannot race extraction or a not-yet-published result.
    from prtfm.store.shared_results import SharedResult
    planned_total, converted_done = len(plan.todo), 0
    converted = [t for t in plan.todo if Path(t.entry.rel).suffix.lower() in {'.docx', '.pptx'}]
    for todo in converted:
        if cancel is not None and cancel.is_set():
            break
        if on_progress:
            on_progress(converted_done, planned_total)
        try:
            source = scope.resolve(todo.entry.rel)
            digest = file_hash(source)
            shared = SharedResult(source, digest, {'kind': 'local', 'pipeline': PIPELINE_VERSION})
            with shared.lock(cancel):
                found = shared.find()
                create_chunks = chunk_policy == 'legacy' or todo.doc_id in enabled
                if found:
                    if len(found['starts']) > limits.skip_file_pages:
                        raise TooManyPages('shared result exceeds page limit')
                    chunks = chunk_doc(found['text']) if create_chunks else []
                    pages = assign_pages(found['text'], chunks, found['starts'])
                    rows = [ChunkRow(i, pg, c.heading_trail, c.text, " ".join(tokenize_doc(c.text)))
                            for i, (c, pg) in enumerate(zip(chunks, pages, strict=True))]
                    res = WorkResult(todo.entry.rel, digest, create_chunks=create_chunks,
                                     text=found['text'], page_starts=found['starts'],
                                     pages=len(found['starts']), rows=rows,
                                     metadata={'_shared_body': str(found['base'] / 'document.md')})
                else:
                    res = process_file(str(source), todo.entry.rel, limits.skip_file_pages, None, create_chunks)
                if res.hash != digest or file_hash(scope.resolve(todo.entry.rel)) != digest:
                    raise ValueError('source_changed')
                res.metadata = shared.metadata(res.metadata)
                _apply(res, todo, store, cache, rep)
                doc = store.docs().get(todo.entry.rel)
                if doc and doc.status == 'ok' and doc.content_hash == digest:
                    shared.register(cache.root / str(doc.id) / str(doc.revision))
        except Exception as exc:
            _apply(WorkResult(todo.entry.rel, None, status='error', error=_err(exc)),
                   todo, store, cache, rep)
        converted_done += 1
        if on_progress:
            on_progress(converted_done, planned_total)
    plan.todo = [t for t in plan.todo if t not in converted]

    total, done, broken = planned_total, converted_done, False
    n = workers if workers is not None else min(4, os.cpu_count() or 1)
    args = [(str(t.entry.path), t.entry.rel, limits.skip_file_pages, t.prev_hash) for t in plan.todo]
    if chunk_policy == 'selected':
        args = [(*a, t.doc_id in enabled) for a, t in zip(args, plan.todo, strict=True)]
    if (n <= 1 or total <= 1) and cancel is None:
        results = ((t, process_file(*a)) for t, a in zip(plan.todo, args, strict=True))
        for t, res in results:
            _apply(res, t, store, cache, rep, original_text=original_text(t))
            done += 1
            if on_progress:
                on_progress(done, total)
    else:
        from prtfm.credentials.provider import configure_development
        configure_development()
        with ProcessPoolExecutor(max_workers=n, mp_context=mp.get_context("spawn")) as ex:
            futs = {ex.submit(process_file, *a): t for t, a in zip(plan.todo, args, strict=True)}
            for f in _completed(futs, ex, cancel):
                t = futs.pop(f)
                try:
                    res = f.result()
                except BrokenProcessPool:
                    broken = True
                    # 작업자가 비정상 종료해 원인 파일을 알 수 없다. 남은 파일은 기록하지 않아 다음 스캔에서 다시 처리된다.
                    rep.errors += [(x.entry.rel, "worker crashed") for x in (t, *futs.values())]
                    break
                except Exception as e:
                    res = WorkResult(t.entry.rel, None, status="error", error=_err(e))
                _apply(res, t, store, cache, rep, original_text=original_text(t))
                done += 1
                if on_progress:
                    on_progress(done, total)
    rep.cancelled = cancel is not None and cancel.is_set()
    if paths is None and not excluded_exts and stale and not broken and not rep.cancelled and not rep.errors:  # 중단된 실행이면 다음 스캔에서 다시 처리한다
        store.set_pipeline_version(PIPELINE_VERSION)
    rep.seconds = round(time.perf_counter() - t0, 2)
    cache.recover()
    from prtfm.store.shared_results import prune_catalogue
    prune_catalogue()
    return rep
