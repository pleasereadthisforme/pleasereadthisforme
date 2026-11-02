"""Block text is a range of the verified document body, not a second copy."""


def restore_blocks(text, metadata):
    version = metadata.get('blocks_version')
    if version not in (None, 1):
        raise ValueError('unknown blocks version')
    blocks = metadata['blocks']
    if not isinstance(blocks, list):
        raise ValueError('invalid blocks')
    restored, cursor = [], 0
    for block in blocks:
        if not isinstance(block, dict):
            raise ValueError('invalid block')
        start, end = block['start'], block['end']
        if (type(start) is not int or type(end) is not int or start != cursor
                or not start <= end <= len(text)):
            raise ValueError('invalid block range')
        body = text[start:end]
        if ('text' in block and block['text'] != body) or (version is None and 'text' not in block):
            raise ValueError('block body mismatch')
        if restored and text[cursor - 2:cursor] != '\n\n':
            raise ValueError('invalid block separator')
        restored.append({**block, 'text': body})
        cursor = end + 2
    if (blocks and cursor - 2 != len(text)) or (not blocks and text):
        raise ValueError('incomplete block coverage')
    return restored


def compact_blocks(text, metadata):
    blocks = restore_blocks(text, metadata)
    return {**metadata, 'blocks_version': 1,
            'blocks': [{k: v for k, v in b.items() if k != 'text'} for b in blocks]}
