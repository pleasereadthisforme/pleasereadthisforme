from __future__ import annotations

import fnmatch
import json
import logging
import re
import time

import regex

from prtfm.retrieval import lexical
from prtfm.store.db import Store

SEARCH_K, SNIPPET = 8, 300
MAX_NEIGHBORS = 2
GREP_MAX_HITS, GREP_MAX_PATTERN, GREP_TIMEOUT_S = 20, 200, 1.0
GLOB_MAX = 100
# 인자 형식·값이 틀렸을 때 나는 예외(바인딩 실패 TypeError, int() 변환 ValueError·OverflowError, 비문자열 AttributeError)
_ARG_ERRORS = (TypeError, ValueError, OverflowError, AttributeError)

log = logging.getLogger("prtfm.query")

TOOL_SPECS = [
    {"type": "function", "name": "search",
     "description": "Search indexed document chunks. Returns top matches with chunk id, document, page, heading and a snippet.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                    "additionalProperties": False}},
    {"type": "function", "name": "read",
     "description": "Read the full text of a chunk, optionally with neighboring chunks (0-2 on each side).",
     "parameters": {"type": "object", "properties": {"chunk_id": {"type": "string"},
                                                     "neighbors": {"type": "integer", "minimum": 0, "maximum": 2}},
                    "required": ["chunk_id", "neighbors"], "additionalProperties": False}},
    {"type": "function", "name": "grep",
     "description": "Case-insensitive regular expression search over all chunk texts. Returns up to 20 matching lines with chunk ids.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"],
                    "additionalProperties": False}},
    {"type": "function", "name": "glob",
     "description": "Find documents whose file path matches a glob pattern (case-insensitive, e.g. '*report*'). "
                    "Use '*' to list every document. Returns paths with chunk counts.",
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"],
                    "additionalProperties": False}},
]

_TAG = re.compile(r"<\s*/?\s*tool_result[^>]*>", re.I)


def fence(payload) -> str:
    """도구 결과를 고정 태그로 감싼다. 결과 속 같은 태그는 무력화해 문서 내용이 지시처럼 읽히지 않게 한다."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return f"<tool_result>\n{_TAG.sub('[removed]', text)}\n</tool_result>"


def _parse_chunk_id(chunk_id: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"([0-9]+)@([0-9]+)#([0-9]+)", chunk_id)
    if m is None:
        return None
    return tuple(int(v) for v in m.groups())


class QueryTools:
    """agent가 부르는 도구. 파일 시스템이 아니라 이 폴더의 색인만 읽는다."""

    def __init__(self, store: Store, semantic=None):
        self.store = store
        self.semantic = semantic
        self.read_ids: set[str] = set()
        self.read_snapshots: dict[str, dict] = {}
        self.read_candidates: list[str] = []

    def _remember(self, hits):
        for hit in hits[:2]:
            cid = hit['chunk_id']
            if cid not in self.read_candidates:
                self.read_candidates.append(cid)
        self.read_candidates = self.read_candidates[-24:]

    def search(self, query: str) -> list[dict]:
        hits = lexical.search(self.store, query, SEARCH_K)
        if self.semantic is not None:
            hits = self.semantic.search(query, hits)
        self._remember([{'chunk_id': h.chunk_id} for h in hits])
        return [{"chunk_id": h.chunk_id, "doc": h.doc_path, "page": h.page, "heading": " > ".join(h.heading_trail),
                 "snippet": h.snippet[:SNIPPET]} for h in hits]

    def read(self, chunk_id: str, neighbors: int = 0) -> list[dict] | str:
        parsed = _parse_chunk_id(chunk_id)
        if parsed is None:
            return f"unknown chunk_id: {chunk_id}"
        doc_id, revision, idx = parsed
        meta = self.store.chunk_meta(doc_id, idx)
        if meta is None or meta["revision"] != revision:
            return f"unknown chunk_id: {chunk_id}"
        n = max(0, min(int(neighbors), MAX_NEIGHBORS))
        out = []
        for r in self.store.chunk_window(doc_id, idx - n, idx + n):
            cid = f"{doc_id}@{revision}#{r['idx']}"
            self.read_ids.add(cid)
            meta = self.store.chunk_meta(doc_id, r["idx"])
            self.read_snapshots[cid] = {
                "chunk_id": cid, "doc": meta["path"], "content_hash": meta["content_hash"],
                "revision": revision, "text": r["text"], "page": meta["page"],
                "page_end": meta["page_end"], "heading": " > ".join(json.loads(meta["heading_trail"])),
                "provenance": json.loads(meta["spans"]),
            }
            links = []
            for span in self.read_snapshots[cid]['provenance']:
                for link in span.get('table_caption_candidates', []):
                    links.append({k: v for k, v in link.items() if k != 'caption_chunk_indices'} |
                                 {'caption_chunk_ids': [f'{doc_id}@{revision}#{i}'
                                                        for i in link['caption_chunk_indices']]})
            self.read_snapshots[cid]['table_caption_candidates'] = links
            out.append({"chunk_id": cid, "text": r["text"], "table_caption_candidates": links})
        return out

    def grep(self, pattern: str) -> dict | str:
        if len(pattern) > GREP_MAX_PATTERN:
            return f"pattern too long (max {GREP_MAX_PATTERN} chars)"
        try:
            rx = regex.compile(pattern, regex.IGNORECASE)
        except regex.error as e:
            return f"invalid regex: {e}"
        deadline = time.monotonic() + GREP_TIMEOUT_S
        hits: list[dict] = []
        # 중간에 멈춰도 커서를 바로 닫는다 — 열린 커서의 읽기 스냅샷이 writer의 WAL 체크포인트를 막는다
        chunks = self.store.iter_chunks()
        try:
            for r in chunks:
                for line in r["text"].splitlines():
                    remaining = deadline - time.monotonic()
                    try:
                        if remaining <= 0:
                            raise TimeoutError
                        found = rx.search(line, timeout=remaining)
                    except TimeoutError:
                        return {"hits": hits, "note": "stopped: time limit"}
                    if found:
                        hits.append({"chunk_id": f"{r['doc_id']}@{r['revision']}#{r['idx']}",
                                     "doc": r["path"], "line": line[:200]})
                        self._remember(hits)
                        break
                if len(hits) >= GREP_MAX_HITS:
                    break
        finally:
            chunks.close()
        return {"hits": hits}

    def glob(self, pattern: str) -> dict:
        p = pattern.lower()
        docs = [{"doc": path, "chunks": n} for path, n in self.store.doc_chunk_counts()
                if fnmatch.fnmatch(path.lower(), p)]
        if len(docs) > GLOB_MAX:
            return {"docs": docs[:GLOB_MAX], "total": len(docs)}
        return {"docs": docs}

    def call(self, name: str, args: dict) -> str:
        fn = {"search": self.search, "read": self.read, "grep": self.grep, "glob": self.glob}.get(name)
        if fn is None:
            return fence(f"unknown tool: {name}")
        # 어느 쪽이든 루프를 끝내지 않고 모델에 돌려준다
        try:
            return fence(fn(**args))
        except _ARG_ERRORS as e:  # LLM이 만든 인자는 무엇이든 올 수 있다
            return fence(f"bad arguments for {name}: {type(e).__name__}")
        except Exception as e:  # 색인 DB 오류 등 — 메시지·인자에 원문이 담길 수 있어 타입 이름만 남긴다
            log.warning("tool %s failed: %s", name, type(e).__name__)
            return fence(f"tool failed for {name}: {type(e).__name__}")
