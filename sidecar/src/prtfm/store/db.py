from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from prtfm.store.migrations import migrate, migrate_job_quote, migrate_quote_created, migrate_semantic

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT UNIQUE NOT NULL,
  size INTEGER NOT NULL,
  mtime_ns INTEGER NOT NULL,
  content_hash TEXT,
  parse_stage INTEGER NOT NULL DEFAULT 0,
  embedded INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  error TEXT,
  pages INTEGER
);
CREATE TABLE IF NOT EXISTS chunks(
  rid INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES docs(id),
  idx INTEGER NOT NULL,
  page INTEGER,
  heading_trail TEXT NOT NULL,
  text TEXT NOT NULL,
  UNIQUE(doc_id, idx)
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(tok, tokenize='unicode61 remove_diacritics 0');
"""


@dataclass(frozen=True)
class DocRow:
    id: int
    path: str
    size: int
    mtime_ns: int
    content_hash: str | None
    parse_stage: int
    embedded: bool
    status: str
    error: str | None
    pages: int | None
    revision: int = 0
    repair_required: bool = False


@dataclass(frozen=True)
class ChunkRow:
    idx: int
    page: int | None
    heading_trail: list[str]
    text: str
    tok: str
    page_end: int | None = None
    spans: list[dict] = field(default_factory=list)


class Store:
    """폴더 하나의 매니페스트·청크·어휘 색인. 쓰기는 한 프로세스에서만 한다."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(db_path)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        migrate(self.con, db_path)
        migrate_semantic(self.con, db_path)
        migrate_quote_created(self.con)
        migrate_job_quote(self.con)

    @classmethod
    def open_readonly(cls, db_path: Path) -> Store:
        """질의 쪽 읽기 전용 연결. 색인 writer와 다른 스레드에서 쓴다."""
        if not db_path.exists():
            raise FileNotFoundError(db_path)
        self = cls.__new__(cls)
        self.con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        return self

    def close(self) -> None:
        self.con.close()

    def begin_snapshot(self) -> None:
        self.con.execute("BEGIN")
        self.con.execute("SELECT COUNT(*) FROM docs").fetchone()

    def revision(self, doc_id: int) -> int:
        row = self.con.execute("SELECT revision FROM docs WHERE id=?", (doc_id,)).fetchone()
        return row[0] if row else 0

    def get_pipeline_version(self) -> int:
        """색인을 만든 처리 파이프라인 버전(없으면 0)."""
        return self.con.execute("SELECT value FROM app_meta WHERE key='pipeline_version'").fetchone()[0]

    def set_pipeline_version(self, version: int) -> None:
        with self.con:
            self.con.execute("UPDATE app_meta SET value=? WHERE key='pipeline_version'", (version,))

    def docs(self) -> dict[str, DocRow]:
        out = {}
        for r in self.con.execute("SELECT * FROM docs"):
            out[r["path"]] = DocRow(r["id"], r["path"], r["size"], r["mtime_ns"], r["content_hash"],
                                    r["parse_stage"], bool(r["embedded"]), r["status"], r["error"], r["pages"],
                                    r["revision"], bool(r["repair_required"]))
        return out

    def upsert_doc(self, path: str, size: int, mtime_ns: int, content_hash: str | None, status: str,
                   error: str | None = None, pages: int | None = None) -> int:
        with self.con:
            row = self.con.execute(
                "INSERT INTO docs(path, size, mtime_ns, content_hash, status, error, pages) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime_ns=excluded.mtime_ns, "
                "content_hash=excluded.content_hash, status=excluded.status, error=excluded.error, "
                "pages=excluded.pages RETURNING id",
                (path, size, mtime_ns, content_hash, status, error, pages)).fetchone()
        return row[0]

    def write_doc(self, path: str, size: int, mtime_ns: int, content_hash: str | None, status: str,
                  error: str | None, pages: int | None, rows: list[ChunkRow] | None, parse_stage: int,
                  before_commit: Callable[[int], None] | None = None) -> int:
        """문서 행과 청크 교체(rows=None이면 청크 비움, parse_stage 0)를 한 트랜잭션으로 쓴다.
        before_commit(doc_id)은 커밋 전에 실행되며, 실패하면 아무것도 커밋되지 않는다."""
        with self.con:
            doc_id = self.con.execute(
                "INSERT INTO docs(path, size, mtime_ns, content_hash, status, error, pages) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime_ns=excluded.mtime_ns, "
                "content_hash=excluded.content_hash, status=excluded.status, error=excluded.error, "
                "pages=excluded.pages RETURNING id",
                (path, size, mtime_ns, content_hash, status, error, pages)).fetchone()[0]
            self._delete_chunks(doc_id)
            for r in rows or []:
                self._insert_chunk(doc_id, r)
            self.con.execute("UPDATE docs SET revision=revision+?, embedded=0, repair_required=0, parse_stage=? WHERE id=?",
                             (int(rows is not None), parse_stage if rows is not None else 0, doc_id))
            if before_commit is not None:
                before_commit(doc_id)
        return doc_id

    def _insert_chunk(self, doc_id: int, r: ChunkRow) -> None:
        cur = self.con.execute(
            "INSERT INTO chunks(doc_id,idx,page,heading_trail,text,page_end,spans) VALUES (?,?,?,?,?,?,?)",
            (doc_id, r.idx, r.page, json.dumps(r.heading_trail, ensure_ascii=False), r.text,
             r.page_end if r.page_end is not None else r.page, json.dumps(r.spans)))
        self.con.execute("INSERT INTO fts(rowid,tok) VALUES (?,?)", (cur.lastrowid, r.tok))

    def touch_doc(self, doc_id: int, size: int, mtime_ns: int) -> None:
        with self.con:
            self.con.execute("UPDATE docs SET size=?, mtime_ns=? WHERE id=?", (size, mtime_ns, doc_id))

    def _delete_chunks(self, doc_id: int) -> None:
        self.con.execute("DELETE FROM fts WHERE rowid IN (SELECT rid FROM chunks WHERE doc_id=?)", (doc_id,))
        self.con.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

    def replace_chunks(self, doc_id: int, rows: list[ChunkRow], parse_stage: int) -> None:
        with self.con:
            self._delete_chunks(doc_id)
            for r in rows:
                self._insert_chunk(doc_id, r)
            self.con.execute("UPDATE docs SET revision=revision+1,embedded=0,parse_stage=? WHERE id=?", (parse_stage, doc_id))

    def clear_chunks(self, doc_id: int) -> None:
        with self.con:
            self._delete_chunks(doc_id)
            self.con.execute("UPDATE docs SET parse_stage=0,embedded=0 WHERE id=?", (doc_id,))

    def delete_doc(self, doc_id: int) -> None:
        with self.con:
            self._delete_chunks(doc_id)
            self.con.execute("DELETE FROM docs WHERE id=?", (doc_id,))

    def match(self, fts_expr: str, k: int) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT c.doc_id, c.idx, d.revision, d.path, c.page, c.heading_trail, c.text FROM fts "
            "JOIN chunks c ON c.rid = fts.rowid JOIN docs d ON d.id = c.doc_id "
            "WHERE fts MATCH ? AND d.status='ok' AND d.error IS NULL ORDER BY bm25(fts) LIMIT ?", (fts_expr, k)).fetchall()

    def chunk_count(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]

    def chunk_window(self, doc_id: int, lo: int, hi: int) -> list[sqlite3.Row]:
        return self.con.execute(
            "SELECT idx, text FROM chunks WHERE doc_id=? AND idx BETWEEN ? AND ? ORDER BY idx",
            (doc_id, lo, hi)).fetchall()

    def iter_chunks(self) -> Iterator[sqlite3.Row]:
        yield from self.con.execute(
            "SELECT c.doc_id, c.idx, d.revision, d.path, c.text FROM chunks c JOIN docs d ON d.id = c.doc_id "
            "WHERE d.status='ok' AND d.error IS NULL ORDER BY d.path, c.idx")

    def doc_chunk_counts(self) -> list[tuple[str, int]]:
        return [(r[0], r[1]) for r in self.con.execute(
            "SELECT d.path, COUNT(c.rid) FROM docs d JOIN chunks c ON c.doc_id = d.id "
            "WHERE d.status='ok' AND d.error IS NULL GROUP BY d.id ORDER BY d.path")]

    def chunk_meta(self, doc_id: int, idx: int) -> sqlite3.Row | None:
        return self.con.execute(
            "SELECT d.path,d.revision,d.content_hash,c.page,c.page_end,c.spans,c.heading_trail,c.text "
            "FROM chunks c JOIN docs d ON d.id = c.doc_id "
            "WHERE c.doc_id=? AND c.idx=?", (doc_id, idx)).fetchone()
