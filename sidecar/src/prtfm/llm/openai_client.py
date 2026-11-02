from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass

from prtfm.llm.usage import Usage, from_openai

_FAIL_EVENTS = ("response.failed", "response.incomplete", "error")


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class TurnResult:
    output: list[dict]
    text: str
    usage: Usage


class LLMError(Exception):
    """메시지에는 예외·이벤트 타입 이름만 담는다(키·본문이 로그로 새지 않게)."""


class InputTooLarge(LLMError):
    """생성 요청을 보내기 전 입력 상한 초과."""


class InputBudgetExceeded(InputTooLarge):
    """질문 전체의 입력 예산이 부족하여 생성 요청을 보내지 않음."""


class ResponsesClient:
    """OpenAI Responses API 1턴 호출. store=False라 대화 상태는 호출자가 입력 항목으로 들고 다닌다."""

    def __init__(self, api_key: str | None, model: str, client=None):
        # Load the SDK only when a client is needed, not when the UI server imports us.
        import openai

        self._sdk = openai
        self.model = model
        from prtfm.llm.provider import create_client
        self._client = client or create_client(api_key=api_key, max_retries=2, timeout=60)
        self.activity = None
        self.request_number = 0
        self.input_budget = None
        self.final_input_reserve = 0
        self.input_reserved = 0
        self.input_uncertain = False

    def begin_input_budget(self, limit=40_000, *, final_reserve=0):
        """One sequential query; reserve before create, retain unknown reservations."""
        if type(limit) is not int or limit <= 0:
            raise ValueError('invalid input budget')
        if type(final_reserve) is not int or not 0 <= final_reserve < limit:
            raise ValueError('invalid final input reserve')
        self.input_budget, self.input_reserved, self.input_uncertain = limit, 0, False
        self.final_input_reserve = final_reserve
        # A transport retry could bill an additional request outside our reservation.
        if isinstance(self._client, self._sdk.OpenAI):
            self._client = self._client.with_options(max_retries=0)

    def close(self) -> None:
        """내부 HTTP 연결 풀을 닫는다."""
        self._client.close()

    def turn(self, instructions: str, input_items: list[dict], tools: list[dict],
             tool_choice: str = "auto") -> Iterator[TextDelta | TurnResult]:
        self.request_number += 1
        request_id = self.request_number
        def emit(kind, snapshot=None, **fields):
            if self.activity is not None:
                self.activity.emit(kind, snapshot, request_id=request_id, **fields)
        request = dict(model=self.model, instructions=instructions, input=input_items, tools=tools,
                       tool_choice=tool_choice, store=False, include=['reasoning.encrypted_content'],
                       stream=True, truncation='disabled')
        emit('model_request_prepared', request)
        completed = False
        reserved = False
        try:
            if self.input_budget is not None and self.input_uncertain:
                emit('model_request_blocked', reason='input_usage_unknown')
                raise InputBudgetExceeded('previous input usage is unknown; query stopped')
            emit('token_count_sent', {k: v for k, v in request.items()
                                      if k not in ('store', 'include', 'stream')})
            count = self._client.responses.input_tokens.count(
                model=self.model, instructions=instructions, input=input_items,
                tools=tools, tool_choice=tool_choice, truncation="disabled").input_tokens
            if type(count) is not int or count < 0:
                raise LLMError("invalid input token count")
            emit('token_count_completed', input_tokens=count)
            if count > 40_000:
                emit('model_request_blocked', reason='input_limit')
                raise InputTooLarge("input token limit exceeded")
            if self.input_budget is not None:
                if self.input_reserved + count > self.input_budget:
                    emit('model_request_blocked', reason='cumulative_input_limit',
                         input_tokens=count, remaining_input_tokens=self.input_budget - self.input_reserved)
                    raise InputBudgetExceeded('cumulative input token limit exceeded')
                ceiling = self.input_budget - self.final_input_reserve
                if tool_choice != 'none' and self.input_reserved + count > ceiling:
                    emit('model_request_blocked', reason='final_input_reserve',
                         input_tokens=count, remaining_input_tokens=self.input_budget - self.input_reserved,
                         final_input_reserve=self.final_input_reserve)
                    raise InputBudgetExceeded('input space reserved for final answer')
                self.input_reserved += count
                reserved = True
            # with: 정상 종료·오류·호출자가 생성기를 닫는 경우 모두 HTTP 스트림을 바로 닫는다
            emit('model_request_sent', request, boundary='sdk_create_invoked')
            with self._client.responses.create(**request) as stream:
                for ev in stream:
                    if ev.type == "response.output_text.delta":
                        yield TextDelta(ev.delta)
                    elif ev.type == "response.completed":
                        r = ev.response
                        if reserved and r.usage is None:
                            raise LLMError('input usage missing')
                        usage = from_openai(r.usage)
                        if reserved:
                            # Never refund based on a smaller reported count. Stop on
                            # missing usage or an unexpectedly larger actual input.
                            if r.usage is None or usage.input_total > count:
                                self.input_uncertain = True
                                self.input_reserved += max(0, usage.input_total - count)
                                raise LLMError('input usage differs from reservation')
                        emit('model_request_completed', {
                            'output': [item.model_dump(exclude_none=True, by_alias=True) for item in r.output],
                            'text': r.output_text or '', 'usage': asdict(from_openai(r.usage))})
                        completed = True
                        # by_alias: 다시 입력으로 보낼 dict라 SDK 필드명(async_)이 아닌 API 필드명(async)을 쓴다
                        yield TurnResult([item.model_dump(exclude_none=True, by_alias=True) for item in r.output],
                                         r.output_text or "", from_openai(r.usage))
                        return
                    elif ev.type in _FAIL_EVENTS:
                        raise LLMError(ev.type)
        except self._sdk.OpenAIError as e:
            raise LLMError(type(e).__name__) from None
        finally:
            if not completed:
                if reserved:
                    self.input_uncertain = True
                emit('model_request_unfinished', usage='unknown_or_not_sent')
        raise LLMError("stream ended without completion")
