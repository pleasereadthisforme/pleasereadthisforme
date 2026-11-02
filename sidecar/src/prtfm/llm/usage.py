from __future__ import annotations

from dataclasses import dataclass

# 100만 토큰당 USD (입력, 캐시 읽기 입력, 출력). 가격이 바뀌면 여기만 고친다.
PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-6-luna": (0.10, 0.01, 0.50),
    "gpt-6-sol": (2.00, 0.20, 10.00),
}


@dataclass(frozen=True)
class Usage:
    """호출 한 번의 토큰 사용량. 제공자 공통 4필드(캐시 쓰기는 Anthropic용, OpenAI는 0)."""

    input_uncached: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0

    def __add__(self, o: Usage) -> Usage:
        return Usage(self.input_uncached + o.input_uncached, self.cache_write + o.cache_write,
                     self.cache_read + o.cache_read, self.output + o.output)

    @property
    def input_total(self) -> int:
        return self.input_uncached + self.cache_write + self.cache_read


def from_openai(raw) -> Usage:
    details = getattr(raw, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    return Usage(raw.input_tokens - cached, 0, cached, raw.output_tokens)


def cost_usd(model: str, u: Usage) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    return (u.input_uncached * p[0] + u.cache_read * p[1] + u.output * p[2]) / 1e6
