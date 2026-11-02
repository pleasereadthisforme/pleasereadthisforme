# Import boundaries are intentional: diagnose cold startup without logging credentials.
# ruff: noqa: E402
from __future__ import annotations

import time

_startup_begin = time.perf_counter()
_startup_marks = {}


def _startup_mark(stage):
    _startup_marks[stage] = round((time.perf_counter() - _startup_begin) * 1000, 3)


import argparse
import json
import multiprocessing
import os
import secrets
import socket
import sys
import threading
from collections.abc import Callable
from pathlib import Path

_startup_mark("stdlib")

import uvicorn

_startup_mark("uvicorn")

from prtfm.llm.openai_client import LLMError, ResponsesClient

_startup_mark("llm-client")
from prtfm.server.app import DEFAULT_ORIGINS, create_app

_startup_mark("server-app")
from prtfm.server.jobs import IndexJobs

_startup_mark("server-jobs")


def bind_localhost() -> socket.socket:
    """127.0.0.1의 빈 포트를 OS에 받아 듣기 상태의 소켓을 미리 연다(포트 경쟁 없이 Tauri에 알려 주기 위해)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    # 첫 줄을 읽자마자 붙는 연결이 거부되지 않고 uvicorn이 뜰 때까지 대기열에 쌓이게 한다
    s.listen(128)
    return s


def make_llm_factory(model: str) -> Callable[[], object]:
    def factory():
        from prtfm.credentials.provider import get_key
        key = get_key()
        if not key:
            raise LLMError("API key not configured")
        return ResponsesClient(key, model)

    return factory


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="prtfm-server")
    ap.add_argument("--allow-origin", action="append", default=[], help="허용할 Origin 추가(개발용)")
    ap.add_argument("--model", default=os.environ.get("PRTFM_MODEL", "gpt-6-luna"))
    ap.add_argument("--browser", action="store_true", help="일회용 브라우저 연결 코드 제공")
    ap.add_argument("--hosted", action="store_true", help="네이티브 호스트의 stdin 수명에 연결")
    ap.add_argument("--storage-channel", choices=["preview", "stable", "development"])
    a = ap.parse_args(argv)
    if a.hosted and sys.stdin.readline() != "go\n":
        return 2

    _startup_mark("start-gate")
    if a.hosted and sys.platform == "win32":
        from prtfm.credentials.hosted import configure_hosted_key
        from prtfm.credentials.windows import CredentialError
        try:
            configure_hosted_key(a.storage_channel or 'stable')
        except CredentialError:
            print(json.dumps({'error': 'credential_storage_unavailable'}), flush=True)
            return 2

        # GUI-hosted workers must not allocate a console. In the native host,
        # console-python workers can stall before Python initialization.
        worker = Path(sys.executable).with_name("pythonw.exe")
        if not worker.is_file():
            ap.error("bundled pythonw.exe is required for Windows hosted workers")
        multiprocessing.set_executable(str(worker))

    from prtfm.credentials.provider import configure_development
    configure_development()
    _startup_mark("credentials")
    if a.storage_channel is None:
        return _serve(a)
    from prtfm.store.lifecycle import StorageBlocked, storage_session
    _startup_mark("lifecycle-import")
    try:
        with storage_session(a.storage_channel):
            _startup_mark("storage-session")
            return _serve(a)
    except StorageBlocked as exc:
        from prtfm.store.preflight import blocked_notice
        if a.hosted:
            print(json.dumps({'error': 'storage_blocked', 'reason': str(exc)}), flush=True)
        print(f"storage blocked: {exc}: {blocked_notice(str(exc))}", file=sys.stderr)
        return 2


def _serve(a):
    token = secrets.token_urlsafe(32)
    sock = bind_localhost()
    # 실행기가 읽는 첫 줄: 기본은 토큰, 브라우저 모드는 일회용 연결 코드
    connect_code = secrets.token_urlsafe(32) if a.browser else None
    handshake = {"port": sock.getsockname()[1]}
    handshake['connect_code' if a.browser else 'token'] = connect_code if a.browser else token
    _startup_mark("handshake")
    if a.hosted and os.environ.get("PRTFM_STARTUP_TIMING") == "1":
        handshake["startup_ms"] = _startup_marks
    print(json.dumps(handshake), flush=True)
    try:
        return _run_server(a, token, sock, connect_code)
    finally:
        sock.close()


def _run_server(a, token, sock, connect_code):
    app = create_app(token, DEFAULT_ORIGINS | frozenset(a.allow_origin), make_llm_factory(a.model), IndexJobs(), connect_code=connect_code)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False))
    if a.hosted:
        def watch_host():
            # Host owns stdin. EOF (including a crashed host) requests shutdown.
            while sys.stdin.read(1):
                pass
            server.should_exit = True
        threading.Thread(target=watch_host, name="prtfm-host-watch", daemon=True).start()
    server.run(sockets=[sock])
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
