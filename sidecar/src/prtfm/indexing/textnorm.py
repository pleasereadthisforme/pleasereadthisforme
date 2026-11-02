from __future__ import annotations

import re
import unicodedata

_JA = r"぀-ヿ㐀-䶿一-鿿"  # 가나 + 한자
_JA_SPACE = re.compile(rf"(?<=[{_JA}]) +(?=[{_JA}])")
_JA_CHAR = re.compile(rf"[{_JA}]")
_HANGUL = re.compile(r"[가-힣]")


def normalize(text: str) -> str:
    """줄바꿈(CRLF·CR)을 LF로 통일하고 NFKC로 호환 문자를 정리한 뒤, 한글이 없는 줄에서만 한자·가나 사이 공백을 지운다.

    한국어는 띄어쓰기가 정상 표기라 추출 오류 공백과 구별할 수 없어 건드리지 않는다.
    """
    text = unicodedata.normalize("NFKC", text.replace("\r\n", "\n").replace("\r", "\n"))
    # Without a candidate Japanese gap, the per-line spacing rule cannot change text.
    if text.isascii() or not _JA_CHAR.search(text) or not _JA_SPACE.search(text):
        return text
    return "\n".join(ln if _HANGUL.search(ln) else _JA_SPACE.sub("", ln) for ln in text.split("\n"))
