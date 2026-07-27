"""GitHub OAuth redirect URI must not trust client Host (audit M4)."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from kazma_gateway.routers.github import _oauth_redirect_uri


def _req(host: str = "evil.example", scheme: str = "https") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/github/oauth/start",
            "headers": [
                (b"host", host.encode()),
                (b"x-forwarded-proto", b"https"),
            ],
            "query_string": b"",
            "server": ("127.0.0.1", 9090),
            "scheme": scheme,
        }
    )


def test_public_url_wins_over_spoofed_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://kazma.example.com")
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    uri = _oauth_redirect_uri(_req("evil.attacker"))
    assert uri == "https://kazma.example.com/api/github/oauth/callback"
    assert "evil" not in uri


def test_prod_requires_public_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)
    with pytest.raises(ValueError, match="KAZMA_PUBLIC_URL"):
        _oauth_redirect_uri(_req("evil.attacker"))


def test_dev_fallback_ignores_request_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.delenv("KAZMA_PUBLIC_URL", raising=False)
    monkeypatch.setenv("KAZMA_HOST", "127.0.0.1")
    monkeypatch.setenv("KAZMA_PORT", "9090")
    uri = _oauth_redirect_uri(_req("evil.attacker"))
    assert uri == "http://127.0.0.1:9090/api/github/oauth/callback"
    assert "evil" not in uri
