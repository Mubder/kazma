"""A provider 5xx from ``LLMProvider.chat`` is a transient ``LLMError``.

Audit 2026-10-26 AUD-014: the last ``else`` of the non-streaming error
handling raised ``transient=False`` for every status, 500/502/503 included,
so a provider outage inside ``resilient_chat`` failed fast with no retry and
no failover. AGENTS.md §3: 429 and 5xx are transient, 4xx content is
permanent. The streaming path and ``_http_status_is_transient`` already said
so; this holds the non-streaming path to the same rule, through a real
``httpx`` transport.
"""

from __future__ import annotations

import asyncio
import inspect
import re

import httpx
import pytest

from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider

BASE = "https://llm.example.invalid/v1"


def _provider(status: int, body: str = '{"error":{"message":"upstream is down"}}') -> LLMProvider:
    provider = LLMProvider(LLMConfig(base_url=BASE, api_key="demo-key", model="demo-model"))

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url=BASE)

    async def get_client():
        return client

    provider._get_client = get_client
    return provider


def _raised(status: int) -> LLMError:
    provider = _provider(status)
    with pytest.raises(LLMError) as info:
        asyncio.run(provider.chat([{"role": "user", "content": "hi"}]))
    return info.value


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_a_provider_5xx_is_transient(status):
    err = _raised(status)
    assert err.transient is True, f"HTTP {status} must be retryable (§3)"
    assert f"HTTP {status}" in str(err)


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_a_provider_4xx_stays_permanent(status):
    assert _raised(status).transient is False


def test_negative_control_the_old_branch_hard_coded_permanent(monkeypatch):
    """The old final branch hard-coded ``transient=False``. Re-create that
    rule by making the classifier answer False for everything: the same 503
    then comes out permanent -- the behaviour the tests above reject."""
    import kazma_core.llm_provider as lp

    monkeypatch.setattr(lp, "_http_status_is_transient", lambda _sc: False)
    assert _raised(503).transient is False

    # And the branch decides by the classifier, not by a literal.
    src = inspect.getsource(LLMProvider._chat_inner)
    tail = src[src.index("Name the model AND the endpoint"):]
    branch = tail[: tail.index("except LLMError:")]
    assert "transient=_http_status_is_transient(status_code)" in branch
    assert not re.search(r"transient=False", branch)
