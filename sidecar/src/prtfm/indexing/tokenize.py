from __future__ import annotations

import re
from functools import lru_cache

_HANGUL = re.compile(r"[가-힣]")
_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_EN_WORD = re.compile(r"[a-z0-9]+(?:[.\-'][a-z0-9]+)*")

# Kiwi: 내용어 + 접두사 유지, 조사·어미·접미사·기호 제거
KO_KEEP = frozenset({"NNG", "NNP", "NNB", "NR", "XR", "XPN", "SL", "SN", "SH", "VV", "VA", "MAG"})
KO_NOUN = frozenset({"NNG", "NNP"})
# Sudachi: 명사·동사·형용사·형상사·접두사 유지
JA_KEEP = frozenset({"名詞", "動詞", "形容詞", "形状詞", "接頭辞"})


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi

    return Kiwi()


@lru_cache(maxsize=1)
def _sudachi():
    from sudachipy import Dictionary

    return Dictionary(dict="core").tokenizer()


def lang_of_text(text: str) -> str:
    kana, hangul = len(_KANA.findall(text)), len(_HANGUL.findall(text))
    if kana and kana >= hangul:
        return "ja"
    if hangul:
        return "ko"
    return "en"


def _tok_ko(text: str) -> list[str]:
    out: list[str] = []
    run: list[str] = []
    prefix = None
    for t in _kiwi().tokenize(text):
        tag = t.tag.split("-")[0]
        if tag in KO_KEEP:
            out.append((t.form + "다" if tag in ("VV", "VA") else t.form).lower())
        # 접두사+명사 결합형도 색인("비"+"급여" → "비급여") — 접두사가 뜻을 뒤집는 경우 보존
        if tag in KO_NOUN and prefix:
            out.append((prefix + t.form).lower())
        prefix = t.form if tag == "XPN" else None
        # 연속 명사는 결합형도 색인(원형 + 분해형). 결합형은 표기 그대로 둔다 — 색인(unicode61)이 대소문자를 접으므로 검색에는 영향 없음
        if tag in KO_NOUN:
            run.append(t.form)
        else:
            if len(run) > 1:
                out.append("".join(run))
            run = []
    if len(run) > 1:
        out.append("".join(run))
    return out


def _tok_ja(text: str) -> list[str]:
    from sudachipy import SplitMode

    out: list[str] = []
    for m in _sudachi().tokenize(text, SplitMode.C):
        pos = m.part_of_speech()[0]
        if pos not in JA_KEEP:
            continue
        out.append(m.dictionary_form() if pos in ("動詞", "形容詞") else m.normalized_form())
        if pos == "名詞":
            subs = m.split(SplitMode.A)
            if len(subs) > 1:  # 긴 단위(C)와 짧은 단위(A)를 함께 색인
                out.extend(s.normalized_form() for s in subs)
    return [t.lower() for t in out]


def _tok_en(text: str) -> list[str]:
    return _EN_WORD.findall(text.lower())


def tokenize_doc(text: str) -> list[str]:
    lang = lang_of_text(text)
    if lang == "en" and _HAN.search(text):
        # 가나·한글 없이 한자만 있으면 한·일 어느 쪽인지 알 수 없어 질의 쪽과 같이 두 분석기를 모두 쓴다
        return _tok_ko(text) + _tok_ja(text) + _tok_en(text)
    return {"ko": _tok_ko, "ja": _tok_ja, "en": _tok_en}[lang](text)


def tokenize_query(q: str) -> list[str]:
    if _KANA.search(q):
        toks = _tok_ja(q)
    elif _HANGUL.search(q):
        toks = _tok_ko(q)
    elif _HAN.search(q):
        toks = _tok_ko(q) + _tok_ja(q)
    else:
        toks = _tok_en(q)
    return list(dict.fromkeys(t for t in toks if t))
