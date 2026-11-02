from __future__ import annotations

import json
from dataclasses import dataclass

from prtfm.indexing.textnorm import normalize
from prtfm.indexing.tokenize import tokenize_query
from prtfm.store.db import Store

SNIPPET_CHARS = 300
MAX_K = 50


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    doc_id: int
    idx: int
    doc_path: str
    page: int | None
    heading_trail: list[str]
    snippet: str


def _quote(token: str) -> str:
    # 토큰을 큰따옴표로 감싸 FTS5 문법(AND·*·괄호 등)으로 해석되지 않게 한다
    return '"' + token.replace('"', '""') + '"'


def search(store: Store, query: str, k: int = 50) -> list[Hit]:
    if k <= 0:
        return []
    k = min(k, MAX_K)
    # 색인 쪽과 같은 정규화(NFKC 등)를 거쳐야 전각 영숫자·반각 가나 질의가 맞는다
    tokens = tokenize_query(normalize(query))
    if not tokens:
        return []
    rows = store.match(" OR ".join(_quote(t) for t in tokens), k)
    return [Hit(f"{r['doc_id']}@{r['revision']}#{r['idx']}", r["doc_id"], r["idx"], r["path"], r["page"],
                json.loads(r["heading_trail"]), r["text"][:SNIPPET_CHARS]) for r in rows]
