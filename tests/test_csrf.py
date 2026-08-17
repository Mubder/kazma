"""Tests for the CSRF cross-origin mutation guard (kazma_ui/csrf.py).

Builds REAL starlette Request objects from ASGI scopes — a MagicMock would
auto-attribute ``.host`` and hide exactly the AttributeError class these
tests exist to catch (request.url.host vs request.url.hostname).
"""

from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.responses import Response

from kazma_ui.csrf import create_csrf_middleware


def _make_request(
    method: str = "POST",
    path: str = "/api/example",
    headers: dict[str, str] | None = None,
    server: tuple[str, int] = ("127.0.0.1", 9090),
) -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ],
        "query_string": b"",
        "server": server,
        "scheme": "http",
        "client": ("203.0.113.9", 54321),
    }
    return Request(scope)


async def _call_next(request: Request) -> Response:
    return Response("OK", status_code=200)


@pytest.mark.asyncio
async def test_same_origin_post_passes():
    """THE regression: a real browser POST (Origin present, same host) must
    reach the app — request.url.host used to raise AttributeError here."""
    mw = create_csrf_middleware()
    req = _make_request(headers={"origin": "http://127.0.0.1:9090"})
    resp = await mw(req, _call_next)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_cross_origin_post_rejected():
    mw = create_csrf_middleware()
    req = _make_request(headers={"origin": "http://evil.example"})
    resp = await mw(req, _call_next)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_origin_null_rejected():
    mw = create_csrf_middleware()
    req = _make_request(headers={"origin": "null"})
    resp = await mw(req, _call_next)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_referer_mismatch_rejected():
    mw = create_csrf_middleware()
    req = _make_request(headers={"referer": "https://evil.example/attack"})
    resp = await mw(req, _call_next)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_forwarded_host_accepted(monkeypatch):
    """Proxied deployments: X-Forwarded-Host is allowed only if it matches PUBLIC_URL."""
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example.com")
    mw = create_csrf_middleware()
    req = _make_request(
        headers={
            "origin": "https://kazma.example.com",
            "x-forwarded-host": "kazma.example.com",
        }
    )
    resp = await mw(req, _call_next)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_spoofed_forwarded_host_rejected(monkeypatch):
    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)
    mw = create_csrf_middleware()
    req = _make_request(
        headers={
            "origin": "https://evil.example",
            "x-forwarded-host": "evil.example",
        }
    )
    resp = await mw(req, _call_next)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_bypasses():
    mw = create_csrf_middleware()
    req = _make_request(method="GET", headers={"origin": "http://evil.example"})
    resp = await mw(req, _call_next)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_authorization_header_exempt():
    """Explicit credentials (API token / CLI) are not CSRF-able — exempt."""
    mw = create_csrf_middleware()
    req = _make_request(
        headers={
            "origin": "http://evil.example",
            "authorization": "Bearer kzm_token123",
        }
    )
    resp = await mw(req, _call_next)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_originless_client_bypasses():
    """curl / CLI / server-to-server webhooks send no Origin/Referer."""
    mw = create_csrf_middleware()
    req = _make_request()
    resp = await mw(req, _call_next)
    assert resp.status_code == 200
