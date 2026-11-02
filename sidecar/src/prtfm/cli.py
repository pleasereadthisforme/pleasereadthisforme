from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prtfm.indexing.pass1 import run_pass1, workspace_chunk_policy
from prtfm.indexing.scope import Scope, ScopeError
from prtfm.store.workspace import open_workspace


def main(argv: list[str] | None = None) -> int:
    from prtfm.credentials.provider import configure_development
    configure_development()
    ap = argparse.ArgumentParser(prog="prtfm")
    ap.add_argument("--storage-channel", choices=["preview", "stable", "development"], help="채널 보호 모드; 기존 무표식 저장소는 자동 승인하지 않음")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_idx = sub.add_parser("index", help="폴더를 1차 패스로 색인")
    p_idx.add_argument("folder", type=Path)
    p_idx.add_argument("--workers", type=int, default=None)
    p_idx.add_argument("--refine", action="store_true", help="PDF 2차 패스까지 실행")
    p_s = sub.add_parser("search", help="실제 rg로 추출 원문 검색")
    p_s.add_argument("folder", type=Path)
    p_s.add_argument("query")
    p_sem = sub.add_parser('semantic', help='선택적 의미 검색 설정·견적·실행')
    p_sem.add_argument('folder', type=Path)
    actions = p_sem.add_subparsers(dest='action', required=True)
    actions.add_parser('status')
    p_toggle = actions.add_parser('toggle')
    p_toggle.add_argument('doc_id', type=int)
    p_toggle.add_argument('enabled', choices=['on', 'off'])
    p_est = actions.add_parser('estimate')
    p_est.add_argument('doc_ids', type=int, nargs='+')
    p_run = actions.add_parser('run')
    p_run.add_argument('quote_id')
    p_run.add_argument('--budget-usd', required=True)
    p_mode = sub.add_parser('document-mode', help='기존 폴더 문서 탐색 전환·상태·되돌리기')
    p_mode.add_argument('folder', type=Path)
    p_mode.add_argument('action', choices=['status', 'apply', 'rollback'])
    p_original = sub.add_parser('original-text', help='기존 텍스트 본문 사본 검증·정리')
    p_original.add_argument('folder', type=Path)
    p_original.add_argument('--apply', action='store_true', help='검증된 사본만 정리; 기본은 확인만')
    p_blocks = sub.add_parser('compact-blocks', help='변환 metadata 중복 본문 검증·정리')
    p_blocks.add_argument('folder', type=Path)
    p_blocks.add_argument('--apply', action='store_true')
    p_audit = sub.add_parser('storage-audit', help='저장소·공유 벡터·세대 읽기 전용 진단; 삭제/복구하지 않음')
    p_audit.add_argument('--plan', action='store_true', help='보존·추가 확인·복구 검토 미리보기 포함; 실행하지 않음')
    p_audit.add_argument('--check-bodies', action='store_true', help='활성 변환 본문 checksum·페이지·블록 검사; 크기 제한 있음')
    p_audit.add_argument('--check-vectors', action='store_true', help='완료된 공유 벡터 내용·청크 정보 검사; 제한 있음')
    p_prune = sub.add_parser('storage-prune-refs', help='한 폴더의 미사용 공유 참조만 정리; 벡터 파일 보존')
    p_prune.add_argument('folder', type=Path)
    p_prune.add_argument('--apply', action='store_true', help='잠금 아래 다시 확인 후 참조만 제거; 기본 미리보기')
    p_preflight = sub.add_parser('storage-preflight', help='저장소 위치·채널 사전 검사; 생성/변경하지 않음')
    p_preflight.add_argument('--inspect-source', choices=['target', 'previous'], help='선택 위치의 DB형식·내부참조 관찰 검사; 가져오기 승인 아님')
    p_preflight.add_argument('--review-legacy', action='store_true', help='무표식 원본의 형식·본문·벡터 읽기 전용 검토; 실행 승인 아님')
    p_preflight.add_argument('--locked', action='store_true', help='표식 있는 소스의 협력 writer 잠금과 전후 inventory 검사; 부모에 작은 lock 파일 유지')
    p_preflight.add_argument('--check-payloads', action='store_true', help='--locked 검사 안에서 활성 본문·완료 공유 벡터 검증')
    p_preflight.add_argument('--first-run', action='store_true', help='첫 실행 선택 가능 여부 읽기 전용 조회; 초기화/가져오기 실행 안 함')
    a = ap.parse_args(argv)
    if a.cmd == 'storage-preflight' and a.review_legacy and (not a.inspect_source or a.locked or a.check_payloads):
        ap.error('--review-legacy requires --inspect-source and cannot combine with --locked/--check-payloads')
    if a.cmd == 'storage-preflight' and a.check_payloads and not a.locked:
        ap.error('--check-payloads requires --locked')
    if a.cmd == 'storage-preflight' and a.locked and not a.inspect_source:
        ap.error('--locked requires --inspect-source')
    if a.cmd == 'storage-preflight':
        from prtfm.store.preflight import storage_preflight
        report = storage_preflight(a.storage_channel or 'development')
        if a.first_run:
            from prtfm.store.first_run import first_run_plan
            report['first_run'] = first_run_plan(a.storage_channel or 'development')
        if a.inspect_source:
            from prtfm.store.import_inspection import inspect_import_source
            path = report['path'] if a.inspect_source == 'target' else report['previous_location']['path']
            if a.review_legacy:
                from prtfm.store.legacy_review import review_legacy_source
                report['source_inspection'] = review_legacy_source(Path(path))
            elif a.locked:
                from prtfm.store.locked_inspection import inspect_locked_source
                report['read_only'] = False  # The lifecycle gate may create a sibling lock file.
                report['read_only_data'] = True
                report['source_inspection'] = inspect_locked_source(Path(path), check_payloads=a.check_payloads)
            else:
                report['source_inspection'] = inspect_import_source(Path(path))
            if report['source_inspection']['status'] != 'observed':
                print(json.dumps(report, ensure_ascii=False, indent=2))
                return 2
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['required_action'] in ('ready', 'initialization_required') else 2
    if a.storage_channel is None or a.cmd == 'storage-audit':
        # Audit remains read-only and must not create ownership or lock files.
        if a.storage_channel is not None:
            import os

            from prtfm.config import resolve_storage_location
            previous = os.environ.get('PRTFM_DATA_DIR')
            os.environ['PRTFM_DATA_DIR'] = str(resolve_storage_location(a.storage_channel).path)
            try:
                return _execute(a)
            finally:
                if previous is None:
                    os.environ.pop('PRTFM_DATA_DIR', None)
                else:
                    os.environ['PRTFM_DATA_DIR'] = previous
        return _execute(a)
    from prtfm.store.lifecycle import StorageBlocked, storage_session
    try:
        with storage_session(a.storage_channel):
            return _execute(a)
    except StorageBlocked as exc:
        from prtfm.store.preflight import blocked_notice
        print(f"storage blocked: {exc}: {blocked_notice(str(exc))}", file=sys.stderr)
        return 2


