"""CSWSH guard on loopback WebSocket trust (audit H — WS Origin check).

Builds REAL starlette WebSocket objects from ASGI scopes (test_csrf.py
philosophy): a browser can open ``ws://127.0.0.1`` from any public page, so
the anonymous loopback trust path must validate the handshake ``Origin``
(absent / same-authority / explicitly allow-listed) before granting access.
"""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocket

import kazma_ui.auth as auth_mod
from kazma_ui.auth import websocket_is_authenticated


def _make_ws(
    origin: str | None = None,
    host: bytes = b"127.0.0.1:9090",
    client: tuple[str, int] = ("127.0.0.1", 50000),
    cookie: str | None = None,
    secret_header: str | None = None,
) -> WebSocket:
    headers = [(b"host", host)]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if secret_header is not None:
        headers.append((b"x-kazma-secret", secret_header.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    scope = {
        "type": "websocket",
        "path": "/ws/chat",
        "headers": headers,
        "query_string": b"",
        "client": client,
        "server": ("127.0.0.1", 9090),
        "scheme": "http",
    }

    async def _receive() -> dict:
        return {"type": "websocket.connect"}

    async def _send(message: dict) -> None:
        return None

    return WebSocket(scope, receive=_receive, send=_send)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for var in (
        "KAZMA_WS_ORIGIN_CHECK", "KAZMA_WS_EXTRA_ORIGINS", "KAZMA_DEV_WS_BYPASS",
        "KAZMA_PUBLIC_URL", "KAZMA_CORS_ORIGINS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_loopback_no_origin_accepted() -> None:
    """Non-browser clients (curl/TUI) send no Origin — trusted as before."""
    assert websocket_is_authenticated(_make_ws(), expected_secret="s") is True


def test_loopback_same_host_origin_accepted() -> None:
    assert websocket_is_authenticated(
        _make_ws("http://127.0.0.1:9090"), expected_secret="s"
    ) is True


def test_loopback_cross_host_origin_rejected() -> None:
    """THE regression: a public page opening ws://127.0.0.1 must be refused."""
    assert websocket_is_authenticated(
        _make_ws("https://evil.example"), expected_secret="s"
    ) is False


def test_loopback_null_origin_rejected() -> None:
    assert websocket_is_authenticated(_make_ws("null"), expected_secret="s") is False


def test_same_authority_different_port_rejected() -> None:
    assert websocket_is_authenticated(
        _make_ws("http://127.0.0.1:8888"), expected_secret="s"
    ) is False


def test_kill_switch_restores_old_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_WS_ORIGIN_CHECK", "0")
    assert websocket_is_authenticated(
        _make_ws("https://evil.example"), expected_secret="s"
    ) is True


def test_extra_origins_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_WS_EXTRA_ORIGINS", "https://tunnel.corp, wss://ws2.example.org")
    assert websocket_is_authenticated(
        _make_ws("https://tunnel.corp"), expected_secret="s"
    ) is True
    # Other origins are still rejected.
    assert websocket_is_authenticated(
        _make_ws("https://other.corp"), expected_secret="s"
    ) is False


def test_header_credential_survives_origin_rejection() -> None:
    """A HEADER credential falls through a failing Origin (no lock-out).

    X-Kazma-Secret / Bearer are never auto-sent by a browser cross-site, so
    they carry no CSWSH risk — a legitimate non-browser client (curl/CLI from
    a tunnel) must not be locked out by the Origin check.
    """
    ws = _make_ws("https://evil.example", secret_header="s")
    assert websocket_is_authenticated(ws, expected_secret="s") is True


def test_cookie_auth_rejected_cross_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """AUD-022: a cookie IS auto-sent on a cross-site WS handshake, so a
    valid session cookie with a foreign Origin must be refused (CSWSH).

    A remote peer isolates the cookie path from loopback peer-trust.
    """
    monkeypatch.setattr(
        "kazma_core.security.web_sessions.validate_session", lambda sid: True
    )
    ws = _make_ws(
        "https://evil.example",
        client=("203.0.113.9", 4444),
        cookie="kazma-session=sess-abc",
    )
    assert websocket_is_authenticated(ws, expected_secret="s") is False


def test_cookie_auth_accepted_same_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive control: the same valid cookie with a same-authority Origin
    authenticates — the Origin check only blocks the cross-site case."""
    monkeypatch.setattr(
        "kazma_core.security.web_sessions.validate_session", lambda sid: True
    )
    ws = _make_ws(
        "http://127.0.0.1:9090",
        client=("203.0.113.9", 4444),
        cookie="kazma-session=sess-abc",
    )
    assert websocket_is_authenticated(ws, expected_secret="s") is True


def _tunnelled_cookie_ws(origin: str) -> WebSocket:
    """The live topology: cloudflared is the peer and may forward the local
    service's Host, while the browser's Origin is the public URL."""
    return _make_ws(
        origin,
        host=b"127.0.0.1:9090",
        client=("203.0.113.9", 4444),
        cookie="kazma-session=sess-abc",
    )


def test_cookie_auth_accepts_the_declared_public_origin_behind_a_tunnel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's own page, served through a tunnel whose forwarded Host
    is not the public name, must keep its cookie-authenticated socket: the
    Origin is KAZMA_PUBLIC_URL, the same origin CSRF and CORS already trust."""
    monkeypatch.setattr(
        "kazma_core.security.web_sessions.validate_session", lambda sid: True
    )
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://my.kazma.ai")
    ws = _tunnelled_cookie_ws("https://my.kazma.ai")
    assert websocket_is_authenticated(ws, expected_secret="s") is True


def test_tunnelled_cookie_needs_the_declared_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: the same handshake with no declared public URL is
    refused — the declared origin, not the tunnel, is what admits it."""
    monkeypatch.setattr(
        "kazma_core.security.web_sessions.validate_session", lambda sid: True
    )
    ws = _tunnelled_cookie_ws("https://my.kazma.ai")
    assert websocket_is_authenticated(ws, expected_secret="s") is False


def test_declared_public_origin_does_not_admit_a_foreign_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declaring the public URL trusts THAT origin only."""
    monkeypatch.setattr(
        "kazma_core.security.web_sessions.validate_session", lambda sid: True
    )
    monkeypatch.setenv("KAZMA_PUBLIC_URL", "https://my.kazma.ai")
    for foreign in ("https://evil.example", "https://my.kazma.ai.evil.example",
                    "http://my.kazma.ai", "https://my.kazma.ai:8443"):
        ws = _tunnelled_cookie_ws(foreign)
        assert websocket_is_authenticated(ws, expected_secret="s") is False, foreign


def test_remote_peer_with_evil_origin_still_fails_closed() -> None:
    ws = _make_ws("https://evil.example", client=("203.0.113.9", 4444))
    assert websocket_is_authenticated(ws, expected_secret="s") is False


def test_guard_not_applied_when_no_secret_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open mode (empty expected secret) returns before any trust path."""
    monkeypatch.setattr(auth_mod, "get_kazma_secret", lambda: "")
    ws = _make_ws("https://evil.example")
    assert websocket_is_authenticated(ws) is True
