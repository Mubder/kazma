"""Tests for the MCP OAuth 2.1 + DCR module (kazma_core.mcp.oauth)."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from kazma_core.mcp.oauth import (
    MCPOAuthError,
    clear_oauth,
    discover_auth_requirements,
    get_valid_token,
    oauth_status,
    parse_www_authenticate,
)


# ---------------------------------------------------------------------------
# WWW-Authenticate parsing
# ---------------------------------------------------------------------------


class TestWwwAuthenticateParsing:
    def test_bearer_with_resource_metadata(self) -> None:
        header = (
            'Bearer resource_metadata="https://mcp.facebook.com/'
            '.well-known/oauth-protected-resource/devtools", '
            'scope="developer_tools_mcp_app_read developer_tools_mcp_app_management"'
        )
        params = parse_www_authenticate(header)
        assert params["resource_metadata"].endswith("/devtools")
        assert "developer_tools_mcp_app_read" in params["scope"]

    def test_empty_and_non_bearer(self) -> None:
        assert parse_www_authenticate("") == {}
        assert parse_www_authenticate("Basic realm=x") == {}
        assert parse_www_authenticate('Bearer error="invalid_token"') == {
            "error": "invalid_token"
        }


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _stand_in(monkeypatch, routes: dict[str, tuple[int, dict]]):
    """The pinned path with a stand-in resolver and socket (no network):
    every metadata address on mcp.example / as.example."""
    from tests._pinned_stand_in import OTHER_PUBLIC_IP, PUBLIC_IP, Answer, install

    table = {}
    for path, (status, body) in routes.items():
        table[("mcp.example", path)] = Answer.json(body, status=status)
    return install(monkeypatch, dns={"mcp.example": [PUBLIC_IP], "as.example": [OTHER_PUBLIC_IP]},
                   routes=table)


class TestDiscovery:
    @pytest.mark.asyncio
    async def test_full_discovery_chain(self, monkeypatch) -> None:
        _stand_in(monkeypatch, {
            "/.well-known/oauth-protected-resource/devtools": (200, {
                "authorization_servers": ["https://as.example"],
                "scopes_supported": ["read"],
            }),
            "/.well-known/oauth-authorization-server/devtools": (200, {
                "issuer": "https://as.example",
                "authorization_endpoint": "https://as.example/authorize",
                "token_endpoint": "https://as.example/token",
                "registration_endpoint": "https://as.example/register",
            }),
        })
        result = await discover_auth_requirements("https://mcp.example/devtools")
        assert result["token_endpoint"] == "https://as.example/token"
        assert result["registration_endpoint"] == "https://as.example/register"

    @pytest.mark.asyncio
    async def test_missing_metadata_raises(self, monkeypatch) -> None:
        _stand_in(monkeypatch, {})
        with pytest.raises(MCPOAuthError, match="resource metadata"):
            await discover_auth_requirements("https://mcp.example/devtools")

    @pytest.mark.asyncio
    async def test_challenge_scopes_win(self, monkeypatch) -> None:
        _stand_in(monkeypatch, {
            "/.well-known/oauth-protected-resource/devtools": (200, {
                "authorization_servers": ["https://as.example"], "scopes_supported": ["a"],
            }),
            "/.well-known/oauth-authorization-server/devtools": (200, {
                "issuer": "https://as.example",
                "authorization_endpoint": "https://as.example/authorize",
                "token_endpoint": "https://as.example/token",
            }),
        })
        result = await discover_auth_requirements(
            "https://mcp.example/devtools",
            www_authenticate='Bearer scope="s1 s2"',
        )
        assert result["scopes"] == ["s1", "s2"]


# ---------------------------------------------------------------------------
# Token persistence / refresh
# ---------------------------------------------------------------------------


class _FakeConfigStore:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value

    def delete(self, key: str) -> bool:
        return self._data.pop(key, None) is not None


class TestTokenStore:
    @pytest.mark.asyncio
    async def test_valid_token_returned(self, monkeypatch) -> None:
        store = _FakeConfigStore()
        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store", lambda: store
        )
        from kazma_core.mcp import oauth

        oauth._save_tokens("svc", "https://r", "cid", {
            "access_token": "abc", "expires_in": 3600,
        })
        assert await get_valid_token("svc") == "abc"
        assert oauth_status("svc") == "authenticated"

    @pytest.mark.asyncio
    async def test_expired_token_with_refresh(self, monkeypatch) -> None:
        store = _FakeConfigStore()
        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store", lambda: store
        )
        from kazma_core.mcp import oauth

        oauth._save_tokens("svc", "https://r", "cid", {
            "access_token": "old",
            "refresh_token": "rt",
            "expires_in": -100,  # already expired
        })

        refreshed = {"access_token": "new", "expires_in": 3600}
        with patch.object(oauth, "_refresh", AsyncMock(return_value=refreshed)):
            assert await get_valid_token("svc") == "new"

    @pytest.mark.asyncio
    async def test_expired_without_refresh_returns_none(self, monkeypatch) -> None:
        store = _FakeConfigStore()
        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store", lambda: store
        )
        from kazma_core.mcp import oauth

        oauth._save_tokens("svc", "https://r", "cid", {
            "access_token": "old", "expires_in": -100,
        })
        assert await get_valid_token("svc") is None

    def test_clear_oauth(self, monkeypatch) -> None:
        store = _FakeConfigStore()
        monkeypatch.setattr(
            "kazma_core.config_store.get_config_store", lambda: store
        )
        from kazma_core.mcp import oauth

        oauth._save_tokens("svc", "https://r", "cid", {"access_token": "x"})
        assert clear_oauth("svc") is True
        assert oauth_status("svc") == "none"