def _execute(a):
    if a.cmd == 'storage-prune-refs':
        from prtfm.store.reference_cleanup import prune_references

        report = prune_references(a.folder, apply=a.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report['state'] == 'blocked' else 0

    if a.cmd == 'storage-audit':
        from prtfm.store.audit import audit_storage

        report = audit_storage()
        if a.check_bodies:
            from prtfm.store.body_audit import inspect_bodies

            inspect_bodies(report)
        if a.check_vectors:
            from prtfm.store.vector_audit import inspect_vectors

            inspect_vectors(report)
        if a.plan:
            from prtfm.store.maintenance_plan import preview

            report['plan'] = preview(report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1 if report['findings'] else 0


    try:
        scope = Scope(a.folder)
    except ScopeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.cmd == 'document-mode':
        from prtfm.config import workspace_dir

        if not (workspace_dir(scope.root) / 'index.db').exists():
            print('error: folder is not indexed', file=sys.stderr)
            return 2
    try:
        ws = open_workspace(scope.root, readonly=a.cmd == "search" or (a.cmd in ("original-text", "compact-blocks") and not a.apply) or
                            (a.cmd in ('semantic', 'document-mode') and a.action == 'status'))
    except FileNotFoundError:
        print("error: folder is not indexed", file=sys.stderr)
        return 2
    try:
        if a.cmd == 'compact-blocks':
            from prtfm.indexing.block_transition import transition

            print(json.dumps(transition(ws, apply=a.apply), ensure_ascii=False, indent=2))
        elif a.cmd == 'original-text':
            from prtfm.indexing.original_transition import transition

            print(json.dumps(transition(scope, ws, apply=a.apply), ensure_ascii=False, indent=2))
        elif a.cmd == 'document-mode':
            from prtfm.indexing import document_mode

            print(json.dumps(getattr(document_mode, a.action)(ws), ensure_ascii=False, indent=2))
        elif a.cmd == "index":
            rep = run_pass1(scope, ws.store, ws.cache, workers=a.workers,
                            chunk_policy=workspace_chunk_policy(ws.store))
            print(f"files {rep.files} | new {rep.new} changed {rep.changed} unchanged {rep.unchanged} "
                  f"deleted {rep.deleted} | pages {rep.pages} chunks {rep.chunks} | {rep.seconds}s")
            for w in rep.warnings:
                print(f"warning: {w}")
            for rel, why in rep.skipped + rep.errors:
                print(f"not indexed: {rel} ({why})")
            if a.refine:
                from prtfm.indexing.pass2 import run_pass2

                print(run_pass2(scope, ws.store, ws.cache))
        elif a.cmd == 'semantic':
            import threading

            from prtfm.indexing.embedding import estimate, run, status, toggle
            from prtfm.llm.embeddings import configured_price

            if a.action == 'status':
                result = status(ws.store)
            elif a.action == 'toggle':
                result = toggle(ws.store, a.doc_id, a.enabled == 'on')
            elif a.action == 'estimate':
                result = estimate(ws.store, a.doc_ids, configured_price())
            else:
                import signal

                cancel = threading.Event()
                previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
                try:
                    result = run(ws.store, ws.dir / 'vectors', a.quote_id, a.budget_usd,
                                 configured_price(), cancel)
                finally:
                    signal.signal(signal.SIGINT, previous)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if isinstance(result, dict) and result.get('state') in ('error', 'cancelled'):
                return 1
        else:
            from prtfm.query.file_commands import open_file_commands
            from prtfm.query.sources import FolderSourceReader

            with open_file_commands(reader=FolderSourceReader(ws.store, ws.cache, scope)) as tools:
                args = {'query': a.query, 'context': 0}
                result = tools.execute('rg', args, tools.prepare('rg', args))
                print(result['stdout'], end='')
                unavailable = result.get('unavailable_sources', {})
                if unavailable:
                    print('search partial/unavailable:', file=sys.stderr)
                    for path, reason in sorted(unavailable.items()):
                        print(f'{path}: {reason}', file=sys.stderr)
                    return 1
                if result['status'] not in ('complete', 'no_match'):
                    print(f"search {result['status']}: {result['stderr']}", file=sys.stderr)
                    return 1
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    finally:
        ws.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
