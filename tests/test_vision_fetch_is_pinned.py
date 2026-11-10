"""An image URL is fetched on the address that was validated.

Security audit 2026-11-10. ``analyze_image`` (read tier: no approval card)
validated the URL's DNS answer, then fetched it with the shared pooled
client, which resolved the name again for the real request. A name that
answers a public address and then an internal one (DNS rebinding) made
Kazma send one GET to the internal address; the peer check ran only after
that request was sent. The download now goes through
``web_acquire.http.fetch_public``: every hop validated, the connection
pinned to the validated address, the peer attested before the body is read,
the body bounded at ``MAX_DOWNLOAD_BYTES``.
"""

from __future__ import annotations

import io

import httpx
import pytest
from kazma_core.tools import vision_analyze

VALIDATED = "93.184.216.34"
INTERNAL = "10.0.0.5"


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def acquisition(monkeypatch: pytest.MonkeyPatch) -> dict:
    """The acquisition layer for real; the DNS worker and the socket are
    stand-ins. The stand-in socket connects where the pin map says, as
    ``PinHostAsyncTransport`` does, and attests that address."""
    from kazma_core.proxy import client as proxy_client
    from kazma_core.web_acquire import dns_worker

    seen: dict = {"connected_to": [], "pin_maps": []}
    answers = {"n": 0}

    def _rebinding_dns(host: str) -> list[str]:
        # First answer public (what the check sees), every later one internal.
        answers["n"] += 1
        return [VALIDATED] if answers["n"] == 1 else [INTERNAL]

    monkeypatch.setattr("kazma_core.security.ssrf._resolve_host_ips", _rebinding_dns)

    async def _validate(url: str, validator=None):
        return [VALIDATED]

    monkeypatch.setattr(dns_worker, "validate_public_url", _validate)
    monkeypatch.setattr(proxy_client, "get_active_proxy_url", lambda: None)

    def _client(*, pin_hosts=None, **kwargs):
        seen["pin_maps"].append(pin_hosts)

        def _socket(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            address = (pin_hosts or {}).get(host) or _rebinding_dns(host)[0]
            seen["connected_to"].append(address)
            ext = {"kazma_pinned_ip": address} if pin_hosts and host in pin_hosts else {}
            return httpx.Response(200, headers={"content-type": "image/png"},
                                  content=seen.get("body", _png()), extensions=ext)

        return httpx.AsyncClient(transport=httpx.MockTransport(_socket),
                                 follow_redirects=False)

    monkeypatch.setattr(proxy_client, "get_scraping_client", _client)
    return seen


async def test_the_download_connects_to_the_validated_address(acquisition) -> None:
    data, mime = await vision_analyze._download_image("https://rebind.example/cat.png")
    assert mime == "image/png" and data.startswith(b"\x89PNG")
    assert acquisition["connected_to"] == [VALIDATED]


async def test_an_oversized_body_is_refused(acquisition, monkeypatch) -> None:
    monkeypatch.setattr(vision_analyze, "MAX_DOWNLOAD_BYTES", 32)
    acquisition["body"] = b"\x89PNG\r\n\x1a\n" + b"\0" * 500
    with pytest.raises(ValueError, match="too large|exceeds"):
        await vision_analyze._download_image("https://rebind.example/big.png")


async def test_control_an_unpinned_connection_reaches_the_rebound_address(
    acquisition, monkeypatch
) -> None:
    """Negative control: the same stand-in socket without a pin map (the old
    pooled client) resolves the name again and connects to the internal
    address -- the request is sent before any check can stop it. (The
    acquisition layer then refuses the unattested peer: too late for the
    request, which is the finding.)"""
    from kazma_core.proxy import client as proxy_client

    stand_in = proxy_client.get_scraping_client
    monkeypatch.setattr(proxy_client, "get_scraping_client",
                        lambda *, pin_hosts=None, **kw: stand_in(pin_hosts=None, **kw))
    with pytest.raises(ValueError, match="Blocked"):
        await vision_analyze._download_image("https://rebind.example/cat.png")
    assert acquisition["connected_to"] == [INTERNAL]


def test_the_pooled_client_is_no_longer_the_seam() -> None:
    import inspect

    source = inspect.getsource(vision_analyze._download_image)
    assert "fetch_public(" in source
    assert "get_http_client" not in source
