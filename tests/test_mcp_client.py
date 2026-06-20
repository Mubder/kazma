"""Tests for MCPClient — unit tests using mocks (no real servers)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.mcp_client import (
    MCPClient,
    MCPConnectionError,
    MCPError,
    MCPServerConfig,
    _jsonrpc_request,
    _jsonrpc_response,
)


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------


class TestJsonRpcHelpers:
    def test_request_basic(self) -> None:
        req = _jsonrpc_request("initialize")
        assert req["jsonrpc"] == "2.0"
        assert req["method"] == "initialize"
        assert "id" in req
        assert "params" not in req

    def test_request_with_params(self) -> None:
        req = _jsonrpc_request("tools/call", {"name": "foo"})
        assert req["params"] == {"name": "foo"}

    def test_request_ids_increment(self) -> None:
        r1 = _jsonrpc_request("m")
        r2 = _jsonrpc_request("m")
        assert r2["id"] == r1["id"] + 1

    def test_response_success(self) -> None:
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})
        result = _jsonrpc_response(raw)
        assert result == {"tools": []}

    def test_response_error(self) -> None:
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"message": "boom"}})
        with pytest.raises(MCPError, match="boom"):
            _jsonrpc_response(raw)

    def test_response_no_result(self) -> None:
        raw = json.dumps({"jsonrpc": "2.0", "id": 1})
        result = _jsonrpc_response(raw)
        assert result == {}


# ---------------------------------------------------------------------------
# MCPServerConfig
# ---------------------------------------------------------------------------


class TestMCPServerConfig:
    def test_defaults(self) -> None:
        cfg = MCPServerConfig(name="test")
        assert cfg.transport == "stdio"
        assert cfg.command == []
        assert cfg.url == ""
        assert cfg.timeout == 30.0

    def test_sse_config(self) -> None:
        cfg = MCPServerConfig(
            name="remote",
            transport="sse",
            url="http://localhost:8080",
            headers={"Authorization": "Bearer tok"},
        )
        assert cfg.transport == "sse"
        assert cfg.url == "http://localhost:8080"


# ---------------------------------------------------------------------------
# MCPClient
# ---------------------------------------------------------------------------


class TestMCPClient:
    @pytest.fixture
    def client(self) -> MCPClient:
        return MCPClient()

    def test_initial_state(self, client: MCPClient) -> None:
        assert not client.connected
        assert client.server_name == ""

    @pytest.mark.asyncio
    async def test_connect_bad_transport(self, client: MCPClient) -> None:
        with pytest.raises(MCPConnectionError, match="Unsupported transport"):
            await client.connect({"name": "x", "transport": "ws"})

    @pytest.mark.asyncio
    async def test_connect_stdio_no_command(self, client: MCPClient) -> None:
        with pytest.raises(MCPConnectionError, match="non-empty command"):
            await client.connect({"name": "x", "transport": "stdio", "command": []})

    @pytest.mark.asyncio
    async def test_connect_sse_no_url(self, client: MCPClient) -> None:
        with pytest.raises(MCPConnectionError, match="requires a URL"):
            await client.connect({"name": "x", "transport": "sse", "url": ""})

    @pytest.mark.asyncio
    async def test_connect_stdio_success(self, client: MCPClient) -> None:
        """Simulate a successful stdio connection by mocking Popen."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        with patch("kazma_core.mcp_client.subprocess.Popen", return_value=mock_proc):
            # We need to mock the _send_stdio to return a valid initialize response
            init_response = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"serverInfo": {"name": "test-server"}},
            })
            mock_proc.stdout.readline.return_value = (init_response + "\n").encode()

            result = await client.connect({
                "name": "test-server",
                "transport": "stdio",
                "command": ["echo", "test"],
            })

            assert result is True
            assert client.connected
            assert client.server_name == "test-server"

        await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_sse_success(self, client: MCPClient) -> None:
        """Simulate a successful SSE connection."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"serverInfo": {"name": "sse-server"}},
        })
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch("kazma_core.mcp_client.httpx.AsyncClient", return_value=mock_client):
            result = await client.connect({
                "name": "sse-server",
                "transport": "sse",
                "url": "http://localhost:8080",
            })
            assert result is True
            assert client.connected

        await client.disconnect()

    @pytest.mark.asyncio
    async def test_not_connected_error(self, client: MCPClient) -> None:
        with pytest.raises(MCPError, match="Not connected"):
            await client.list_tools()

        with pytest.raises(MCPError, match="Not connected"):
            await client.call_tool("foo")

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self) -> None:
        client = MCPClient()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()

        with patch("kazma_core.mcp_client.subprocess.Popen", return_value=mock_proc):
            init_response = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "result": {"serverInfo": {"name": "test"}},
            })
            mock_proc.stdout.readline.return_value = (init_response + "\n").encode()

            await client.connect({
                "name": "test",
                "transport": "stdio",
                "command": ["echo", "test"],
            })
            assert client.connected

        await client.disconnect()
        assert not client.connected
        assert client.server_name == ""
        assert client._process is None

    @pytest.mark.asyncio
    async def test_connect_bad_command(self) -> None:
        """Test connecting with a nonexistent command."""
        client = MCPClient()
        with pytest.raises(MCPConnectionError, match="Command not found"):
            await client.connect({
                "name": "test",
                "transport": "stdio",
                "command": ["/nonexistent/binary/that/does/not/exist"],
            })
