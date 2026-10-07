"""Public attachment fetches validate redirects, pin DNS and scope credentials."""
from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import pytest

from kazma_core.security import ssrf, ssrf_pin
from kazma_gateway.adapters.downloads import public_attachment_download


@pytest.fixture
def network(monkeypatch):
    calls = []
    pins = {}
    replies = []
    original = ssrf.validate_url

    def validate(url, **kwargs):
        if urlsplit(url).hostname in ("files.slack.com", "public.example", "other.example"):
            return ("93.184.216.34",)
        return original(url, **kwargs)

    def handle(request):
        assert pins[request.url.host] == "93.184.216.34"
        calls.append(request)
        return replies.pop(0)

    def transport(mapping, **kwargs):
        nonlocal pins
        pins = mapping
        return httpx.MockTransport(handle)

    monkeypatch.setattr(ssrf, "validate_url", validate)
    monkeypatch.setattr(ssrf_pin, "PinHostAsyncTransport", transport)
    return calls, replies


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1/secret", "http://169.254.169.254/latest/meta-data", "https://localhost/private"])
async def test_private_initial_targets_are_never_contacted(network, url):
    calls, _ = network
    with pytest.raises(ValueError):
        await public_attachment_download(url)
    assert calls == []


@pytest.mark.asyncio
async def test_private_redirect_is_refused_before_the_second_request(network):
    calls, replies = network
    replies.append(httpx.Response(302, headers={"location": "http://127.0.0.1/private"}))
    with pytest.raises(ValueError):
        await public_attachment_download("https://public.example/start")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_slack_credential_does_not_follow_a_redirect(network):
    calls, replies = network
    replies.extend([httpx.Response(302, headers={"location": "https://other.example/file"}),
                    httpx.Response(200, content=b"proof")])
    assert await public_attachment_download("https://files.slack.com/file", slack_token="synthetic-token") == b"proof"
    assert calls[0].headers["authorization"] == "Bearer synthetic-token"
    assert "authorization" not in calls[1].headers


@pytest.mark.asyncio
async def test_arbitrary_initial_host_never_receives_a_slack_credential(network):
    calls, replies = network
    replies.append(httpx.Response(200, content=b"proof"))
    assert await public_attachment_download("https://public.example/file", slack_token="synthetic-token") == b"proof"
    assert "authorization" not in calls[0].headers


@pytest.mark.asyncio
async def test_redirect_loop_has_a_fixed_request_budget(network):
    calls, replies = network
    replies.extend(httpx.Response(302, headers={"location": "/again"}) for _ in range(4))
    with pytest.raises(ValueError, match="redirect limit"):
        await public_attachment_download("https://public.example/start")
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_oversized_body_cannot_override_the_stream_cap(network):
    _, replies = network
    replies.append(httpx.Response(200, headers={"content-length": "1"}, content=b"x" * 100))
    with pytest.raises(ValueError, match="size limit"):
        await public_attachment_download("https://public.example/file", max_bytes=8)
