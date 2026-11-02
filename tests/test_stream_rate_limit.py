"""A streaming 429 waits and retries, bounded, as the blocking call does (FA-214).

The 2026-11 functional audit: one 429 on the stream path ended the turn. The
blocking ``chat()`` honoured ``Retry-After`` with three bounded retries; the
stream raised ``rate_limit_exhausted`` at once, which the supervisor does not
retry on the same provider (the provider "already backed off"), so a single
rate limit was the turn's answer -- worded "I lost the connection to the
model mid-turn".

The provider is real (``LLMProvider``) on a scripted transport; the call
goes through ``invoke_llm_chat``, the supervisor's own call. The waits are
recorded, not slept.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from kazma_core import llm_provider
from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider
from kazma_core.llm_stream import invoke_llm_chat
from kazma_core.retry import friendly_llm_error

ANSWER = "Three pull requests are open."
_SSE = (
    f'data: {json.dumps({"choices": [{"delta": {"content": ANSWER}}]})}\n\n'
    f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})}\n\n'
    "data: [DONE]\n\n"
).encode("utf-8")


@pytest.fixture()
def waits(monkeypatch):
    """The provider's backoff sleeps, recorded instead of slept."""
    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *a, **k):
        recorded.append(float(seconds))
        await real_sleep(0)

    monkeypatch.setattr(llm_provider.asyncio, "sleep", fake_sleep)
    return recorded


def _provider(monkeypatch, refusals: int, *, body: str = '{"error":"rate limited"}'):
    """A provider whose first *refusals* stream requests answer 429."""
    prov = LLMProvider(LLMConfig(base_url="http://test", api_key="k", model="stub"))
    seen = {"requests": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["requests"] += 1
        if seen["requests"] <= refusals:
            return httpx.Response(429, headers={"Retry-After": "2"}, text=body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_SSE)

    transport = httpx.MockTransport(handler)

    async def get_client():
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    monkeypatch.setattr(prov, "_get_client", get_client)
    monkeypatch.setattr(prov, "_sync_gateway_async", _no_gateway)
    return prov, seen


async def _no_gateway():
    return None


def _ask(prov):
    return asyncio.run(invoke_llm_chat(prov, [{"role": "user", "content": "which PRs are open?"}]))


def test_a_429_then_an_answer_answers_the_turn(monkeypatch, waits):
    prov, seen = _provider(monkeypatch, refusals=1)
    response = _ask(prov)
    assert response.content == ANSWER
    assert seen["requests"] == 2
    assert waits == [2.0]  # the provider's Retry-After


def test_three_429s_then_an_answer_still_answer(monkeypatch, waits):
    prov, seen = _provider(monkeypatch, refusals=3)
    assert _ask(prov).content == ANSWER
    assert waits == [2.0, 3.0, 4.5]  # bounded, growing, as chat() waits


def test_a_provider_that_keeps_refusing_says_how_many_attempts(monkeypatch, waits):
    prov, seen = _provider(monkeypatch, refusals=10)
    with pytest.raises(LLMError) as raised:
        _ask(prov)
    exc = raised.value
    assert seen["requests"] == 4 and exc.attempts == 4
    assert exc.kind == "rate_limit_exhausted" and exc.transient  # failover may still try another
    assert exc.waited_s == pytest.approx(9.5)
    text = friendly_llm_error(exc, model="stub")
    assert text.startswith("⚠️") and "429" in text
    assert "refused 4 requests in a row, over 10 seconds" in text
    assert "lost the connection" not in text


def test_an_empty_balance_is_not_retried(monkeypatch, waits):
    """A 429 that says the account has no credit is permanent: no wait."""
    prov, seen = _provider(
        monkeypatch, refusals=10,
        body='{"error":{"code":"1113","message":"Insufficient balance or no resource package"}}',
    )
    with pytest.raises(LLMError) as raised:
        _ask(prov)
    assert seen["requests"] == 1 and waits == []
    assert raised.value.transient is False


def test_the_old_stream_path_ended_the_turn_on_one_429(monkeypatch, waits):
    """Negative control: with no retry on the stream (the code before the
    fix), one 429 is the turn's answer, worded as a lost connection."""
    monkeypatch.setattr(llm_provider, "_RATE_LIMIT_RETRIES", 0)
    prov, seen = _provider(monkeypatch, refusals=1)
    with pytest.raises(LLMError):
        _ask(prov)
    assert seen["requests"] == 1 and waits == []
    old_wording = friendly_llm_error(LLMError("x", transient=True))
    assert "lost the connection" in old_wording


def test_the_blocking_path_counts_its_attempts_too(monkeypatch, waits):
    """chat() had the backoff; its error now carries what it tried."""
    monkeypatch.setenv("KAZMA_LLM_STREAM", "0")
    prov, seen = _provider(monkeypatch, refusals=10)
    with pytest.raises(LLMError) as raised:
        _ask(prov)
    assert raised.value.attempts == 4 and waits == [2.0, 3.0, 4.5]
