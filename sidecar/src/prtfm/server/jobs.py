from __future__ import annotations

import dataclasses
import os
import threading
from pathlib import Path

from prtfm.indexing.pass1 import run_pass1, workspace_chunk_policy
from prtfm.indexing.pass2 import run_pass2
from prtfm.indexing.scope import Scope
from prtfm.store.workspace import open_workspace


class JobRunning(Exception):
    pass


class IndexJobs:
    """색인 작업은 한 번에 하나만(단일 writer). 상태는 폴더 실제 경로별로 보관한다."""

    def __init__(self):
        self._lock = threading.Lock()
        self._status: dict[str, dict] = {}
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._controls = []
        self._accept_control = False
        self._folder = None
        self._closing = False
        self._chats = set()

    def request_close(self, force=False) -> bool:
        """Atomically reserve idle shutdown, or cancel after explicit confirmation."""
        with self._lock:
            busy = self._thread is not None and self._thread.is_alive()
            if (busy or self._chats) and not force:
                return False
            self._closing = True
            if force:
                self._cancel.set()
                for cancel in self._chats:
                    cancel.set()
            return True

    def abort_close(self) -> None:
        """Release a reservation when the native window remains open."""
        with self._lock:
            self._closing = False

    def begin_chat(self):
        """Register before opening resources, under the shutdown reservation lock."""
        with self._lock:
            if self._closing:
                raise JobRunning
            cancel = threading.Event()
            self._chats.add(cancel)
            return cancel

    def idle_operation(self, operation):
        """Reserve the app while removing a workspace, including session data."""
        with self._lock:
            if self._closing or self._chats or (self._thread is not None and self._thread.is_alive()):
                raise JobRunning
            return operation()

    def end_chat(self, cancel):
        with self._lock:
            self._chats.discard(cancel)

    def start(self, folder: Path, paths=None, automatic=False) -> dict:
        scope = Scope(folder)  # ScopeError는 호출자가 처리
        key = str(scope.root)
        with self._lock:
            if self._closing:
                raise JobRunning
            if self._thread is not None and self._thread.is_alive():
                current = self._status.get(key, {})
                if (automatic and self._folder == key and current.get("automatic")
                        and current.get("state") == "running" and not self._cancel.is_set()):
                    return dict(current)
                raise JobRunning
            self._cancel.clear()
            self._folder = key
            st = {"state": "running", "done": 0, "total": 0, "report": None, "error": None,
                  "automatic": automatic, "stage": "pass1", "stage_done": 0, "stage_total": 0, "current_doc": None,
                  "pass1_ready": False, "degraded_pages": 0, "failed_docs": []}
            self._status[key] = st
            self._thread = threading.Thread(target=self._run, args=(scope, st, paths, automatic), daemon=True)
            self._thread.start()
        return dict(st)

    def _run(self, scope: Scope, st: dict, paths=None, automatic=False) -> None:
        def progress(done: int, total: int) -> None:
            self._drain(ws.store)
            st["done"], st["total"] = done, total
            st["stage_done"], st["stage_total"] = done, total

        try:
            ws = open_workspace(scope.root)
            try:
                with self._lock:
                    self._accept_control = True
                options = {"paths": paths} if paths is not None else {}
                if automatic:
                    from prtfm.indexing.extract import VLM_EXTS
                    options['excluded_exts'] = VLM_EXTS
                    from prtfm.indexing.vision import pending, reuse_prepared
                    st["reused_files"] = reuse_prepared(scope, ws, self._cancel)
                    st['excluded_files'] = pending(scope, ws)
                rep = run_pass1(scope, ws.store, ws.cache, on_progress=progress, cancel=self._cancel,
                                chunk_policy=workspace_chunk_policy(ws.store), **options)
                st["report"] = dataclasses.asdict(rep)
                first_total = st["total"]
                st["pass1_ready"], st["stage"] = not rep.cancelled, "pass2"

                def refined(done, total, current):
                    self._drain(ws.store)
                    st.update(stage_done=done, stage_total=total, current_doc=current,
                              done=first_total + done, total=first_total + total)

                if automatic:
                    st.update(state='cancelled' if rep.cancelled else 'done', stage='done', current_doc=None)
                    return
                refinement = run_pass2(scope, ws.store, ws.cache, self._cancel, refined, **({"paths": paths} if paths is not None else {}))
                st.update(degraded_pages=refinement["degraded_pages"], failed_docs=refinement["failed_docs"],
                          state="cancelled" if refinement["cancelled"] else "done", current_doc=None)
                st["report"]["refinement"] = refinement
            finally:
                self._finish_controls(ws.store)
                ws.close()
        except Exception as e:  # 작업 스레드 예외는 상태로만 알린다
            st["error"] = type(e).__name__
            st["state"] = "error"

    def status(self, folder: Path) -> dict | None:
        # 폴더가 그새 사라져도 예외 없이 조회되게 Scope 검사 없이 실제 경로로 찾는다(키는 start와 같은 형태)
        st = self._status.get(os.path.realpath(folder))
        return dict(st) if st else None

    def cancel(self, folder: Path) -> bool:
        with self._lock:
            st = self._status.get(os.path.realpath(folder))
            if not st or st["state"] != "running":
                return False
            self._cancel.set()
            return True

    def wait(self, timeout: float) -> None:
        t = self._thread
        if t is not None:
            t.join(timeout)

    def _drain(self, store):
        with self._lock:
            pending, self._controls = self._controls, []
        for operation, event, result in pending:
            try:
                result.append(operation(store))
            except Exception as exc:
                result.append(exc)
            finally:
                event.set()

    def _finish_controls(self, store):
        with self._lock:
            self._accept_control = False
        self._drain(store)

    def operation(self, folder, operation, queue=False):
        scope = Scope(folder)
        with self._lock:
            if self._closing:
                raise JobRunning
            if self._thread is not None and self._thread.is_alive():
                if not queue or not self._accept_control or self._folder != str(scope.root):
                    raise JobRunning
                event, result = threading.Event(), []
                self._controls.append((operation, event, result))
            else:
                ws = open_workspace(scope.root)
                try:
                    return operation(ws.store)
                finally:
                    ws.close()
        event.wait()
        if isinstance(result[0], Exception):
            raise result[0]
        return result[0]

    def workspace_operation(self, folder, operation):
        """Serialize policy changes with index/embedding jobs and external writers."""
        scope = Scope(folder)
        with self._lock:
            if self._closing:
                raise JobRunning
            if self._thread is not None and self._thread.is_alive():
                raise JobRunning
            ws = open_workspace(scope.root)
            try:
                return operation(ws)
            finally:
                ws.close()

    def start_vision(self, folder, quote_id, files, transport=None):
        from prtfm.indexing.vision import claim
        scope = Scope(folder)
        with self._lock:
            if self._closing:
                raise JobRunning
            if self._thread is not None and self._thread.is_alive():
                raise JobRunning
            ws = open_workspace(scope.root)
            try:
                quote = claim(scope, ws, quote_id, files)
            finally:
                ws.close()
            self._cancel.clear()
            self._folder = str(scope.root)
            st = {'state': 'running', 'stage': 'vision', 'quote_id': quote_id,
                  'done': 0, 'total': sum(i['page_count'] for i in quote['items'])}
            self._status[self._folder] = st
            self._thread = threading.Thread(target=self._run_vision, args=(scope, quote, st, transport), daemon=True)
            self._thread.start()
            return dict(st)

    def _run_vision(self, scope, quote, st, transport):
        from prtfm.indexing.vision import run, transcribe
        try:
            ws = open_workspace(scope.root)
            try:
                st.update(run(scope, ws, quote, self._cancel,
                              lambda done, total, path: st.update(done=done, total=total, current_doc=path),
                              transport or transcribe))
            finally:
                ws.close()
        except Exception as exc:
            st.update(state='error', error=type(exc).__name__)

    def start_semantic(self, folder, quote_id, budget, price, transport=None):
        scope = Scope(folder)
        with self._lock:
            if self._closing:
                raise JobRunning
            if self._thread is not None and self._thread.is_alive():
                raise JobRunning
            self._cancel.clear()
            self._folder = str(scope.root)
            st = {'state': 'running', 'stage': 'embedding', 'done': 0, 'total': 0, 'error': None}
            self._status[self._folder] = st
            self._thread = threading.Thread(target=self._run_semantic,
                                            args=(scope, st, quote_id, budget, price, transport), daemon=True)
            self._thread.start()
        return dict(st)

    def _run_semantic(self, scope, st, quote_id, budget, price, transport):
        from prtfm.indexing.embedding import run
        from prtfm.indexing.quotes import QuoteUnavailable

        try:
            ws = open_workspace(scope.root)
            try:
                with self._lock:
                    self._accept_control = True
                result = run(ws.store, ws.dir / 'vectors', quote_id, budget, price, self._cancel, transport,
                             self._drain, lambda done, total: st.update(done=done, total=total))
                st.update(result)
            finally:
                self._finish_controls(ws.store)
                ws.close()
        except QuoteUnavailable:
            st.update(state='error', error='quote_unavailable')
        except Exception as exc:
            st.update(state='error', error=type(exc).__name__)
