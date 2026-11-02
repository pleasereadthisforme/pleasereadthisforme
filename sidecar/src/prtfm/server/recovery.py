"""Recover abandoned chat runs; read cost evidence without settling charges."""
import json
import sqlite3

from prtfm.config import data_root


def vision_ledger_state(ledger):
    """Recognize the older final-result shape without inventing its job state."""
    if not isinstance(ledger, dict):
        raise ValueError('invalid ledger')
    if isinstance(ledger.get('state'), str):
        return ledger['state']
    if ('state' not in ledger and 'version' not in ledger
            and isinstance(ledger.get('model'), str)
            and isinstance(ledger.get('results'), list)
            and isinstance(ledger.get('usage'), list)
            and isinstance(ledger.get('cost_usd'), (int, float))
            and not isinstance(ledger.get('cost_usd'), bool)
            and isinstance(ledger.get('unknown_usage'), bool)):
        return None
    raise ValueError('invalid ledger')


def startup_recovery():
    result = {'embedding_jobs': 0, 'embedding_requests': 0,
              'chat_interrupted': 0, 'vision_jobs': 0, 'vision_uncertain_jobs': 0, 'unreadable': 0}
    try:
        _collect(result)
    except OSError:
        result['unreadable'] += 1
    return result


def _collect(result):
    root = data_root() / 'folders'
    if not root.exists():
        return
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        sessions_db = directory / 'sessions.db'
        if sessions_db.is_file():
            try:
                # Inspect first: do not migrate old session stores merely for an empty notice.
                con = sqlite3.connect(sessions_db.as_uri() + '?mode=ro', uri=True, timeout=1)
                try:
                    has_runs = con.execute("SELECT 1 FROM sqlite_master WHERE name='runs' AND type='table'").fetchone()
                    running = has_runs and con.execute("SELECT 1 FROM runs WHERE state='running' LIMIT 1").fetchone()
                    interrupted = con.execute("SELECT count(*) FROM runs WHERE state='interrupted'").fetchone()[0] if has_runs else 0
                finally:
                    con.close()
                if running:
                    from prtfm.store.sessions import SessionStore
                    sessions = SessionStore(sessions_db)
                    try:
                        sessions.recover_interrupted()
                        interrupted = sessions.con.execute("SELECT count(*) FROM runs WHERE state='interrupted'").fetchone()[0]
                    finally:
                        sessions.close()
                result['chat_interrupted'] += interrupted
            except (sqlite3.Error, OSError, ValueError):
                result['unreadable'] += 1
        for name in ('index.db', 'embedding-usage.db'):
            db = directory / name
            if not db.is_file():
                continue
            try:
                con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=1)
                try:
                    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if 'semantic_usage' in tables:
                        result['embedding_requests'] += con.execute(
                            "SELECT count(*) FROM semantic_usage WHERE state IN ('reserved','unknown')").fetchone()[0]
                    if 'semantic_jobs' in tables:
                        result['embedding_jobs'] += con.execute(
                            "SELECT count(*) FROM semantic_jobs WHERE state IN ('running','interrupted')").fetchone()[0]
                finally:
                    con.close()
            except (sqlite3.Error, OSError):
                result['unreadable'] += 1
        for path in (directory / 'vision-quotes').glob('*.result.json'):
            try:
                ledger = json.loads(path.read_text(encoding='utf-8'))
                if vision_ledger_state(ledger) == 'running':
                    result['vision_jobs'] += 1
                if ledger.get('unknown_usage'):
                    result['vision_uncertain_jobs'] += 1
            except (ValueError, OSError):
                result['unreadable'] += 1
