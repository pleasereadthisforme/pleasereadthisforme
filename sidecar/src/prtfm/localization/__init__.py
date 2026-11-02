"""Local UI translations only; never translates user documents or model output."""
from contextvars import ContextVar

import json
import os
from functools import lru_cache
from pathlib import Path

request_language = ContextVar("ui_language", default=None)

@lru_cache(maxsize=1)
def _messages():
    return json.loads(Path(__file__).with_name('native.json').read_text(encoding='utf-8'))

def text(value: str) -> str:
    if (request_language.get() or os.environ.get('PRTFM_UI_LANGUAGE', 'en')) == 'ko':
        return value
    return _messages().get(value, value)
