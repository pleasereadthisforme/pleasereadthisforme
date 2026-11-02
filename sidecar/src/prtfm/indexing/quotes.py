"""Retire only unexecuted quotes, under the owning workspace writer lock."""
import json
import math
import re

TTL = 1800


class QuoteUnavailable(ValueError):
    pass


def prune_semantic(store, now):
    with store.con:
        store.con.execute("""DELETE FROM semantic_quotes WHERE state='quoted'
            AND (created IS NULL OR created < ?)""", (now - TTL,))


def prune_vision(directory, now):
    if not directory.exists():
        return
    for path in directory.iterdir():
        if not re.fullmatch(r'[0-9a-f]{32}\.json', path.name) or path.is_symlink():
            continue
        try:
            quote = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(quote, dict):
                continue
            created = quote.get('created')
            if (quote.get('id') == path.stem and quote.get('state') == 'quoted'
                    and type(created) in (int, float) and math.isfinite(created)
                    and created < now - TTL):
                path.unlink()
        except (OSError, ValueError):
            # Unknown/corrupt entries are not evidence that deletion is safe.
            continue
