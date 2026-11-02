"""임베딩 요청은 예산 예약 후 전송하고, 결과 불명 비용도 남긴다."""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal

import tiktoken

from prtfm.config import SEMANTIC_LIMITS
from prtfm.credentials.provider import get_key
from prtfm.store.vectors import DIMENSIONS, MODEL, validate


class EmbeddingError(Exception):
    pass


class Cancelled(EmbeddingError):
    pass


def nusd(value) -> int:
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError('budget must be positive and finite')
    return int((number * 10**9).to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class Price:
    per_million: str
    checked_at: str

    def __post_init__(self):
        nusd(self.per_million)
        if not self.checked_at:
            raise ValueError('price verification date required')

    def cost(self, tokens: int) -> int:
        return int((Decimal(self.per_million) * tokens * 1000).to_integral_value(rounding=ROUND_CEILING))


def configured_price() -> Price | None:
    value, date = os.getenv('PRTFM_EMBEDDING_PRICE_PER_M'), os.getenv('PRTFM_EMBEDDING_PRICE_CHECKED_AT')
    # App-maintained standard (non-Batch) API price, not an end-user setting.
    # Source: https://developers.openai.com/api/docs/models/text-embedding-3-small
    # Verified 2026-10-24. Update with the model when provider pricing changes.
    if value is None and date is None:
        return Price('0.02', '2026-10-24') if MODEL == 'text-embedding-3-small' else None
    # Explicit operator overrides must provide both fields; never mix prices/dates.
    return Price(value, date) if value and date else None


def token_count(text: str) -> int:
    return len(tiktoken.encoding_for_model(MODEL).encode(text, disallowed_special=()))


class Pacer:
    """프로세스 내 문서·질의 요청이 한 토큰 창을 공유한다."""

    def __init__(self, limit=30_000):
        if not 1 <= limit <= 30_000:
            raise ValueError('TPM must be between 1 and 30000')
        self.limit = limit
        self.window = deque()
        self.lock = threading.Lock()

    def reserve(self, tokens, cancel):
        if tokens > self.limit:
            raise EmbeddingError('request exceeds configured TPM')
        while True:
            if cancel.is_set():
                raise Cancelled('cancelled')
            with self.lock:
                now = time.monotonic()
                while self.window and now - self.window[0][0] >= 60:
                    self.window.popleft()
                if sum(n for _, n in self.window) + tokens <= self.limit:
                    self.window.append((now, tokens))
                    return
            cancel.wait(0.1)


PACER = Pacer(int(os.getenv('PRTFM_EMBEDDING_TPM', '30000')))


class OpenAITransport:
    def __init__(self, cancel):
        self.cancel = cancel

    def __call__(self, texts):
        from prtfm.llm.provider import create_client

        async def request():
            async with create_client(asynchronous=True, max_retries=0, timeout=SEMANTIC_LIMITS.timeout_seconds) as client:
                task = asyncio.create_task(client.embeddings.create(
                    model=MODEL, dimensions=DIMENSIONS, encoding_format='float', input=texts))
                try:
                    async with asyncio.timeout(SEMANTIC_LIMITS.timeout_seconds):
                        while not task.done():
                            if self.cancel.is_set():
                                raise Cancelled('cancelled')
                            await asyncio.wait({task}, timeout=0.1)
                        return await task
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        return asyncio.run(request())

    def close(self):
        pass


class Embedder:
    def __init__(self, con, job_id, price: Price, budget, transport=None, cancel=None, pacer=None,
                 kind='document'):
        self.con, self.job_id, self.price = con, job_id, price
        self.budget = nusd(budget)
        self.transport = transport
        self.cancel = cancel or threading.Event()
        self.pacer = pacer or PACER
        self.kind = kind

    def summary(self):
        row = self.con.execute('''SELECT COALESCE(SUM(actual_nusd),0),
            COALESCE(SUM(CASE WHEN state IN ('reserved','unknown') THEN reserved_nusd ELSE 0 END),0)
            FROM semantic_usage WHERE job_id=?''', (self.job_id,)).fetchone()
        return {'cost_usd': row[0] / 1e9, 'unknown_reserved_usd': row[1] / 1e9}

    def _reserve(self, tokens):
        cost = self.price.cost(tokens)
        with self.con:
            spent = self.con.execute('''SELECT COALESCE(SUM(COALESCE(actual_nusd,reserved_nusd)),0)
                FROM semantic_usage WHERE job_id=?''', (self.job_id,)).fetchone()[0]
            if spent + cost > self.budget:
                raise EmbeddingError('budget exhausted')
            request_id = uuid.uuid4().hex
            self.con.execute('INSERT INTO semantic_usage VALUES (?,?,?,?,?,?,?,?)',
                             (request_id, self.job_id, self.kind, tokens, cost, None, 'reserved', time.time()))
        return request_id

    def _settle(self, request_id, state, tokens=None):
        with self.con:
            self.con.execute('UPDATE semantic_usage SET state=?,actual_nusd=? WHERE id=?',
                             (state, self.price.cost(tokens) if tokens is not None else None, request_id))

    def embed(self, texts):
        tokens = sum(token_count(t) for t in texts)
        if (not texts or any(not t.strip() for t in texts) or len(texts) > SEMANTIC_LIMITS.batch_chunks
                or tokens > SEMANTIC_LIMITS.batch_tokens):
            raise EmbeddingError('invalid embedding batch')
        for attempt in range(SEMANTIC_LIMITS.retries_429 + 1):
            self.pacer.reserve(tokens, self.cancel)
            if self.cancel.is_set():
                raise Cancelled('cancelled')
            # 클라이언트 구성 오류는 전송 전이므로 예약하지 않는다.
            if self.transport is None:
                # 키 누락은 네트워크 전송 전에 검출한다.
                if not get_key():
                    raise EmbeddingError('API key missing')
                self.transport = OpenAITransport(self.cancel)
            rid = self._reserve(tokens)
            try:
                result = self.transport(texts)
            except Exception as exc:
                if getattr(exc, 'status_code', None) == 429:
                    self._settle(rid, 'rejected', 0)
                    if attempt < SEMANTIC_LIMITS.retries_429:
                        if self.cancel.wait(2 ** attempt):
                            raise Cancelled('cancelled') from exc
                        continue
                else:
                    self._settle(rid, 'unknown')
                if isinstance(exc, Cancelled):
                    raise
                raise EmbeddingError(type(exc).__name__) from None
            try:
                used = result.usage.total_tokens
                if type(used) is not int or used < 0:
                    raise ValueError('invalid usage')
                self._settle(rid, 'confirmed', used)
                rows = sorted(result.data, key=lambda item: item.index)
                if [r.index for r in rows] != list(range(len(texts))):
                    raise ValueError('invalid embedding indices')
                for row in rows:
                    validate(row.embedding)
                if self.price.cost(used) > self.price.cost(tokens):
                    raise EmbeddingError('usage exceeded estimate')
                if self.cancel.is_set():
                    raise Cancelled('cancelled')
                return [r.embedding for r in rows]
            except Exception:
                # usage까지 유효하지 않은 응답은 예약을 유지한다.
                with self.con:
                    self.con.execute("UPDATE semantic_usage SET state='unknown' WHERE id=? AND state='reserved'", (rid,))
                raise
        raise EmbeddingError('retry limit')

    def close(self):
        if self.transport is not None and hasattr(self.transport, 'close'):
            self.transport.close()
