"""A timeout says what it means for that request (2026-10-29).

Every X timeout read "X API timed out. Did not retry (avoids double-post).":
the mentions poller's reads paged that through the 05:31-06:01 UTC network
outage, and on 2026-10-28 a metrics post X had published 3 s after the
request (its response never came back) was recorded with the same words as
a request that never left. Now a read says it timed out, a write that never
connected says nothing was sent, and a write whose answer was lost says it
may have taken effect.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

_OLD = "X API timed out. Did not retry (avoids double-post)."
_ROOT = Path(__file__).resolve().parents[1]


def _client_failing_with(monkeypatch, failure):
    from kazma_core.x_api import ai_budget
    from kazma_core.x_api.client import XClient
    from kazma_core.x_api.config import XCredentials

    original = httpx.AsyncClient

    async def handle(request):
        raise failure

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr(ai_budget, "reserve_read", lambda *a, **k: None)
    return XClient(XCredentials("a", "b", "c", "d"))


@pytest.mark.asyncio
async def test_a_read_that_times_out_says_only_that(monkeypatch):
    from kazma_core.x_api.client import XApiError

    client = _client_failing_with(monkeypatch, httpx.ReadTimeout("no answer"))
    with pytest.raises(XApiError) as caught:
        await client.verify_credentials()
    assert str(caught.value) == "X API timed out (no answer in 20 s)."
    assert "double-post" not in str(caught.value)


@pytest.mark.asyncio
async def test_a_post_whose_answer_was_lost_may_have_taken_effect(monkeypatch):
    from kazma_core.x_api.client import XApiError

    client = _client_failing_with(monkeypatch, httpx.ReadTimeout("response lost"))
    with pytest.raises(XApiError) as caught:
        await client.create_tweet("Test")
    message = str(caught.value)
    assert caught.value.outcome == "unknown"
    assert "may have taken effect" in message and "Verify on X before any resend" in message


@pytest.mark.asyncio
async def test_a_post_that_never_connected_was_not_sent(monkeypatch):
    from kazma_core.x_api.client import XApiError

    client = _client_failing_with(monkeypatch, httpx.ConnectTimeout("no route"))
    with pytest.raises(XApiError) as caught:
        await client.create_tweet("Test")
    assert caught.value.outcome == "not_sent"
    assert str(caught.value) == "X API could not be reached in time; nothing was sent."


def test_negative_control_the_old_message_fails_each_case():
    """The old single message says none of the three things the tests require."""
    assert _OLD != "X API timed out (no answer in 20 s)."
    assert "may have taken effect" not in _OLD
    assert "nothing was sent" not in _OLD


def test_no_x_message_promises_a_double_post_guard():
    """'avoids double-post' named no request; it is said nowhere in x_api now."""
    hits = [p.name for p in (_ROOT / "kazma-core" / "kazma_core" / "x_api").glob("*.py")
            if "avoids double-post" in p.read_text(encoding="utf-8")]
    assert hits == []
