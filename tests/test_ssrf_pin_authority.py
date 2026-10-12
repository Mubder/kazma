"""IP pinning changes only the socket destination, never origin authority."""

from __future__ import annotations

import httpx
import httpcore
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
    assert request.url.host == sni
    assert request.url.port == port
    assert request.headers["host"] == authority
    assert request.extensions["sni_hostname"] == sni
    assert request.url.path == "/page"


@pytest.mark.asyncio
async def test_shared_ip_keeps_tls_authentication_separate_by_logical_origin():
    """Exercise the real HTTPX/httpcore pool, including keep-alive reuse."""
    connections = []
    handshakes = []

    class Stream(httpcore.AsyncMockStream):
        async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            handshakes.append(server_hostname)
            return self

    class Backend(httpcore.AsyncMockBackend):
        async def connect_tcp(self, host, port, **kwargs):
            connections.append((host, port))
            return Stream([b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"] * 3)

    transport = PinHostAsyncTransport({
        "first.example": "93.184.216.34", "second.example": "93.184.216.34",
    })
    transport._pool._network_backend.backend = Backend([])
    async with httpx.AsyncClient(transport=transport) as client:
        for host in ("first.example", "second.example", "first.example"):
            response = await client.get(f"https://{host}/page")
            assert response.text == "ok"
            assert response.extensions["kazma_pinned_ip"] == "93.184.216.34"
    assert handshakes == ["first.example", "second.example"]
    assert connections == [("93.184.216.34", 443)] * 2


@pytest.mark.asyncio
async def test_reused_socket_attests_its_original_pin_after_map_changes():
    pins = {"first.example": "93.184.216.34"}
    transport = PinHostAsyncTransport(pins)
    transport._pool._network_backend.backend = httpcore.AsyncMockBackend([
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok",
    ])
    async with httpx.AsyncClient(transport=transport) as client:
        first = await client.get("https://first.example/")
        pins["first.example"] = "1.1.1.1"
        second = await client.get("https://first.example/")
    assert first.extensions["kazma_pinned_ip"] == "93.184.216.34"
    assert second.extensions["kazma_pinned_ip"] == "93.184.216.34"
    from kazma_core.security.ssrf import SSRFError, assert_peer_public

    with pytest.raises(SSRFError, match="could not be verified"):
        assert_peer_public(second, url="https://first.example/", validated_ips=["1.1.1.1"],
                           require_peer=True)


@pytest.mark.parametrize("kwargs", [{"proxy": "http://proxy.example:8080"}, {"uds": "/socket"}])
def test_pin_transport_refuses_non_direct_destinations(kwargs):
    from kazma_core.security.ssrf import SSRFError

    with pytest.raises(SSRFError, match="direct TCP"):
        PinHostAsyncTransport({"first.example": "93.184.216.34"}, **kwargs)
