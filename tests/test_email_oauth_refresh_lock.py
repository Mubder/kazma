"""OAuth token-refresh race fix (audit M3).

Validates that concurrent 401 responses never invoke the token-refresh
endpoint more than once at a time. Without the ``asyncio.Lock``, N parallel
401s each POST a refresh simultaneously — for Microsoft (which rotates
refresh tokens) this can invalidate the token and log the account out, and it
races on ``self.access_token``. The lock serializes refreshes so at most one
is in flight at any moment.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


class _FakeResponse:
    """Minimal duck-type of httpx.Response for the _request path."""

    def __init__(self, status_code: int, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self.content = b"{}"
        self._json = json_data or {}
        self.text = "{}"

    def json(self) -> dict:
        return self._json


async def _run_concurrent_401_test(backend, first_url: str) -> tuple[int, int]:
    """Fire 5 concurrent _request calls that all 401 on first attempt.

    Returns ``(total_refreshes, max_concurrent_refreshes)``. The critical
    invariant is ``max_concurrent_refreshes == 1`` — refreshes are serialized
    by the lock, so no two token-endpoint POSTs overlap.
    """
    total_refreshes = 0
    active_refreshes = 0
    max_concurrent_refreshes = 0

    async def fake_refresh(self) -> None:
        nonlocal total_refreshes, active_refreshes, max_concurrent_refreshes
        total_refreshes += 1
        active_refreshes += 1
        max_concurrent_refreshes = max(max_concurrent_refreshes, active_refreshes)
        self.access_token = f"fresh_{total_refreshes}"
        # Hold long enough that queued coroutines pile up on the lock, so the
        # race (and its serialization) is observable.
        await asyncio.sleep(0.03)
        active_refreshes -= 1

    # All first attempts → 401; all retries (after a refresh) → 200.
    call_count = 0

    async def fake_request(self, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 5:
            return _FakeResponse(401)
        return _FakeResponse(200, {"id": "msg1"})

    with (
        patch.object(type(backend), "_refresh", fake_refresh),
        patch("httpx.AsyncClient.request", new=fake_request),
    ):
        await asyncio.gather(*(backend._request("GET", first_url) for _ in range(5)))

    return total_refreshes, max_concurrent_refreshes


@pytest.mark.asyncio
async def test_gmail_concurrent_401s_refresh_serialized() -> None:
    """Concurrent 401s must never run two refreshes at once."""
    from kazma_skills.native.email_manager.backends.gmail_api import GmailApiBackend

    backend = GmailApiBackend(
        access_token="expired", refresh_token="rt", client_id="cid", client_secret="sec"
    )
    total, max_overlap = await _run_concurrent_401_test(backend, "/users/me/messages")
    assert max_overlap == 1, (
        f"refreshes overlapped (max {max_overlap} in flight) — lock not serializing; "
        f"total refreshes={total}"
    )


@pytest.mark.asyncio
async def test_graph_concurrent_401s_refresh_serialized() -> None:
    """Microsoft rotates refresh tokens — concurrent refreshes can invalidate
    the token, so the lock must serialize (max 1 in flight)."""
    from kazma_skills.native.email_manager.backends.microsoft_graph import (
        MicrosoftGraphBackend,
    )

    backend = MicrosoftGraphBackend(
        access_token="expired", refresh_token="rt", client_id="cid", client_secret="sec"
    )
    total, max_overlap = await _run_concurrent_401_test(backend, "/me/messages")
    assert max_overlap == 1, (
        f"refreshes overlapped (max {max_overlap} in flight) — lock not serializing; "
        f"total refreshes={total}"
    )


def test_backends_have_token_lock() -> None:
    """Both backends must instantiate an asyncio.Lock for refresh serialization."""
    from kazma_skills.native.email_manager.backends.gmail_api import GmailApiBackend
    from kazma_skills.native.email_manager.backends.microsoft_graph import (
        MicrosoftGraphBackend,
    )

    g = GmailApiBackend(access_token="x")
    m = MicrosoftGraphBackend(access_token="x", client_id="c")
    assert isinstance(g._token_lock, asyncio.Lock)
    assert isinstance(m._token_lock, asyncio.Lock)
