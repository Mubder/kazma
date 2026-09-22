"""IP pinning changes only the socket destination, never origin authority."""

from __future__ import annotations

import httpx
import pytest

from kazma_core.security.ssrf_pin import PinHostAsyncTransport


@pytest.mark.parametrize("url,authority,sni,port", [
    ("https://example.com:8443/page", "example.com:8443", "example.com", 8443),
    ("https://example.com/page", "example.com", "example.com", None),
    ("http://example.com:8080/page", "example.com:8080", "example.com", 8080),
    ("https://[2606:4700:4700::1111]:8443/page", "[2606:4700:4700::1111]:8443", "2606:4700:4700::1111", 8443),
])
async def test_pinned_request_preserves_logical_origin(url, authority, sni, port, monkeypatch):
    received = []

    async def capture(self, request):
        received.append(request)
        return httpx.Response(200, request=request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", capture)
    async with PinHostAsyncTransport({sni: "93.184.216.34"}) as transport:
        original = httpx.Request("GET", url)
        await transport.handle_async_request(original)
    request = received.pop()
    assert request.url.host == "93.184.216.34"
    assert request.url.port == port
    assert request.headers["host"] == authority
    assert request.extensions["sni_hostname"] == sni
    assert request.url.path == "/page"
