"""Browser WS auth paths (MED #6): cookie + loopback, no meta/query token.

The browser no longer embeds a WS bearer token in the page or the WS URL;
these tests pin that the cookie and loopback paths authenticate without it,
and that a credential-less remote connection still fails closed.
"""

from __future__ import annotations

from unittest.mock import patch

from kazma_ui.auth import websocket_is_authenticated


class _FakeWS:
    def __init__(self, query=None, headers=None, cookies=None):
        self.query_params = query if query is not None else {}
        self.headers = headers if headers is not None else {}
        self.cookies = cookies if cookies is not None else {}


def test_cookie_auth_suffices_without_token() -> None:
    with patch("kazma_ui.auth._is_loopback_client", return_value=False), patch(
        "kazma_ui.auth._is_private_lan_client", return_value=False
    ), patch(
        "kazma_core.security.web_sessions.validate_session", return_value=True
    ):
        ws = _FakeWS(cookies={"kazma-session": "sess-abc"})
        assert websocket_is_authenticated(ws, expected_secret="test-secret") is True


def test_no_credential_remote_fails_closed() -> None:
    with patch("kazma_ui.auth._is_loopback_client", return_value=False), patch(
        "kazma_ui.auth._is_private_lan_client", return_value=False
    ):
        ws = _FakeWS()  # no cookie / header / token
        assert websocket_is_authenticated(ws, expected_secret="test-secret") is False


def test_loopback_trusted_without_any_credential() -> None:
    with patch("kazma_ui.auth._is_loopback_client", return_value=True):
        ws = _FakeWS()
        assert websocket_is_authenticated(ws, expected_secret="test-secret") is True
