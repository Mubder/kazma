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
) -> WebSocket:
    headers = [(b"host", host)]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
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
    for var in ("KAZMA_WS_ORIGIN_CHECK", "KAZMA_WS_EXTRA_ORIGINS", "KAZMA_DEV_WS_BYPASS"):
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


def test_credentialed_client_survives_origin_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing Origin falls through to the credential checks (no lock-out)."""
    monkeypatch.setattr(
        "kazma_core.security.web_sessions.validate_session", lambda sid: True
    )
    ws = _make_ws("https://evil.example", cookie="kazma-session=sess-abc")
    assert websocket_is_authenticated(ws, expected_secret="s") is True


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
