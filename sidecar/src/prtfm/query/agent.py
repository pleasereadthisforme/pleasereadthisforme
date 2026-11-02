from __future__ import annotations

from prtfm.localization import text as _ui_text

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field

from prtfm.llm.openai_client import InputTooLarge, TextDelta, TurnResult
from prtfm.llm.usage import Usage
from prtfm.query.prompts import FINAL_SYSTEM, SYSTEM
from prtfm.query.review import REVIEW, caption_map, needs_review
from prtfm.query.tools import TOOL_SPECS, fence

MAX_ITERS, INPUT_BUDGET, HISTORY_TURNS = 8, 40_000, 6
FORCE_MSG = "Tool budget reached. Answer now using only what you have found, with citations."
UNVERIFIED = "답변의 인용을 현재 읽은 자료에서 확인하지 못했습니다. 질문 범위를 좁혀 다시 요청해 주세요."
_BRACKET = re.compile(r"[\[〔［]([^\[\]〔〕［］\n]+)[\]〕］]")
_CHUNK_ID = re.compile(r"(?<![\w@#])[0-9]+(?:@[0-9]+)?#[0-9]+(?![\w@#])")


@dataclass(frozen=True)
class ToolStart:
    name: str
    args: dict


@dataclass(frozen=True)
class ToolEnd:
    name: str


@dataclass(frozen=True)
class Delta:
    text: str


@dataclass(frozen=True)
class Done:
    answer: str
    cited: list[str]
    calls: list[Usage] = field(default_factory=list)
    iterations: int = 0
    forced: bool = False
    rejected_citations: list[str] = field(default_factory=list)
    automatic_reads: int = 0
    reviewed: bool = False


def _citations(text: str) -> list[str]:
    """대괄호 묶음 안의 청크 id를 모두 꺼낸다("[12#3, 12#4]"도 각각). 처음 나온 순서로 중복 제거."""
    ids = [m for g in _BRACKET.findall(text) for m in _CHUNK_ID.findall(g)]
    return list(dict.fromkeys(ids))


def run_agent(llm, tools, question: str, history: list[tuple[str, str]], *,
              review_trace: dict | None = None) -> Iterator[ToolStart | ToolEnd | Delta | Done]:
    """검색·읽기를 반복해 근거를 확인하고 답한다. 상한에 닿으면 도구 없이 지금까지의 근거로 답하게 한다."""
    items: list[dict] = []
    for q, a in history[-HISTORY_TURNS:]:
        items += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    items.append({"role": "user", "content": question})
    prior_context = items[:-1]
    calls: list[Usage] = []
    iterations, forced = 0, False
    compact = None
    attempted_reads: set[str] = set()
    previous_read_count, stalled = 0, 0
    reviewing, draft = False, ''
    while True:
        force = forced or iterations >= MAX_ITERS or sum(u.input_total for u in calls) >= INPUT_BUDGET
        # 로컬 읽기는 모델 호출을 추가하지 않는다. 실패 후보도 자동 재시도하지 않는다.
        if compact is None and (force or stalled >= 2 or sum(u.input_total for u in calls) >= INPUT_BUDGET // 2):
            candidates = [cid for cid in getattr(tools, 'read_candidates', [])
                          if cid not in tools.read_snapshots and cid not in attempted_reads]
            for cid in candidates[:min(2, 6 - len(attempted_reads))]:
                if cid in tools.read_snapshots:
                    continue
                attempted_reads.add(cid)
                args = {'chunk_id': cid, 'neighbors': 1}
                yield ToolStart('read', args)
                out = tools.call('read', args)
                yield ToolEnd('read')
                items.append({'role': 'user', 'content': 'Automatically read search candidate:\n' + out})
            stalled = 0
        finalizing = force or reviewing
        if finalizing:
            forced = forced or force
            if compact is None:
                compact = list(tools.read_snapshots.values())
            # 도구 호출/출력 쌍·암호화 reasoning을 부분 절단하지 않는다.
            items = prior_context + [{"role": "user", "content": question},
                     {"role": "user", "content": fence(compact)},
                     {"role": "user", "content": (REVIEW if reviewing else FORCE_MSG + '\n' + REVIEW) +
                      " Only cite the supplied read snapshots. State which parts remain unresolved."}]
            if reviewing:
                items.insert(len(prior_context) + 1,
                             {'role': 'user', 'content': fence({'unverified_draft': draft})})
            tables = caption_map(compact)
            if tables:
                items.insert(-1, {'role': 'user', 'content': fence({'caption_map': tables})})
        result: TurnResult | None = None
        try:
            for ev in llm.turn(FINAL_SYSTEM if finalizing else SYSTEM, items,
                               TOOL_SPECS, "none" if finalizing else "auto"):
                if not isinstance(ev, TextDelta):
                    result = ev
        except InputTooLarge:
            if finalizing:
                if draft:
                    draft = ''
                    forced = True
                    continue
                if prior_context:
                    # 원문보다 먼저 오래된 대화를 축소한다. 요청 상한은 그대로 유지한다.
                    prior_context = prior_context[2:]
                    continue
                if not compact:
                    raise
                compact = compact[:len(compact) // 2]
            forced = True
            continue
        if result is None:
            raise RuntimeError("missing turn result")
        iterations += 1
        calls.append(result.usage)
        items += result.output
        fcalls = [o for o in result.output if o.get("type") == "function_call"]
        if not fcalls or finalizing:
            if (not finalizing and iterations < MAX_ITERS
                    and sum(u.input_total for u in calls) < INPUT_BUDGET
                    and needs_review(question, list(tools.read_snapshots.values()))):
                reviewing, draft = True, result.text
                if review_trace is not None:
                    review_trace.update(draft=draft, draft_cited=_citations(draft),
                                        draft_call_index=len(calls) - 1)
                continue
            cited = _citations(result.text)
            allowed = {s["chunk_id"] for s in compact} if compact is not None else tools.read_snapshots.keys()
            answer = result.text
            rejected = [cid for cid in cited if cid not in allowed]
            if rejected:
                answer, cited = _ui_text(UNVERIFIED), []
            if review_trace is not None and reviewing:
                review_trace.update(final=answer, final_cited=cited, changed=answer != review_trace['draft'],
                                    review_call_index=len(calls) - 1)
            # 검증 전 텍스트를 전송하면 SSE에서 취소할 수 없으므로 최종 답변만 내보낸다.
            if answer:
                yield Delta(answer)
            yield Done(answer, cited, calls, iterations, forced, rejected, len(attempted_reads), reviewing)
            return
        for c in fcalls:
            name = c.get("name", "")
            try:
                args = json.loads(c.get("arguments") or "{}")
                if not isinstance(args, dict):
                    raise ValueError
            except ValueError:
                out = fence("arguments were not valid JSON")
                yield ToolStart(name, {})
            else:
                yield ToolStart(name, args)
                out = tools.call(name, args)
            yield ToolEnd(name)
            items.append({"type": "function_call_output", "call_id": c["call_id"], "output": out})
        stalled = stalled + 1 if len(tools.read_snapshots) == previous_read_count else 0
        previous_read_count = len(tools.read_snapshots)
