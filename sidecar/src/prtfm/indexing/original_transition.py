"""Validate and retire redundant text bodies without changing document versions."""
import hashlib
import json
from pathlib import Path

from prtfm.indexing.extract import CODE_EXTS, DOC_EXTS
from prtfm.indexing.pass1 import workspace_chunk_policy
from prtfm.query.sources import FolderSourceReader
from prtfm.store.cache import _atomic_write


def transition(scope, ws, *, apply=False):
    if apply and ws.writer_lock is None:
        raise ValueError('workspace writer lock required')
    if workspace_chunk_policy(ws.store) != 'selected':
        raise ValueError('selected document policy required; legacy workspace unchanged')
    reader = FolderSourceReader(ws.store, ws.cache, scope)
    direct = (DOC_EXTS | CODE_EXTS) - {'.pdf', '.docx', '.pptx'}
    results = []
    with ws.cache._guard():
        protected = ws.cache._leases()
        for path, doc in ws.store.docs().items():
            if Path(path).suffix.lower() not in direct:
                continue
            item = {'path': path, 'state': 'skipped', 'bytes': 0}
            results.append(item)
            try:
                if doc.status != 'ok' or doc.repair_required:
                    item['reason'] = 'not_ready'
                    continue
                if (doc.id, doc.revision) in protected:
                    item['reason'] = 'in_use'
                    continue
                base = ws.cache.root / str(doc.id) / str(doc.revision)
                body = base / 'document.md'
                meta_path = base / 'metadata.json'
                meta = json.loads(meta_path.read_text(encoding='utf-8'))
                already = ws.cache.direct_revision(doc.id, doc.revision)
                if already and not body.exists():
                    item['state'] = 'already_original'
                    continue
                # Only known plain-text metadata can be migrated without losing information.
                if set(meta) - {'sha256', 'page_starts', 'create_chunks', 'source_kind', 'source_version', 'source_hash', 'pipeline_version'}:
                    item['reason'] = 'special_metadata'
                    continue
                if type(meta.get('create_chunks')) is not bool:
                    item['reason'] = 'unknown_chunk_policy'
                    continue
                original, starts = reader.read(path, doc, doc.revision)
                raw = body.read_bytes()
                stored = raw.decode('utf-8')
                digest = hashlib.sha256(raw).hexdigest()
                if stored != original or digest != meta['sha256'] or starts != meta['page_starts']:
                    item['reason'] = 'body_mismatch'
                    continue
                item.update(state='eligible', bytes=len(raw))
                if apply:
                    # Keep revision, chunks, vectors and citation hashes untouched.
                    # If interrupted after replacement, the next invocation verifies and removes the leftover body.
                    replacement = {**meta, 'source_kind': 'original_text', 'source_version': 1,
                                   'source_hash': doc.content_hash}
                    _atomic_write(meta_path, json.dumps(replacement, ensure_ascii=False))
                    body.unlink()
                    item['state'] = 'converted'
            except (OSError, ValueError, KeyError, TypeError) as exc:
                item.update(state='skipped', reason=str(exc) if str(exc).startswith('source_') else type(exc).__name__)
    return {'applied': apply, 'files': results,
            'eligible_bytes': sum(r['bytes'] for r in results if r['state'] in ('eligible', 'converted')),
            'removed_bytes': sum(r['bytes'] for r in results if r['state'] == 'converted')}
