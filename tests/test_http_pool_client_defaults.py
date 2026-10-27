"""The shared HTTP client follows no redirect and uses the shared TLS context
(audit 2026-10-26 AUD-099).

``http_pool.get_http_client`` built its pooled client with
``follow_redirects=True`` and httpx's own TLS context. Any request through it
followed a 3xx to any host, a hop the caller never checked (the vision
downloader resolves its chain with every hop checked and must not depend on
remembering ``follow_redirects=False``), and building the context loaded the
CA bundle again outside the one context built at boot (§35).
"""

from __future__ import annotations

import asyncio
import ssl

import httpx
import pytest

from kazma_core import http_pool


@pytest.fixture
def fresh_pool(monkeypatch):
    monkeypatch.setattr(http_pool, "_client", None)
    yield
    client = http_pool._client
    if client is not None:
        asyncio.run(client.aclose())


def _ssl_context_of(client: httpx.AsyncClient) -> ssl.SSLContext:
    return client._transport._pool._ssl_context


def test_the_shared_client_follows_no_redirect(fresh_pool) -> None:
    assert http_pool.get_http_client().follow_redirects is False


def test_the_shared_client_uses_the_shared_tls_context(fresh_pool) -> None:
    from kazma_core.http_tls import shared_ssl_context

    shared = shared_ssl_context()
    assert isinstance(shared, ssl.SSLContext), "the shared context could not be built here"
    assert _ssl_context_of(http_pool.get_http_client()) is shared


def test_a_client_built_as_before_has_its_own_context() -> None:
    """Negative control: the old constructor (no ``verify=``) builds its own."""
    from kazma_core.http_tls import shared_ssl_context

    async def _go() -> ssl.SSLContext:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return _ssl_context_of(client)

    assert asyncio.run(_go()) is not shared_ssl_context()


def test_a_redirect_reaches_the_caller_unfollowed(fresh_pool) -> None:
    hops: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, text="metadata")

    async def _go() -> httpx.Response:
        client = http_pool.get_http_client()
        client._transport = httpx.MockTransport(_handler)
        return await client.get("http://public.example/image.png")

    resp = asyncio.run(_go())
    assert resp.status_code == 302
    assert hops == ["http://public.example/image.png"]


def test_the_old_client_followed_it() -> None:
    """Negative control: the client as it was built."""
    hops: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        hops.append(str(request.url))
        if request.url.host == "public.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, text="metadata")

    async def _go() -> httpx.Response:
        async with httpx.AsyncClient(
            follow_redirects=True, transport=httpx.MockTransport(_handler)
        ) as client:
            return await client.get("http://public.example/image.png")

    assert asyncio.run(_go()).status_code == 200
    assert hops[-1] == "http://169.254.169.254/latest"
