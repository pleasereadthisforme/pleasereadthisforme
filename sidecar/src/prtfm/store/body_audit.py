"""Optional bounded, read-only validation of active transformed generations."""
import hashlib
import json

from prtfm.config import data_root
from prtfm.store.block_metadata import restore_blocks

BODY_LIMIT = 64 * 1024 * 1024
METADATA_LIMIT = 8 * 1024 * 1024


def _signature(path):
    st = path.stat()
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _read(path, limit):
    with path.open('rb') as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise OverflowError('size_limit')
    return data


def inspect_bodies(report, *, root=None):
    base = (data_root() if root is None else root).resolve()
    checks = []
    for generation in report['generations']:
        if generation['kind'] != 'stored_body':
            continue
        identity = {key: generation[key] for key in ('folder', 'doc_id', 'revision')}
        directory = base / 'folders' / identity['folder'] / 'cache' / str(identity['doc_id']) / str(identity['revision'])
        paths = [directory / 'metadata.json', directory / 'document.md']
        status, reason = 'verified', None
        before = None
        raw = text = metadata = None
        phase = 'read'
        try:
            if any(not p.resolve().is_relative_to(base) for p in paths):
                raise ValueError('outside_data_root')
            before = [_signature(p) for p in paths]
            phase = 'metadata'
            metadata = json.loads(_read(paths[0], METADATA_LIMIT))
            phase = 'body_read'
            raw = _read(paths[1], BODY_LIMIT)
            if not isinstance(metadata, dict) or metadata.get('source_kind') == 'original_text':
                raise ValueError('metadata_kind_changed_or_invalid')
            phase = 'checksum'
            if hashlib.sha256(raw).hexdigest() != metadata.get('sha256'):
                raise ValueError('checksum_mismatch')
            phase = 'utf8'
            text = raw.decode('utf-8')
            phase = 'page_starts'
            starts = metadata.get('page_starts')
            if (not isinstance(starts, list) or any(type(n) is not int or not 0 <= n <= len(text) for n in starts)
                    or starts != sorted(starts)):
                raise ValueError('invalid_page_starts')
            if 'blocks' in metadata:
                phase = 'blocks'
                restore_blocks(text, metadata)
        except OverflowError:
            status, reason = 'skipped', 'size_limit'
        except (OSError, ValueError, KeyError, TypeError):
            status, reason = 'failed', phase + '_failed'
        finally:
            # An observation during generation replacement is not proof of corruption.
            if before is not None:
                try:
                    if (any(not p.resolve().is_relative_to(base) for p in paths)
                            or before != [_signature(p) for p in paths]):
                        status, reason = 'changed', 'changed_during_check'
                except OSError:
                    status, reason = 'changed', 'changed_during_check'
        checks.append({**identity, 'status': status, 'reason': reason})
        if status != 'verified':
            report['findings'].append({'kind': 'stored_body_check_' + status, **identity, 'reason': reason})
    report['body_checks'] = checks
    report['body_check_scope'] = 'observed_active_stored_bodies_only'
    report['body_check_limits'] = {'body_bytes': BODY_LIMIT, 'metadata_bytes': METADATA_LIMIT}
    # This does not inspect vectors, raw originals, inactive or missing-metadata bodies.
    return report
