from __future__ import annotations

from prtfm.localization import text as _ui_text

import contextlib
import hmac
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Generator, Iterator
from pathlib import Path
from typing import Literal

import anyio
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from prtfm.indexing.scope import Scope, ScopeError
from prtfm.llm.embeddings import configured_price, nusd
from prtfm.llm.openai_client import InputBudgetExceeded, LLMError
from prtfm.llm.usage import cost_usd
from prtfm.query.agent import HISTORY_TURNS, Delta, Done, ToolEnd, ToolStart, run_agent
from prtfm.query.file_commands import CommandUnavailable
from prtfm.query.tools import QueryTools
from prtfm.server.jobs import IndexJobs, JobRunning
from prtfm.store.locking import WriterBusy
from prtfm.store.sessions import SessionStore
from prtfm.store.workspace import open_workspace

DEFAULT_ORIGINS = frozenset({"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"})


class RecoveryRemoveReq(BaseModel):
    workspace: str
    kind: Literal['chat', 'embedding', 'vision']
    id: str
    confirmed: Literal[True]


class IndexReq(BaseModel):
    folder: str


class CleanupReq(BaseModel):
    category: Literal['analysis_cache', 'temporary', 'conversations']
    confirmed: Literal[True]


class ProcessedDeleteReq(IndexReq):
    revision: int
    confirmed: Literal[True]


class WorkspaceDeleteReq(BaseModel):
    confirmed: Literal[True]


class DisconnectReq(BaseModel):
    mode: Literal['disconnect', 'delete_data']
    confirmed: Literal[True]


class PrepareReq(IndexReq):
    automatic: bool = False
    file: str | None = None


class VisionEstimateReq(IndexReq):
    files: list[str]
    progress: bool = False


class VisionRunReq(VisionEstimateReq):
    quote_id: str
    consent: bool = False


class ChatReq(BaseModel):
    folder: str
    question: str
    session_id: str | None = None
    mode: Literal['agentic', 'rag'] = 'agentic'
    semantic: bool | None = None
    embedding_budget_usd: str | None = None


def semantic_for_request(ws, req, cancel):
    """Automatic supplementation is optional; configuration failure preserves file search."""
    from prtfm.retrieval.semantic import SemanticSearch, active
    automatic = req.semantic is None and req.mode == 'agentic'
    if not automatic and not req.semantic:
        return None
    try:
        if automatic and not active(ws.store):
            return None
        price = configured_price()
        budget = (os.environ.get('PRTFM_QUERY_EMBEDDING_BUDGET_USD', '0.01')
                  if automatic else req.embedding_budget_usd)
        nusd(budget)
        if price is None:
            raise ValueError('unknown embedding price')
        return SemanticSearch(ws.store, ws.dir, price, budget, cancel=cancel)
    except Exception:
        if automatic:
            return None
        raise


class EstimateReq(IndexReq):
    doc_ids: list[int]


class SemanticRunReq(IndexReq):
    quote_id: str
    budget_usd: str


class ToggleReq(IndexReq):
    enabled: bool


def _sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _scope(folder: str) -> Scope:
    try:
        return Scope(Path(folder))
    except ScopeError as e:
        raise HTTPException(400, "not a folder") from e


class _ClosingStream(StreamingResponse):
    """응답이 끝나거나 연결 끊김·취소로 멈추면 동기 SSE 생성기를 곧바로 닫는다(정리가 gc를 기다리지 않게)."""

    def __init__(self, gen: Generator[str], on_close: Callable[[], None], on_cancel=None, **kw):
        super().__init__(gen, **kw)
        self._gen, self._on_close = gen, on_close
        self._on_cancel = on_cancel

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self._on_cancel is not None:
                self._on_cancel()
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self._close)

    def _close(self) -> None:
        try:
            self._gen.close()  # 생성기의 finally가 세션·LLM·색인 연결을 닫는다
        finally:
            self._on_close()  # 한 번도 시작되지 않은 생성기는 finally가 돌지 않으므로 색인 연결을 따로 닫는다


