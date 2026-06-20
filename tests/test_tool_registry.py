"""Tests for kazma_core.tool_registry module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_core.tool_registry import ToolRegistry, RegisteredTool
from kazma_core.mcp_client import MCPError


class TestToolRegistry:
    """Tests for ToolRegistry."""

    def test_initial_state(self) -> None:
        registry = ToolRegistry()
        assert not registry.connected
        assert registry.tool_count == 0
        assert registry.get_tool_definitions() == []
        assert registry.list_servers() == []

    @pytest.mark.asyncio
    async def test_connect_server_success(self) -> None:
        registry = ToolRegistry()

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(return_value=True)
        mock_client.list_tools = AsyncMock(return_value=[
            {"name": "search", "description": "Search the web", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
            {"name": "fetch", "description": "Fetch a URL", "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}}},
        ])
        mock_client.connected = True

        with patch("kazma_core.tool_registry.MCPClient", return_value=mock_client):
            count = await registry.connect_server({"name": "web", "transport": "stdio", "command": ["echo"]})

        assert count == 2
        assert registry.tool_count == 2
        assert registry.connected

    @pytest.mark.asyncio
    async def test_connect_server_failure(self) -> None:
        registry = ToolRegistry()

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=MCPError("connection refused"))

        with patch("kazma_core.tool_registry.MCPClient", return_value=mock_client):
            count = await registry.connect_server({"name": "bad", "transport": "stdio", "command": ["false"]})

        assert count == 0
        assert not registry.connected

    def test_get_tool_definitions_format(self) -> None:
        registry = ToolRegistry()
        registry._tools["search"] = RegisteredTool(
            name="search",
            description="Search the web",
            input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
            server_name="web",
        )

        defs = registry.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["type"] == "function"
        assert defs[0]["function"]["name"] == "search"
        assert defs[0]["function"]["description"] == "Search the web"

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        registry = ToolRegistry()

        mock_client = AsyncMock()
        mock_client.connected = True
        mock_client.call_tool = AsyncMock(return_value={
            "content": [{"type": "text", "text": "Search results here"}],
        })

        registry._clients["web"] = mock_client
        registry._tools["search"] = RegisteredTool(
            name="search",
            description="Search",
            input_schema={},
            server_name="web",
        )

        result = await registry.execute("search", {"q": "test"})
        assert result["content"] == "Search results here"
        assert result["is_error"] is False

    @pytest.mark.asyncio
    async def test_execute_tool_not_found(self) -> None:
        registry = ToolRegistry()
        result = await registry.execute("nonexistent", {})
        assert "not found" in result["content"]
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_execute_server_disconnected(self) -> None:
        registry = ToolRegistry()
        mock_client = AsyncMock()
        mock_client.connected = False

        registry._clients["web"] = mock_client
        registry._tools["search"] = RegisteredTool(
            name="search", description="", input_schema={}, server_name="web",
        )

        result = await registry.execute("search", {})
        assert "not connected" in result["content"]
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_execute_mcp_error(self) -> None:
        registry = ToolRegistry()
        mock_client = AsyncMock()
        mock_client.connected = True
        mock_client.call_tool = AsyncMock(side_effect=MCPError("timeout"))

        registry._clients["web"] = mock_client
        registry._tools["search"] = RegisteredTool(
            name="search", description="", input_schema={}, server_name="web",
        )

        result = await registry.execute("search", {})
        assert "timeout" in result["content"]
        assert result["is_error"] is True

    @pytest.mark.asyncio
    async def test_disconnect_all(self) -> None:
        registry = ToolRegistry()
        mock_client = AsyncMock()
        mock_client.disconnect = AsyncMock()
        registry._clients["web"] = mock_client
        registry._tools["search"] = RegisteredTool(
            name="search", description="", input_schema={}, server_name="web",
        )

        await registry.disconnect_all()
        assert not registry.connected
        assert registry.tool_count == 0
        mock_client.disconnect.assert_awaited()

    def test_list_tools(self) -> None:
        registry = ToolRegistry()
        registry._tools["search"] = RegisteredTool(
            name="search", description="Search the web for information", input_schema={}, server_name="web",
        )
        tools = registry.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "search"
