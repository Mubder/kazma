"""email_api security fixes (audit H4/H5/H6).

Validates:
- H4: mutating POST endpoints reject requests missing the X-Requested-With
  header or carrying a cross-origin Origin/Referer.
- H5: error responses are sanitized in production (no str(exc) leak).
- H6: _request_base honors KAZMA_PUBLIC_URL and ignores a spoofed Host header.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


# ── H4: CSRF same-origin dependency ────────────────────────────────────


def _make_request(*, headers: dict | None = None, base_url: str = "http://127.0.0.1:9090/"):
    """Build a minimal Request-like object for _verify_same_origin."""
    req = MagicMock()
    req.headers = headers or {}
    req.url.scheme = base_url.split("://")[0]
    req.base_url = base_url
    return req


def test_verify_same_origin_passes_with_header_and_matching_origin(monkeypatch):
    """A same-origin POST with the custom header is allowed."""
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example.com")
    from kazma_ui.email_api import _verify_same_origin

    req = _make_request(
        headers={
            "x-requested-with": "XMLHttpRequest",
            "origin": "https://kazma.example.com/settings",
        }
    )
    import asyncio

    asyncio.run(_verify_same_origin(req))  # must not raise


def test_verify_same_origin_rejects_missing_header(monkeypatch):
    """A POST without X-Requested-With is denied (CSRF defense layer 1)."""
    from kazma_ui.email_api import _verify_same_origin

    req = _make_request(headers={"origin": "http://127.0.0.1:9090"})
    with pytest.raises(HTTPException) as exc:
        import asyncio

        asyncio.run(_verify_same_origin(req))
    assert exc.value.status_code == 403
    assert "custom request header" in exc.value.detail


def test_verify_same_origin_rejects_cross_origin(monkeypatch):
    """A POST with the header but a foreign Origin is denied."""
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example.com")
    from kazma_ui.email_api import _verify_same_origin

    req = _make_request(
        headers={
            "x-requested-with": "XMLHttpRequest",
            "origin": "https://evil.example.com",
        }
    )
    with pytest.raises(HTTPException) as exc:
        import asyncio

        asyncio.run(_verify_same_origin(req))
    assert exc.value.status_code == 403
    assert "cross-origin" in exc.value.detail


def test_verify_same_origin_allows_no_origin_with_header(monkeypatch):
    """Some browsers omit Origin on same-site GETs; with the custom header
    and no Origin, the request passes (layer 1 alone is sufficient)."""
    from kazma_ui.email_api import _verify_same_origin

    req = _make_request(headers={"x-requested-with": "XMLHttpRequest"})
    import asyncio

    asyncio.run(_verify_same_origin(req))  # must not raise


# ── H5: sanitized error responses ─────────────────────────────────────


def _resp_body(resp) -> str:
    """Decode a JSONResponse body to text (works across Starlette versions)."""
    body = resp.body
    if isinstance(body, (bytes, bytearray)):
        return body.decode()
    return b"".join(body).decode()


def test_safe_error_sanitizes_in_production(monkeypatch):
    """In production, str(exc) must NOT appear in the response body."""
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    from kazma_ui.email_api import _safe_error

    exc = RuntimeError("/home/secret/vault.db: disk I/O error")
    resp = _safe_error(exc)
    body = _resp_body(resp)
    assert "/home/secret/vault.db" not in body
    assert "disk I/O error" not in body
    assert resp.status_code == 500
    assert "internal_error" in body


def test_safe_error_leaks_in_dev(monkeypatch):
    """In dev mode, the detail is echoed for debugging."""
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    from kazma_ui.email_api import _safe_error

    exc = RuntimeError("connection refused on imap.gmail.com:993")
    resp = _safe_error(exc)
    body = _resp_body(resp)
    assert "connection refused" in body  # helpful in dev


# ── H6: _request_base honors KAZMA_PUBLIC_URL ─────────────────────────


def test_request_base_uses_kazma_public_url(monkeypatch):
    """KAZMA_PUBLIC_URL is authoritative; a spoofed Host header is ignored."""
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example.com")
    from kazma_ui.email_api import _request_base

    req = _make_request(
        headers={"host": "evil.example.com", "x-forwarded-host": "evil.example.com"},
        base_url="http://127.0.0.1:9090/",
    )
    assert _request_base(req) == "https://kazma.example.com"


def test_request_base_falls_back_to_request_url(monkeypatch):
    """Without KAZMA_PUBLIC_URL, falls back to the request's own base URL."""
    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)
    from kazma_ui.email_api import _request_base

    req = _make_request(base_url="http://localhost:9090/")
    assert _request_base(req) == "http://localhost:9090"