def create_app(token: str, allowed_origins: frozenset[str], llm_factory: Callable[[], object],
               jobs: IndexJobs, connect_code: str | None = None) -> FastAPI:
    from prtfm.query.search_temp import cleanup_search_temps

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.search_temp_cleanup = cleanup_search_temps()
    app.state.jobs = jobs
    from prtfm.server.recovery import startup_recovery
    recovery_snapshot = startup_recovery()
    connect_lock = threading.Lock()
    connect_deadline = time.monotonic() + 120
    pending_code = connect_code

    @app.middleware("http")
    async def ui_language(request: Request, call_next):
        from prtfm.localization import request_language
        value = request.headers.get('accept-language', '').split(',')[0].split('-')[0].lower()
        token = request_language.set(value if value in ('en', 'ko') else None)
        try:
            return await call_next(request)
        finally:
            request_language.reset(token)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # CORS 사전요청은 토큰 없이 오므로 CORS 미들웨어(바깥)가 처리하게 둔다
        if request.method == "OPTIONS":
            return await call_next(request)
        origin = request.headers.get("origin")
        local_same_origin = (request.url.hostname in ('127.0.0.1', 'localhost', '::1')
                             and origin == f'{request.url.scheme}://{request.url.netloc}')
        if origin is not None and origin not in allowed_origins and not local_same_origin:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        if request.method == 'GET' and request.url.path in (
                '/activity-ui/', '/activity-ui/app.js', '/activity-ui/style.css', '/activity-ui/ux.js', '/activity-ui/semantic.js', '/activity-ui/theme.js', '/activity-ui/storage.js', '/activity-ui/characters.js', '/activity-ui/preferences.js', '/activity-ui/i18n.js', '/activity-ui/localize-markup.js', '/activity-ui/character-user-typing-sheet-v7.png', '/activity-ui/character-bot-typing-approved-v1.png', '/activity-ui/character-bot-question-v4.png', '/activity-ui/character-bot-papers-v5.png', '/activity-ui/character-bot-idle-v8.png'):
            return await call_next(request)
        if request.method == 'POST' and request.url.path == '/activity-ui/connect':
            return await call_next(request)
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origins=sorted(allowed_origins), allow_methods=["GET", "POST"],
                       allow_headers=["authorization", "content-type"])

    @app.post('/activity-ui/connect')
    def connect_browser(payload: dict):
        nonlocal pending_code
        code = payload.get('code')
        with connect_lock:
            if (not isinstance(code, str) or len(code) > 128 or not pending_code
                    or time.monotonic() > connect_deadline
                    or not hmac.compare_digest(code.encode(), pending_code.encode())):
                raise HTTPException(401, 'connection code invalid or expired')
            pending_code = None
        return JSONResponse({'token': token}, headers={'Cache-Control': 'no-store'})

    @app.get('/credentials/status')
    def credential_status():
        from prtfm.credentials.provider import get_key
        return JSONResponse({'configured': bool(get_key())}, headers={'Cache-Control': 'no-store'})

    @app.post('/desktop/close-check', status_code=204)
    def desktop_close_check():
        if not jobs.request_close():
            raise HTTPException(409, 'document processing or chat is active')
        return Response(status_code=204)

    @app.post('/desktop/close-abort', status_code=204)
    def desktop_close_abort():
        jobs.abort_close()
        return Response(status_code=204)

    @app.post('/desktop/close-confirm', status_code=204)
    def desktop_close_confirm():
        jobs.request_close(force=True)
        return Response(status_code=204)

    @app.get("/desktop/recovery")
    def recovery_status() -> dict:
        return recovery_snapshot

    @app.get('/desktop/recovery/items')
    def recovery_items():
        from prtfm.server.recovery_items import inspect_records
        return inspect_records()

    @app.post('/desktop/recovery/remove')
    def recovery_remove(req: RecoveryRemoveReq):
        from prtfm.server.recovery_items import remove_record
        try:
            return remove_record(req.workspace, req.kind, req.id)
        except WriterBusy as exc:
            raise HTTPException(409, _ui_text('작업이 실행 중입니다. 완료 후 다시 시도하세요.')) from exc
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise HTTPException(409, _ui_text('기록이 변경되었거나 접근할 수 없습니다. 새로고침해 주세요.')) from exc

    summary_gate = threading.Lock()

    @app.get('/storage/summary')
    def storage_status(request: Request):
        from prtfm.store.summary import storage_summary
        if request.query_params:
            raise HTTPException(400, 'query parameters are not supported')
        if not summary_gate.acquire(blocking=False):
            raise HTTPException(503, 'storage summary is busy')
        try:
            return JSONResponse(storage_summary(), headers={'Cache-Control': 'no-store'})
        finally:
            summary_gate.release()

    @app.get('/storage/processed')
    def processed_documents():
        from prtfm.store.document_cleanup import processed_catalog
        return JSONResponse(processed_catalog(), headers={'Cache-Control': 'no-store'})

    @app.post('/storage/processed/{doc_id}/delete')
    def delete_processed_document(doc_id: int, req: ProcessedDeleteReq):
        from prtfm.store.document_cleanup import remove_processed
        from prtfm.store.shared_results import prune_catalogue
        scope = _scope(req.folder)
        try:
            result = jobs.workspace_operation(scope.root, lambda ws: remove_processed(ws, doc_id, req.revision))
            prune_catalogue()
            return result
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, 'document is busy; retry after the current operation') from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, 'document changed or cleanup is incomplete; refresh and retry') from exc

    @app.post('/storage/cleanup')
    def storage_cleanup(req: CleanupReq):
        from prtfm.store.cleanup import clear_analysis_cache
        from prtfm.store.conversation_cleanup import clear_conversations
        import sqlite3
        if not summary_gate.acquire(blocking=False):
            raise HTTPException(409, 'storage operation is busy')
        try:
            result = (clear_conversations() if req.category == 'conversations' else
                      clear_analysis_cache() if req.category == 'analysis_cache' else cleanup_search_temps())
            return JSONResponse(result, headers={'Cache-Control': 'no-store'})
        except WriterBusy as exc:
            raise HTTPException(409, 'cache is busy') from exc
        except (OSError, ValueError, sqlite3.Error) as exc:
            raise HTTPException(409, 'storage could not be safely cleaned') from exc
        finally:
            summary_gate.release()

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get('/activity-ui/')
    @app.get('/activity-ui/{asset}')
    def activity_ui(asset: str = 'index.html'):
        if asset not in ('index.html', 'app.js', 'style.css', 'ux.js', 'semantic.js', 'theme.js', 'storage.js', 'characters.js', 'preferences.js', 'i18n.js', 'localize-markup.js', 'character-user-typing-sheet-v7.png', 'character-bot-typing-approved-v1.png', 'character-bot-question-v4.png', 'character-bot-papers-v5.png', 'character-bot-idle-v8.png'):
            raise HTTPException(404)
        return FileResponse(Path(__file__).parent / 'activity_ui' / asset,
                            headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/registered-folders')
    def registered_folders():
        from prtfm.store.folders import list_folders

        return {'folders': list_folders()}

    @app.post('/registered-folders')
    def register_folder(req: IndexReq):
        from prtfm.server.folders import browse
        from prtfm.store.folders import register_folder

        if not req.folder.strip():
            raise HTTPException(400, 'folder is required')
        scope = _scope(req.folder)
        try:
            browse(scope)
        except (ScopeError, OSError):
            raise HTTPException(400, 'folder unavailable or excluded') from None
        return register_folder(scope.root)

    @app.post('/registered-folders/{folder_id}/open-file')
    def open_document(folder_id: str, req: dict):
        from prtfm.server.open_document import OpenError, open_registered
        try:
            return open_registered(folder_id, req.get('relative'))
        except OpenError as exc:
            raise HTTPException(400, str(exc)) from None
        except (ScopeError, OSError):
            raise HTTPException(400, 'file_missing') from None

    @app.delete('/registered-folders/{folder_id}')
    def unregister_folder(folder_id: str):
        from prtfm.store.folders import unregister_folder

        unregister_folder(folder_id)
        return {'removed': True}

    @app.post('/registered-folders/{folder_id}/disconnect')
    def disconnect_registered_folder(folder_id: str, req: DisconnectReq):
        from prtfm.store.folder_cleanup import disconnect
        try:
            return jobs.idle_operation(lambda: disconnect(folder_id, req.mode == 'delete_data'))
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, 'workspace is busy') from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, 'cleanup incomplete or unavailable; refresh and retry') from exc

    @app.post('/storage/workspaces/{key}/delete')
    def delete_storage_workspace(key: str, req: WorkspaceDeleteReq):
        from prtfm.store.folder_cleanup import delete_workspace
        try:
            return jobs.idle_operation(lambda: delete_workspace(key))
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, 'workspace is busy') from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(409, 'cleanup incomplete or unavailable; refresh and retry') from exc

    @app.get('/local-folders')
    def local_folders(relative: str = '.'):
        from prtfm.server.folders import browse_local

        try:
            return browse_local(relative)
        except (ScopeError, OSError):
            raise HTTPException(400, 'folder unavailable or outside scope') from None

    @app.get('/folders')
    def folders(folder: str, relative: str = '.', metadata_only: bool = False):
        from prtfm.server.folders import browse

        try:
            result = browse(_scope(folder), relative)
            if metadata_only:
                return result
            from prtfm.server.preparation import preparation_state
            result['preparation'] = preparation_state(result['folder'])
            from prtfm.store.folders import remember_workspace
            remember_workspace(result['folder'])
            return result
        except (ScopeError, OSError):
            raise HTTPException(400, 'folder unavailable or outside scope') from None

    @app.post('/vision/estimate')
    def vision_estimate(req: VisionEstimateReq):
        from prtfm.indexing.vision import estimate
        scope = _scope(req.folder)
        if req.progress:
            import queue
            messages = queue.Queue()
            cancel = threading.Event()
            def produce():
                try:
                    result = jobs.workspace_operation(scope.root, lambda ws: estimate(scope, ws, req.files, messages.put, cancel))
                    messages.put({'kind': 'done', 'quote': result})
                except Exception as exc:
                    message = (_ui_text('다른 문서 작업이 진행 중입니다.') if isinstance(exc, (JobRunning, WriterBusy))
                               else _ui_text('견적 분석을 완료하지 못했습니다. 다시 시도하세요.'))
                    messages.put({'kind': 'error', 'error': message})
            threading.Thread(target=produce, daemon=True).start()
            def stream():
                try:
                    while not cancel.is_set():
                        try:
                            event = messages.get(timeout=2)
                        except queue.Empty:
                            event = {'kind': 'heartbeat'}
                        yield json.dumps(event, ensure_ascii=False) + '\n'
                        if event['kind'] in ('done', 'error'):
                            break
                finally:
                    cancel.set()
            return _ClosingStream(stream(), cancel.set, on_cancel=cancel.set, media_type='application/x-ndjson')
        try:
            return jobs.workspace_operation(scope.root, lambda ws: estimate(scope, ws, req.files))
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, _ui_text('다른 문서 작업이 진행 중입니다.')) from exc
        except TimeoutError as exc:
            raise HTTPException(408, _ui_text('페이지 분석이 60초를 초과했습니다. 파일을 나누거나 더 적은 파일로 다시 시도하세요.')) from exc
        except RuntimeError as exc:
            raise HTTPException(400, _ui_text('페이지 분석을 완료하지 못했습니다. 파일 상태를 확인하세요.')) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(400, _ui_text('파일을 분석하지 못했습니다. 선택한 파일 상태를 확인하세요.')) from exc

    @app.post('/vision/run', status_code=202)
    def vision_run(req: VisionRunReq):
        if not req.consent:
            raise HTTPException(400, _ui_text('파일 전송과 예상 비용에 동의해야 합니다.'))
        scope = _scope(req.folder)
        try:
            return jobs.start_vision(scope.root, req.quote_id, req.files)
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, _ui_text('다른 문서 작업이 진행 중입니다.')) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, _ui_text('파일을 확인할 수 없습니다. 견적을 다시 확인하세요.')) from exc

    @app.post("/index", status_code=202)
    def start_index(req: PrepareReq) -> dict:
        scope = _scope(req.folder)
        try:
            if req.automatic:
                if req.file is not None:
                    raise HTTPException(400, 'automatic preparation does not accept a file')
                return jobs.start(scope.root, automatic=True)
            if req.file is not None:
                from prtfm.indexing.extract import SUPPORTED
                if req.file not in {entry.rel for entry in scope.walk(SUPPORTED)}:
                    raise HTTPException(400, 'file unavailable or outside scope')
                return jobs.start(scope.root, paths={req.file})
            return jobs.start(scope.root)
        except JobRunning as e:
            raise HTTPException(409, "indexing already running") from e

    @app.get("/index/status")
    def index_status(folder: str) -> dict:
        # 폴더가 그새 지워졌어도 작업 상태는 조회되게 Scope 검사 없이 찾는다
        st = jobs.status(Path(folder))
        if st is None:
            raise HTTPException(404, "no indexing job for this folder")
        return st

    @app.post("/index/cancel", status_code=202)
    def cancel_index(req: IndexReq) -> dict:
        if not jobs.cancel(Path(req.folder)):
            raise HTTPException(409, "no running job")
        return {"cancel_requested": True}

    @app.get('/document-mode')
    def document_mode_status(folder: str):
        from prtfm.indexing.document_mode import status

        scope = _scope(folder)
        try:
            ws = open_workspace(scope.root, readonly=True)
        except FileNotFoundError as exc:
            raise HTTPException(404, 'folder is not indexed') from exc
        try:
            ws.store.begin_snapshot()
            return status(ws)
        finally:
            ws.close()

    @app.post('/document-mode/{action}')
    def change_document_mode(action: str, req: IndexReq):
        from prtfm.config import workspace_dir
        from prtfm.indexing import document_mode

        if action not in ('apply', 'rollback'):
            raise HTTPException(404, 'unknown action')
        scope = _scope(req.folder)
        if not (workspace_dir(scope.root) / 'index.db').exists():
            raise HTTPException(404, 'folder is not indexed')
        try:
            return jobs.workspace_operation(scope.root, getattr(document_mode, action))
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, 'workspace busy') from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get('/sessions')
    @app.get('/sessions/{session_id}/turns')
    def saved_conversations(folder: str = '', session_id: str | None = None, before: str | None = None):
        from prtfm.config import workspace_dir
        from prtfm.store.folders import all_sessions, remember_workspace
        if not folder:
            if session_id is not None:
                raise HTTPException(400, 'folder required for session')
            try:
                return all_sessions(before)
            except ValueError as exc:
                raise HTTPException(400, 'invalid history cursor') from exc
        scope = _scope(folder)
        remember_workspace(scope.root)
        path = workspace_dir(scope.root) / 'sessions.db'
        if not path.exists():
            if session_id is None:
                return {'sessions': [], 'limited': False, 'next_cursor': None}
            raise HTTPException(404, 'session not found')
        sessions = SessionStore(path)
        try:
            result = sessions.list_sessions(before) if session_id is None else sessions.saved_turns(session_id, before)
            if result is None:
                raise HTTPException(404, 'session not found in folder')
            return result
        except ValueError as exc:
            raise HTTPException(400, 'invalid history cursor') from exc
        finally:
            sessions.close()

    @app.delete('/sessions/{session_id}')
    def delete_conversation(session_id: str, folder: str):
        from prtfm.config import workspace_dir

        scope = _scope(folder)
        path = workspace_dir(scope.root) / 'sessions.db'
        if not path.exists():
            raise HTTPException(404, 'session not found')
        sessions = SessionStore(path)
        try:
            if not sessions.exists(session_id):
                raise HTTPException(404, 'session not found')
            sessions.delete_session(session_id)
            return {'deleted': True}
        except WriterBusy as exc:
            raise HTTPException(409, 'session is running') from exc
        finally:
            sessions.close()

    @app.get('/sessions/{session_id}/turns/{turn_id}/citations')
    def saved_citations(session_id: str, turn_id: int, folder: str):
        scope = _scope(folder)
        try:
            ws = open_workspace(scope.root, readonly=True)
        except FileNotFoundError as exc:
            raise HTTPException(404, 'folder is not indexed') from exc
        try:
            sessions = SessionStore(ws.dir / 'sessions.db')
            try:
                result = sessions.citations(turn_id, session_id)
                if result['status'] == 'missing':
                    raise HTTPException(404, 'turn not found in session')
                from prtfm.server.citations import index_versions

                result['index_versions'] = index_versions(ws.store, result['citations'])
                return result
            finally:
                sessions.close()
        finally:
            ws.close()

    @app.get('/sessions/{session_id}/runs/{run_id}/events')
    def activity_events(session_id: str, run_id: str, folder: str, after: int = 0,
                        snapshot: int | None = None):
        if after < 0 or (snapshot is not None and snapshot < 1):
            raise HTTPException(400, 'invalid cursor')
        scope = _scope(folder)
        try:
            ws = open_workspace(scope.root, readonly=True)
        except FileNotFoundError as exc:
            raise HTTPException(404, 'folder is not indexed') from exc
        try:
            sessions = SessionStore(ws.dir / 'sessions.db')
            try:
                result = sessions.run_data(session_id, run_id, after, snapshot)
                if result is None:
                    raise HTTPException(404, 'run or snapshot not found')
                return result
            finally:
                sessions.close()
        finally:
            ws.close()

    @app.post("/chat")
    def chat(req: ChatReq) -> StreamingResponse:
        scope = _scope(req.folder)
        if req.semantic:
            try:
                nusd(req.embedding_budget_usd)
                if configured_price() is None:
                    raise ValueError('unknown embedding price')
            except Exception as e:
                raise HTTPException(400, 'semantic search needs verified price and positive budget') from e
        try:
            cancel = jobs.begin_chat()
        except JobRunning as e:
            raise HTTPException(409, 'application is closing') from e
        try:
            ws = open_workspace(scope.root, readonly=True)
        except BaseException as e:
            jobs.end_chat(cancel)
            if isinstance(e, FileNotFoundError):
                raise HTTPException(404, "folder is not indexed") from e
            raise

        def release_chat():
            try:
                ws.close()
            finally:
                jobs.end_chat(cancel)

        try:
            return _ClosingStream(_chat_stream(ws, req, llm_factory, cancel), release_chat,
                                  on_cancel=cancel.set, media_type="text/event-stream")
        except BaseException:
            release_chat()
            raise

    def semantic_operation(folder, operation, queue=False):
        scope = _scope(folder)
        try:
            return jobs.operation(scope.root, operation, queue)
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, 'writer busy') from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/semantic/files/{doc_id}')
    def semantic_toggle(doc_id: int, req: ToggleReq):
        from prtfm.indexing.embedding import toggle
        return semantic_operation(req.folder, lambda store: toggle(store, doc_id, req.enabled), queue=True)

    @app.post('/semantic/files/{doc_id}/delete')
    def semantic_delete(doc_id: int, req: IndexReq):
        from prtfm.indexing.embedding import delete_document_embeddings
        scope = _scope(req.folder)
        try:
            return jobs.workspace_operation(
                scope.root, lambda ws: delete_document_embeddings(ws.store, ws.dir / 'vectors', doc_id))
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, 'writer busy') from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/semantic/estimate')
    def semantic_estimate(req: EstimateReq):
        from prtfm.indexing.embedding import estimate
        from prtfm.indexing.vision import prepare_embedding_chunks
        scope = _scope(req.folder)
        def operation(ws):
            prepare_embedding_chunks(ws, req.doc_ids)
            return estimate(ws.store, req.doc_ids, configured_price())
        try:
            return jobs.workspace_operation(scope.root, operation)
        except (JobRunning, WriterBusy) as exc:
            raise HTTPException(409, 'writer busy') from exc

    @app.post('/semantic/run', status_code=202)
    def semantic_run(req: SemanticRunReq):
        scope = _scope(req.folder)
        try:
            nusd(req.budget_usd)
            price = configured_price()
            if price is None:
                raise ValueError('embedding price unknown')
            return jobs.start_semantic(scope.root, req.quote_id, req.budget_usd, price)
        except JobRunning as exc:
            raise HTTPException(409, 'writer busy') from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/semantic/catalog')
    def semantic_catalog():
        from prtfm.store.folders import semantic_catalog as catalog
        root = Path(os.environ.get('PRTFM_BROWSE_ROOT', str(Path.home())))
        return catalog(root)

    @app.get('/semantic/status')
    def semantic_status(folder: str):
        from prtfm.indexing.embedding import status
        scope = _scope(folder)
        try:
            ws = open_workspace(scope.root, readonly=True)
        except FileNotFoundError as exc:
            raise HTTPException(404, 'folder is not indexed') from exc
        try:
            return {'files': status(ws.store), 'job': jobs.status(scope.root)}
        finally:
            ws.close()

    @app.post('/semantic/cancel', status_code=202)
    def semantic_cancel(req: IndexReq):
        scope = _scope(req.folder)
        st = jobs.status(scope.root)
        if not st or st.get('stage') != 'embedding' or not jobs.cancel(scope.root):
            raise HTTPException(409, 'no running semantic job')
        return {'cancel_requested': True}

    return app


