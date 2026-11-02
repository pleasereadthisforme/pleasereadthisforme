"""Retire duplicate block text while preserving the body and database."""
import json

from prtfm.store.block_metadata import compact_blocks
from prtfm.store.cache import _atomic_write


def transition(ws, *, apply=False):
    if apply and ws.writer_lock is None:
        raise ValueError('workspace writer lock required')
    results = []
    with ws.cache._guard():
        protected = ws.cache._leases()
        for path, doc in ws.store.docs().items():
            item = {'path': path, 'state': 'skipped', 'bytes': 0}
            results.append(item)
            try:
                if doc.status != 'ok' or doc.repair_required:
                    item['reason'] = 'not_ready'
                    continue
                if (doc.id, doc.revision) in protected:
                    item['reason'] = 'in_use'
                    continue
                target = ws.cache.root / str(doc.id) / str(doc.revision) / 'metadata.json'
                raw = target.read_bytes()
                meta = json.loads(raw)
                if 'blocks' not in meta:
                    item['reason'] = 'no_blocks'
                    continue
                stored = ws.cache.read_revision(doc.id, doc.revision)
                if stored is None:
                    raise ValueError('missing body')
                replacement = compact_blocks(stored[0], meta)
                if replacement == meta:
                    item['state'] = 'already_compact'
                    continue
                serialized = json.dumps(replacement, ensure_ascii=False)
                item.update(state='eligible', bytes=len(raw) - len(serialized.encode('utf-8')))
                if apply:
                    _atomic_write(target, serialized)
                    item['state'] = 'converted'
            except (OSError, ValueError, KeyError, TypeError) as exc:
                item.update(state='skipped', reason=str(exc))
    return {'applied': apply, 'files': results,
            'removed_bytes': sum(r['bytes'] for r in results if r['state'] == 'converted')}
