"""Validate parser output before publishing a new document generation."""
from dataclasses import asdict
from prtfm.indexing.blocks import Block, build_document
from prtfm.store.block_metadata import compact_blocks


def validate_result(result, *, create_chunks=None):
    text, starts, pages = result['text'], result['page_starts'], result['pages']
    if (not isinstance(text, str) or type(pages) is not int or pages < 0
            or not isinstance(starts, list) or len(starts) != pages
            or any(type(v) is not int or not 0 <= v <= len(text) for v in starts)
            or starts != sorted(starts) or (starts and starts[0] != 0)
            or (not pages and text)):
        raise ValueError('invalid parser page metadata')
    compact_blocks(text, {'blocks': result['blocks']})
    for block in result['blocks']:
        if type(block.get('page')) is not int or not 1 <= block['page'] <= pages:
            raise ValueError('invalid parser block page')
    rows = result['rows']
    if not isinstance(rows, list):
        raise ValueError('invalid parser rows')
    for i, row in enumerate(rows):
        if (type(row.get('idx')) is not int or row['idx'] != i
                or type(row.get('page')) is not int or not 1 <= row['page'] <= pages
                or not isinstance(row.get('text'), str)):
            raise ValueError('invalid parser chunk')
    for field in ('fallback_pages', 'empty_pages'):
        values = result[field]
        if not isinstance(values, list) or any(type(v) is not int or not 1 <= v <= pages for v in values):
            raise ValueError('invalid parser page list')
    if not isinstance(result['warnings'], list) or any(
            not isinstance(v, dict) or type(v.get('page')) is not int
            or not 1 <= v['page'] <= pages or not isinstance(v.get('code'), str)
            for v in result['warnings']):
        raise ValueError('invalid parser warnings')
    if type(result['attempts']) is not int or result['attempts'] < 0:
        raise ValueError('invalid parser attempts')

    # Reuse the producer's normalization/span/token contract rather than a
    # second permissive interpretation of chunk text. No parser/provider call.
    blocks = result['blocks']
    if any(type(b.get('block_id')) is not int or b['block_id'] != i
           for i, b in enumerate(blocks)):
        raise ValueError('invalid parser block identity')
    page_numbers = [b['page'] for b in blocks]
    if page_numbers != sorted(page_numbers) or set(page_numbers) != set(range(1, pages + 1)):
        raise ValueError('incomplete parser pages')
    for row in rows:
        if type(row.get('page_end')) is not int or not isinstance(row.get('spans'), list):
            raise ValueError('invalid parser chunk spans')
        for span in row['spans']:
            if not isinstance(span, dict) or any(type(span.get(k)) is not int
                    for k in ('start', 'end', 'page', 'block_id')):
                raise ValueError('invalid parser span range')
    canonical = [Block(**{k: b[k] for k in Block.__dataclass_fields__ if k in b}) for b in blocks]
    expected_text, expected_starts, expected_rows, expected_blocks = build_document(
        canonical, create_chunks=bool(rows) if create_chunks is None else create_chunks)
    if text != expected_text or starts != expected_starts or blocks != expected_blocks:
        raise ValueError('parser body/page mismatch')
    if rows != [asdict(row) for row in expected_rows]:
        raise ValueError('parser chunk/body mismatch')
