from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from prtfm.llm.usage import Usage, cost_usd
from prtfm.store.locking import WriterBusy, acquire

SNAPSHOT_BYTES = 1_048_576
RUN_BYTES = 16_777_216
WORKSPACE_BYTES = 134_217_728
RUN_EVENTS = 1000

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, session_id TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_events(
 run_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id,seq));
CREATE TABLE IF NOT EXISTS run_snapshots(
 run_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id,seq));
CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS turns(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, question TEXT NOT NULL,
  answer TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage_log(
  id INTEGER PRIMARY KEY AUTOINCREMENT, turn_id INTEGER NOT NULL, model TEXT NOT NULL,
  input_uncached INTEGER NOT NULL, cache_write INTEGER NOT NULL, cache_read INTEGER NOT NULL,
  output INTEGER NOT NULL, cost_usd REAL NOT NULL);
CREATE TABLE IF NOT EXISTS citation_snapshots(
  turn_id INTEGER NOT NULL, chunk_id TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(turn_id, chunk_id));
CREATE TABLE IF NOT EXISTS embedding_turn_usage(
  turn_id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS turn_runs(
  turn_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS range_citation_snapshots(
  turn_id INTEGER NOT NULL, evidence_id TEXT NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(turn_id, evidence_id));
"""


def session_title(question: str) -> str:
    text = ' '.join(question.split())
    return text if len(text) <= 48 else text[:47].rstrip() + '…'


class SessionStore:
    """폴더별 대화 기록과 호출별 사용량. 색인 DB와 분리해 색인 쓰기와 잠금이 겹치지 않게 한다."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(db_path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        if "citation_state" not in {r[1] for r in self.con.execute("PRAGMA table_info(turns)")}:
            if self.con.execute("SELECT COUNT(*) FROM turns").fetchone()[0]:
                backup_path = db_path.with_suffix(db_path.suffix + ".before-citations")
                if not backup_path.exists():
                    with sqlite3.connect(backup_path) as backup:
                        self.con.backup(backup)
            with self.con:
                self.con.execute("ALTER TABLE turns ADD COLUMN citation_state TEXT NOT NULL DEFAULT 'legacy_unverified'")
        with self.con:
            if 'title' not in {r[1] for r in self.con.execute('PRAGMA table_info(sessions)')}:
                self.con.execute('ALTER TABLE sessions ADD COLUMN title TEXT')
                rows = self.con.execute("""SELECT s.id,
                    (SELECT question FROM turns WHERE session_id=s.id ORDER BY id LIMIT 1)
                    FROM sessions s""").fetchall()
                self.con.executemany('UPDATE sessions SET title=? WHERE id=?',
                                     [(session_title(q), sid) for sid, q in rows if q])
        self._lock = threading.Lock()
        self._run_locks = {}
        self._lease_dir = db_path.parent / 'run-leases'
        self._lease_dir.mkdir(exist_ok=True)

    def close(self) -> None:
        for rid, handle in self._run_locks.items():
            handle.close()
            (self._lease_dir / rid).unlink(missing_ok=True)
        self._run_locks.clear()
        self.con.close()

    def new_run(self, session_id):
        with self._lock, self.con:
            if not self.con.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                raise ValueError('unknown session')
            rid = uuid.uuid4().hex
            handle = acquire(self._lease_dir / rid)
            try:
                self.con.execute("INSERT INTO runs VALUES (?,?,'running')", (rid, session_id))
            except BaseException:
                handle.close()
                (self._lease_dir / rid).unlink(missing_ok=True)
                raise
            self._run_locks[rid] = handle
            return rid

    def record_event(self, run_id, kind, fields, snapshot=None, *, reuse_snapshot=None):
        with self._lock, self.con:
            self.con.execute('BEGIN IMMEDIATE')
            row = self.con.execute('SELECT state FROM runs WHERE id=?', (run_id,)).fetchone()
            if row is None or row[0] != 'running':
                return None
            seq = self.con.execute('SELECT COALESCE(MAX(seq),0)+1 FROM run_events WHERE run_id=?',
                                   (run_id,)).fetchone()[0]
            if seq > RUN_EVENTS and kind not in ('done', 'error', 'cancelled', 'interrupted'):
                return None
            event = {**fields, 'schema_version': 1, 'run_id': run_id, 'seq': seq,
                     'timestamp': time.time(), 'kind': kind}
            if seq > RUN_EVENTS:
                event['events_truncated'] = True
            if snapshot is not None and reuse_snapshot is not None:
                # Only share a stored request within this run, after comparing the
                # filtered payload. Changed requests must retain their own body.
                previous = self.con.execute('SELECT payload FROM run_snapshots WHERE run_id=? AND seq=?',
                                            (run_id, reuse_snapshot)).fetchone()
                if previous:
                    body = json.loads(previous[0])
                    if body == snapshot:
                        event['snapshot_ref'] = reuse_snapshot
                    elif (kind == 'token_count_sent' and isinstance(body, dict)
                          and {k: v for k, v in body.items() if k not in ('store', 'include', 'stream')} == snapshot):
                        # Keep the token-count view exact without storing its input again.
                        event.update(snapshot_ref=seq, request_snapshot_ref=reuse_snapshot)
            if snapshot is not None and 'snapshot_ref' not in event:
                encoded = json.dumps(snapshot, ensure_ascii=False)
                size = len(encoded.encode('utf-8'))
                used = self.con.execute('SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) '
                                        'FROM run_snapshots WHERE run_id=?', (run_id,)).fetchone()[0]
                total = self.con.execute('SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) '
                                         'FROM run_snapshots').fetchone()[0]
                if size > SNAPSHOT_BYTES or used + size > RUN_BYTES or total + size > WORKSPACE_BYTES:
                    event['snapshot_omitted'] = 'storage_limit'
                else:
                    event['snapshot_ref'] = seq
                    self.con.execute('INSERT INTO run_snapshots VALUES (?,?,?)', (run_id, seq, encoded))
            self.con.execute('INSERT INTO run_events VALUES (?,?,?)',
                             (run_id, seq, json.dumps(event, ensure_ascii=False)))
            if kind in ('done', 'error', 'cancelled', 'interrupted'):
                self.con.execute('UPDATE runs SET state=? WHERE id=?', (kind, run_id))
            return event

    def recover_interrupted(self):
        """Only recover runs whose OS ownership lease can be acquired."""
        with self._lock:
            ids = [r[0] for r in self.con.execute("SELECT id FROM runs WHERE state='running'")]
        for run_id in ids:
            self._recover_run(run_id)

    def _recover_run(self, run_id):
        if run_id not in self._run_locks:
            # Only database-issued IDs become filesystem names.
            if not re.fullmatch('[0-9a-f]{32}', run_id):
                raise ValueError('invalid run id')
            try:
                handle = acquire(self._lease_dir / run_id)
            except WriterBusy:
                pass
            else:
                try:
                    self.record_event(run_id, 'interrupted', {'reason': 'owner_lock_released'})
                finally:
                    handle.close()
                    (self._lease_dir / run_id).unlink(missing_ok=True)

    def run_data(self, session_id, run_id, after=0, snapshot=None):
        with self._lock:
            row = self.con.execute('SELECT state FROM runs WHERE id=? AND session_id=?',
                                   (run_id, session_id)).fetchone()
        if row and row[0] == 'running':
            self._recover_run(run_id)
        with self._lock:
            row = self.con.execute('SELECT state FROM runs WHERE id=? AND session_id=?',
                                   (run_id, session_id)).fetchone()
            if row is None:
                return None
            if snapshot is not None:
                value = self.con.execute('SELECT payload FROM run_snapshots WHERE run_id=? AND seq=?',
                                         (run_id, snapshot)).fetchone()
                if value:
                    return json.loads(value[0])
                event = self.con.execute('SELECT payload FROM run_events WHERE run_id=? AND seq=?',
                                         (run_id, snapshot)).fetchone()
                event = json.loads(event[0]) if event else {}
                ref = event.get('request_snapshot_ref')
                if (event.get('kind') == 'token_count_sent' and type(ref) is int and 0 < ref < snapshot):
                    value = self.con.execute('SELECT payload FROM run_snapshots WHERE run_id=? AND seq=?',
                                             (run_id, ref)).fetchone()
                    if value:
                        return {k: v for k, v in json.loads(value[0]).items()
                                if k not in ('store', 'include', 'stream')}
                return None
            events = self.con.execute('SELECT payload FROM run_events WHERE run_id=? AND seq>? '
                                      'ORDER BY seq LIMIT 100', (run_id, after)).fetchall()
            return {'state': row[0], 'events': [json.loads(r[0]) for r in events]}

    def new_session(self) -> str:
        sid = uuid.uuid4().hex
        with self._lock, self.con:
            self.con.execute("INSERT INTO sessions(id,created) VALUES (?, ?)", (sid, time.time()))
        return sid

    def ensure_title(self, session_id: str, question: str) -> None:
        """Save a local first-question title once, including before a failed response."""
        with self._lock, self.con:
            self.con.execute("UPDATE sessions SET title=? WHERE id=? AND (title IS NULL OR trim(title)='')",
                             (session_title(question), session_id))

    def exists(self, session_id: str) -> bool:
        with self._lock:
            return self.con.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone() is not None

    def list_sessions(self, before: str | None = None):
        with self._lock:
            boundary = None
            if before is not None:
                boundary = self.con.execute('SELECT created,id FROM sessions WHERE id=?', (before,)).fetchone()
                if boundary is None:
                    raise ValueError('invalid history cursor')
            rows = self.con.execute('''SELECT s.id,s.created,
                (SELECT question FROM turns WHERE session_id=s.id ORDER BY id LIMIT 1),
                (SELECT COUNT(*) FROM turns WHERE session_id=s.id), s.title
                FROM sessions s''' + (' WHERE (s.created,s.id) < (?,?)' if boundary else '') +
                ' ORDER BY created DESC,id DESC LIMIT 51', boundary or ()).fetchall()
        return {'sessions': [{'id': r[0], 'created': r[1], 'question': r[2], 'turns': r[3], 'title': r[4]}
                             for r in rows[:50]], 'limited': len(rows) > 50,
                'next_cursor': rows[49][0] if len(rows) > 50 else None}

    def saved_turns(self, session_id, before: str | None = None):
        with self._lock:
            if not self.con.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                return None
            if before is not None and (not before.isdecimal() or len(before) > 18 or not self.con.execute(
                'SELECT 1 FROM turns WHERE session_id=? AND id=?', (session_id, before)
            ).fetchone()):
                raise ValueError('invalid history cursor')
            rows = self.con.execute('''SELECT id,question,answer,created,citation_state,
                (SELECT run_id FROM turn_runs WHERE turn_id=turns.id) FROM turns
                WHERE session_id=?''' + (' AND id < ?' if before is not None else '') +
                ' ORDER BY id DESC LIMIT 101', (session_id, before) if before is not None else (session_id,)).fetchall()
        return {'turns': [dict(zip(('id', 'question', 'answer', 'created', 'citation_state', 'run_id'), r, strict=True))
                          for r in reversed(rows[:100])], 'limited': len(rows) > 100,
                'next_cursor': str(rows[99][0]) if len(rows) > 100 else None}

    def history(self, session_id: str, limit: int) -> list[tuple[str, str]]:
        with self._lock:
            rows = self.con.execute(
                "SELECT question, answer FROM turns WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit)).fetchall()
        return [(q, a) for q, a in reversed(rows)]

    def history_evidence(self, session_id: str, limit: int = 6) -> list[dict]:
        """Bounded saved ranges from the same recent conversation, newest first."""
        with self._lock:
            rows = self.con.execute('''
                SELECT c.payload FROM range_citation_snapshots c
                JOIN turns t ON t.id=c.turn_id
                WHERE t.id IN (SELECT id FROM turns WHERE session_id=?
                               ORDER BY id DESC LIMIT ?)
                  AND t.citation_state='recorded' AND length(c.payload)<=24000
                ORDER BY t.id DESC LIMIT 12
            ''', (session_id, limit)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def add_turn(self, session_id: str, question: str, answer: str, model: str, calls: list[Usage],
                 citations: list[dict] | None = None, embedding: dict | None = None,
                 range_citations: list[dict] | None = None, run_id: str | None = None) -> int:
        with self._lock, self.con:
            if not self.con.execute('SELECT 1 FROM sessions WHERE id=?', (session_id,)).fetchone():
                raise ValueError('unknown session')
            if run_id is not None and not self.con.execute(
                'SELECT 1 FROM runs WHERE id=? AND session_id=?', (run_id, session_id)
            ).fetchone():
                raise ValueError('run does not belong to session')
            self.con.execute("UPDATE sessions SET title=? WHERE id=? AND (title IS NULL OR trim(title)='')",
                             (session_title(question), session_id))
            turn_id = self.con.execute(
                "INSERT INTO turns(session_id, question, answer, created) VALUES (?,?,?,?)",
                (session_id, question, answer, time.time())).lastrowid
            if run_id is not None:
                self.con.execute('INSERT INTO turn_runs VALUES (?,?)', (turn_id, run_id))
            for u in calls:
                self.con.execute(
                    "INSERT INTO usage_log(turn_id, model, input_uncached, cache_write, cache_read, output, cost_usd) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (turn_id, model, u.input_uncached, u.cache_write, u.cache_read, u.output,
                     cost_usd(model, u) or 0.0))
            for citation in citations or []:
                self.con.execute("INSERT INTO citation_snapshots VALUES (?,?,?)",
                                 (turn_id, citation["chunk_id"], json.dumps(citation, ensure_ascii=False)))
            for citation in range_citations or []:
                _validate_range(citation)
                self.con.execute('INSERT INTO range_citation_snapshots VALUES (?,?,?)',
                                 (turn_id, citation['evidence_id'], json.dumps(citation, ensure_ascii=False)))
            if citations is not None or range_citations is not None:
                self.con.execute("UPDATE turns SET citation_state='recorded' WHERE id=?", (turn_id,))
            if embedding is not None:
                self.con.execute('INSERT INTO embedding_turn_usage VALUES (?,?)', (turn_id, json.dumps(embedding)))
        return turn_id

    def citations(self, turn_id: int, session_id: str | None = None) -> dict:
        with self._lock:
            if session_id is not None and not self.con.execute(
                    'SELECT 1 FROM turns WHERE id=? AND session_id=?', (turn_id, session_id)).fetchone():
                return {'status': 'missing', 'citations': []}
            rows = self.con.execute("SELECT payload FROM citation_snapshots WHERE turn_id=?", (turn_id,)).fetchall()
            rows += self.con.execute('SELECT payload FROM range_citation_snapshots WHERE turn_id=?',
                                     (turn_id,)).fetchall()
            turn = self.con.execute("SELECT citation_state FROM turns WHERE id=?", (turn_id,)).fetchone()
        return {"status": turn[0] if turn else "missing",
                "citations": [json.loads(r[0]) for r in rows]}

    def delete_session(self, session_id: str) -> None:
        with self._lock, contextlib.ExitStack() as leases, self.con:
            self.con.execute('BEGIN IMMEDIATE')
            for (rid,) in self.con.execute("SELECT id FROM runs WHERE session_id=? AND state='running'", (session_id,)):
                if not re.fullmatch('[0-9a-f]{32}', rid):
                    raise ValueError('invalid run id')
                leases.callback(acquire(self._lease_dir / rid).close)
            for table in ('run_events', 'run_snapshots'):
                self.con.execute(f'DELETE FROM {table} WHERE run_id IN '
                                 '(SELECT id FROM runs WHERE session_id=?)', (session_id,))
            self.con.execute('DELETE FROM runs WHERE session_id=?', (session_id,))
            for table in ("citation_snapshots", "range_citation_snapshots", "usage_log", "embedding_turn_usage", "turn_runs"):
                self.con.execute(f"DELETE FROM {table} WHERE turn_id IN "
                                 "(SELECT id FROM turns WHERE session_id=?)", (session_id,))
            self.con.execute("DELETE FROM turns WHERE session_id=?", (session_id,))
            self.con.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def session_cost(self, session_id: str) -> float | None:
        with self._lock:
            cost = self.con.execute(
                "SELECT COALESCE(SUM(u.cost_usd), 0) FROM usage_log u JOIN turns t ON t.id = u.turn_id "
                "WHERE t.session_id=?", (session_id,)).fetchone()[0]
            rows = self.con.execute('''SELECT u.payload FROM embedding_turn_usage u JOIN turns t ON t.id=u.turn_id
                WHERE t.session_id=?''', (session_id,)).fetchall()
            for row in rows:
                usage = json.loads(row[0])
                if usage['unknown_reserved_usd']:
                    return None
                cost += usage['cost_usd']
            return cost


def _validate_range(citation):
    """Validate persisted shape; the caller must verify source and model delivery."""
    if citation.get('schema_version') != 2 or citation.get('kind') != 'document_range':
        raise ValueError('invalid range schema')
    for key in ('evidence_id', 'doc', 'text', 'page_kind'):
        if not isinstance(citation.get(key), str) or not citation[key]:
            raise ValueError('invalid range field')
    for key in ('doc_id', 'revision', 'line_start', 'line_end'):
        if type(citation.get(key)) is not int or citation[key] < 1:
            raise ValueError('invalid range location')
    for key in ('content_hash', 'text_hash'):
        if not isinstance(citation.get(key), str) or not re.fullmatch('[0-9a-f]{64}', citation[key]):
            raise ValueError('invalid range hash')
    if 'original_line_start' in citation or 'original_line_end' in citation:
        first, last = citation.get('original_line_start'), citation.get('original_line_end')
        if (type(first) is not int or type(last) is not int or first < 1 or last < first
                or first - citation['line_start'] != last - citation['line_end']
                or first < citation['line_start']):
            raise ValueError('invalid original line location')
    start, end = citation.get('start'), citation.get('end')
    if (type(start) is not int or type(end) is not int or not 0 <= start < end
            or end - start != len(citation['text']) or citation['line_end'] < citation['line_start']):
        raise ValueError('invalid range bounds')