def _chat_stream(ws, req: ChatReq, llm_factory, cancel=None) -> Iterator[str]:
    # ExitStack: 무엇을 열었든 연 것만, 하나가 닫다 실패해도 나머지까지 닫는다
    with contextlib.ExitStack() as stack:
        stack.callback(ws.close)
        activity = None
        try:
            from prtfm.store.folders import remember_workspace
            remember_workspace(req.folder)
            ws.store.begin_snapshot()
            sessions = SessionStore(ws.dir / "sessions.db")
            stack.callback(sessions.close)
            sid = req.session_id if req.session_id and sessions.exists(req.session_id) else sessions.new_session()
            sessions.ensure_title(sid, req.question)
            yield _sse("session", {"session_id": sid})
            document_mode = req.mode == 'agentic'
            if document_mode:
                from prtfm.query.activity import Activity
                activity = Activity(sessions, sid)
                activity.emit('run_started')
                yield _sse('activity', {'run_id': activity.run_id})
            if cancel is not None and cancel.is_set():
                return
            semantic = semantic_for_request(ws, req, cancel)
            if semantic is not None:
                stack.callback(semantic.close)
            if document_mode:
                from prtfm.query.document_agent import run_document_agent
                from prtfm.query.file_commands import open_file_commands
                from prtfm.query.sources import FolderSourceReader
                reader = FolderSourceReader(ws.store, ws.cache, _scope(req.folder))
                tools = stack.enter_context(open_file_commands(reader=reader))
                tools.semantic = semantic
            else:
                tools = QueryTools(ws.store, semantic)
            history = sessions.history(sid, HISTORY_TURNS)
            llm = llm_factory()  # 키가 없으면 LLMError → error 이벤트
            if activity and hasattr(llm, 'activity'):
                llm.activity = activity
            close_llm = getattr(llm, "close", None)
            if close_llm is not None:
                stack.callback(close_llm)
            events = (run_document_agent(llm, tools, req.question, history, cancel, activity,
                                         prior_evidence=sessions.history_evidence(sid, HISTORY_TURNS)) if document_mode
                      else run_agent(llm, tools, req.question, history))
            stack.callback(events.close)
            for ev in events:
                if cancel is not None and cancel.is_set():
                    return
                if isinstance(ev, ToolStart):
                    yield _sse("tool_start", {"name": ev.name, "args": {k: str(v)[:200] for k, v in ev.args.items()}})
                elif isinstance(ev, ToolEnd):
                    yield _sse("tool_end", {"name": ev.name})
                elif isinstance(ev, Delta):
                    yield _sse("delta", {"text": ev.text})
                elif isinstance(ev, Done):
                    if document_mode:
                        citations = [tools.resolve_citation(cid) for cid in ev.cited]
                        invalid = []
                    else:
                        citations = [tools.read_snapshots[cid] for cid in ev.cited if cid in tools.read_snapshots]
                        invalid = [cid for cid in ev.cited if cid not in tools.read_snapshots]
                    costs = [cost_usd(llm.model, u) for u in ev.calls]
                    embedding = semantic.summary() if semantic else {'cost_usd': 0, 'unknown_reserved_usd': 0, 'warnings': []}
                    if document_mode:
                        sessions.add_turn(sid, req.question, ev.answer, llm.model, ev.calls,
                                          embedding=embedding, range_citations=citations,
                                          run_id=activity.run_id if activity else None)
                    else:
                        sessions.add_turn(sid, req.question, ev.answer, llm.model, ev.calls, citations, embedding)
                    cost = (sum(costs) + embedding['cost_usd']
                            if all(c is not None for c in costs) and not embedding['unknown_reserved_usd'] else None)
                    if activity:
                        activity.emit('done', {'answer': ev.answer, 'citations': citations}, cost_usd=cost)
                    yield _sse("done", {"answer": ev.answer, "citations": citations,
                                        "cost_usd": round(cost, 6) if cost is not None else None, 'embedding': embedding,
                                        "forced": ev.forced, "invalid_citations": invalid,
                                        "rejected_citations": ev.rejected_citations,
                                        "automatic_reads": ev.automatic_reads, "reviewed": ev.reviewed})
        except CommandUnavailable as e:
            if activity:
                activity.emit('error', error='command_environment_missing')
            yield _sse('error', {'message': _ui_text('파일 검색 실행 환경을 준비해야 합니다: ') + str(e)})
        except InputBudgetExceeded:
            if activity:
                activity.emit('error', error='input_budget_exhausted')
            yield _sse('error', {'message': _ui_text('이번 질문의 입력 한도가 부족하거나 사용량을 확인할 수 없어 중단했습니다. 질문 범위를 좁혀 다시 시도해 주세요.')})
        except LLMError as e:
            if activity:
                activity.emit('error', error=type(e).__name__)
            yield _sse("error", {"message": f"LLM call failed: {e}"})
        except Exception as e:  # 원문이 담길 수 있는 메시지·트레이스백 대신 타입 이름만 알린다
            if activity:
                activity.emit('error', error=type(e).__name__)
            yield _sse("error", {"message": f"internal error: {type(e).__name__}"})
        finally:
            if activity:
                activity.emit('cancelled')
