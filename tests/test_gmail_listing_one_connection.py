"""A Gmail listing uses one connection and reads its mails a few at a time.

2026-11 functional audit, FA-242. Listing 20 mails was 21 requests, each in a
client of its own: a TLS handshake per mail, about 0.6 s each on the live
install. A listing now holds one client for all its requests, and reads up to
``_LIST_CONCURRENCY`` mails at once, in order.

Clients are counted on a fake Gmail (the real client over
``httpx.MockTransport``); the old per-request client is the negative control.
Graph and both calendars list in one request, so a listing there is already
one connection.
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest

from kazma_skills.native.email_manager.backends import gmail_api
from kazma_skills.native.email_manager.models import ListQuery

MAILS = [f"m{i:02d}" for i in range(20)]


class _Gmail:
    def __init__(self) -> None:
        self.in_flight = 0
        self.most = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/users/me/messages"):
            return httpx.Response(200, json={"messages": [{"id": m} for m in MAILS]})
        self.in_flight += 1
        self.most = max(self.most, self.in_flight)
        await asyncio.sleep(0.005)
        self.in_flight -= 1
        mid = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json={
            "id": mid, "threadId": "t", "labelIds": ["INBOX"], "snippet": "",
            "payload": {"headers": [{"name": "Subject", "value": f"mail {mid}"}]},
        })


@pytest.fixture
def gmail(monkeypatch):
    real = httpx.AsyncClient
    fake = _Gmail()
    made: list[int] = []

    def client(*args, **kwargs):
        kwargs.pop("verify", None)
        made.append(1)
        return real(*args, transport=httpx.MockTransport(fake), **kwargs)

    monkeypatch.setattr(gmail_api.httpx, "AsyncClient", client)
    backend = gmail_api.GmailApiBackend(access_token="demo-gmail-token")
    return backend, fake, made


def _list(backend):
    return asyncio.run(backend.list_messages(ListQuery(folder="INBOX", limit=20)))


def test_a_listing_is_one_connection_and_keeps_its_order(gmail):
    backend, fake, made = gmail
    mails = _list(backend)
    assert [m.subject for m in mails] == [f"mail {m}" for m in MAILS]
    assert len(made) == 1
    assert 1 < fake.most <= gmail_api._LIST_CONCURRENCY


def test_negative_control_a_client_per_request(gmail, monkeypatch):
    backend, _fake, made = gmail

    @contextlib.asynccontextmanager
    async def per_request():
        async with gmail_api.httpx.AsyncClient(timeout=45.0) as client:
            yield client

    monkeypatch.setattr(backend, "_connection", per_request)
    _list(backend)
    assert len(made) > 20  # one per mail, and more


def test_a_single_request_still_closes_its_own_client(gmail):
    backend, _fake, made = gmail
    asyncio.run(backend.get_message("m00"))
    assert len(made) == 1 and backend._client is None
