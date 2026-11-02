"""Explicit per-workspace removal of derived bodies; originals and history survive."""
import shutil
import sqlite3
from pathlib import Path

from prtfm.config import workspace_dir
from prtfm.store.db import Store
from prtfm.store.folders import registry
from prtfm.store.locking import WriterBusy
from prtfm.store.summary import _link


def processed_catalog():
    with registry() as con:
        folders = [r[0] for r in con.execute('SELECT path FROM workspaces ORDER BY path')]
    files, unavailable = [], 0
    for folder in folders:
        directory = workspace_dir(Path(folder))
        if not (directory / 'index.db').is_file():
            continue
        try:
            store = Store.open_readonly(directory / 'index.db')
            try:
                for doc in store.docs().values():
                    body = directory / 'cache' / str(doc.id) / str(doc.revision) / 'document.md'
                    if body.is_file() and not body.is_symlink():
                        files.append({'folder': folder, 'doc_id': doc.id, 'path': doc.path,
                                      'revision': doc.revision, 'bytes': body.stat().st_size})
            finally:
                store.close()
        except (OSError, ValueError, sqlite3.Error):
            unavailable += 1
    return {'files': files, 'unavailable': unavailable}


def remove_processed(ws, doc_id, revision):
    doc = next((d for d in ws.store.docs().values() if d.id == doc_id), None)
    if doc is None or doc.revision != revision:
        raise ValueError('document changed; refresh the list')
    directory = ws.cache.root / str(doc_id)
    # Never follow a directory link while removing derived generations.
    for parent in (ws.dir, ws.cache.root, directory):
        if parent.exists() and _link(parent.lstat()):
            raise ValueError('unsafe processed document path')
    with ws.cache._guard():
        if any(d == doc_id for d, _ in ws.cache._leases()):
            raise WriterBusy('document is being read')
        if directory.exists():
            for path in directory.rglob('*'):
                if _link(path.lstat()):
                    raise ValueError('unsafe processed document entry')
        body = directory / str(revision) / 'document.md'
        if not body.is_file() and doc.error != 'processing_result_removed':
            raise ValueError('no stored processing result')
        from prtfm.indexing.embedding import delete_document_embeddings
        # Shared vector release only frees payloads no other workspace references.
        delete_document_embeddings(ws.store, ws.dir / 'vectors', doc_id)
        # Commit exclusion before unlinking; an interrupted unlink is retryable.
        with ws.store.con:
            ws.store._delete_chunks(doc_id)
            ws.store.con.execute("UPDATE docs SET status='removed',error='processing_result_removed',"
                                 'parse_stage=0,embedded=0,repair_required=0 WHERE id=?', (doc_id,))
        if directory.exists():
            shutil.rmtree(directory)
        for suffix in ('.md', '.json'):
            legacy = ws.cache.root / (str(doc_id) + suffix)
            if legacy.is_symlink():
                raise ValueError('unsafe legacy entry')
            legacy.unlink(missing_ok=True)
    return {'removed': True, 'original_preserved': True}
