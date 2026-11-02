"""스키마 변경은 백업 후 트랜잭션으로 적용한다."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def migrate(con: sqlite3.Connection, path: Path) -> None:
    columns = {r[1] for r in con.execute("PRAGMA table_info(docs)")}
    if "revision" in columns:
        return
    if con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]:
        backup_path = path.with_suffix(path.suffix + ".before-revisions")
        if not backup_path.exists():
            with sqlite3.connect(backup_path) as backup:
                con.backup(backup)
    pipeline = con.execute("PRAGMA user_version").fetchone()[0]
    with con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("ALTER TABLE docs ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
        con.execute("ALTER TABLE docs ADD COLUMN repair_required INTEGER NOT NULL DEFAULT 0")
        con.execute("ALTER TABLE chunks ADD COLUMN page_end INTEGER")
        con.execute("ALTER TABLE chunks ADD COLUMN spans TEXT NOT NULL DEFAULT '[]'")
        con.execute("UPDATE docs SET revision=1 WHERE status='ok'")
        con.execute("UPDATE chunks SET page_end=page")
        con.execute("CREATE TABLE app_meta(key TEXT PRIMARY KEY, value INTEGER NOT NULL)")
        con.executemany("INSERT INTO app_meta VALUES (?,?)",
                        [("schema_version", 1), ("pipeline_version", pipeline), ("pass2_version", 1)])
        con.execute("""CREATE TABLE refinement(
            doc_id INTEGER PRIMARY KEY, input_hash TEXT, version INTEGER NOT NULL,
            state TEXT NOT NULL, failure_code TEXT, details TEXT NOT NULL DEFAULT '{}')""")


def migrate_semantic(con: sqlite3.Connection, path: Path) -> None:
    if con.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_files'").fetchone():
        return
    if con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]:
        backup_path = path.with_suffix(path.suffix + '.before-semantic')
        if not backup_path.exists():
            with sqlite3.connect(backup_path) as backup:
                con.backup(backup)
    with con:
        con.execute('BEGIN IMMEDIATE')
        con.execute('''CREATE TABLE semantic_files(
            doc_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
            generation TEXT, revision INTEGER, state TEXT NOT NULL DEFAULT 'off')''')
        con.execute('''CREATE TABLE semantic_quotes(
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'quoted')''')
        con.execute('''CREATE TABLE semantic_jobs(
            id TEXT PRIMARY KEY, state TEXT NOT NULL, budget_nusd INTEGER NOT NULL,
            error TEXT, warnings TEXT NOT NULL DEFAULT '[]')''')
        con.execute('''CREATE TABLE semantic_staging(
            doc_id INTEGER PRIMARY KEY, revision INTEGER NOT NULL, generation TEXT NOT NULL)''')
        con.execute('''CREATE TABLE semantic_usage(
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL, kind TEXT NOT NULL,
            tokens INTEGER NOT NULL, reserved_nusd INTEGER NOT NULL,
            actual_nusd INTEGER, state TEXT NOT NULL, created REAL NOT NULL)''')
        con.execute('''CREATE TRIGGER semantic_invalidate AFTER UPDATE OF revision,content_hash,status,parse_stage,repair_required ON docs
            WHEN NEW.revision != OLD.revision OR NEW.content_hash IS NOT OLD.content_hash
              OR NEW.status != 'ok' OR NEW.parse_stage=0 OR NEW.repair_required=1
            BEGIN
              UPDATE semantic_files SET generation=NULL,revision=NULL,
                state=CASE WHEN enabled=1 THEN 'needs_estimate' ELSE 'off' END WHERE doc_id=NEW.id;
              UPDATE docs SET embedded=0 WHERE id=NEW.id;
              DELETE FROM semantic_staging WHERE doc_id=NEW.id;
            END''')
        con.execute('''CREATE TRIGGER semantic_delete AFTER DELETE ON docs BEGIN
            DELETE FROM semantic_files WHERE doc_id=OLD.id;
            DELETE FROM semantic_staging WHERE doc_id=OLD.id;
            DELETE FROM refinement WHERE doc_id=OLD.id;
            END''')
        con.execute("UPDATE docs SET embedded=0")
        con.execute("UPDATE app_meta SET value=2 WHERE key='schema_version'")


def migrate_quote_created(con: sqlite3.Connection) -> None:
    if 'created' in {r[1] for r in con.execute('PRAGMA table_info(semantic_quotes)')}:
        return
    # Additive migration: old quotes retain their payload/state. NULL means that
    # their age is unknown; only unexecuted quotes are retired at the next use.
    with con:
        con.execute('ALTER TABLE semantic_quotes ADD COLUMN created REAL')


def migrate_job_quote(con: sqlite3.Connection) -> None:
    if 'quote_id' in {r[1] for r in con.execute('PRAGMA table_info(semantic_jobs)')}:
        return
    # Historical jobs have no reliable quote association. Never infer one from
    # row order, matching costs, or current document state.
    with con:
        con.execute('ALTER TABLE semantic_jobs ADD COLUMN quote_id TEXT')
