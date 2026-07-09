"""MCP settings service — extracted from settings_manager (S5)."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

class MCPSettingsService:
    """Service handling MCP server configuration, state, and client connection testing."""

    def __init__(self, config_store: Any) -> None:
        self._cs = config_store

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """List all MCP servers with status."""
        servers = self._cs.get("mcp.servers", [])
        if isinstance(servers, str):
            try:
                servers = json.loads(servers)
            except (json.JSONDecodeError, TypeError):
                servers = []
        return servers if isinstance(servers, list) else []

    def add_mcp_server(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new MCP server."""
        servers = self.get_mcp_servers()
        name = data.get("name", "")
        if not name:
            return {"error": "Server name is required"}
        # Check for duplicates
        for s in servers:
            if s.get("name") == name:
                s.update(data)
                self._cs.set("mcp.servers", json.dumps(servers), category="mcp")
                return s
        server = {
            "name": name,
            "transport": data.get("transport", "stdio"),
            "command": data.get("command", []),
            "url": data.get("url", ""),
            "env": data.get("env", {}),
            "enabled": True,
            "connected": False,
            "tool_count": 0,
            "tools": [],
        }
        servers.append(server)
        self._cs.set("mcp.servers", json.dumps(servers), category="mcp")
        return server

    def delete_mcp_server(self, name: str) -> None:
        """Remove an MCP server."""
        servers = self.get_mcp_servers()
        servers = [s for s in servers if s.get("name") != name]
        self._cs.set("mcp.servers", json.dumps(servers), category="mcp")

    def toggle_mcp_server(self, name: str, enabled: bool) -> None:
        """Enable/disable an MCP server."""
        servers = self.get_mcp_servers()
        for s in servers:
            if s.get("name") == name:
                s["enabled"] = enabled
                break
        self._cs.set("mcp.servers", json.dumps(servers), category="mcp")

    async def test_mcp_server(self, name: str) -> dict[str, Any]:
        """Test an MCP server connection."""
        servers = self.get_mcp_servers()
        server = None
        for s in servers:
            if s.get("name") == name:
                server = s
                break
        if not server:
            return {"success": False, "error": f"Server '{name}' not found"}

        try:
            from kazma_core.mcp.manager import AsyncMCPManager
            manager = AsyncMCPManager()
            count = await manager.connect_from_config([server])
            tool_schemas = manager.get_all_tool_schemas()
            tool_names = [t.get("function", {}).get("name", "") for t in tool_schemas]
            await manager.shutdown()
            return {"success": True, "tool_count": count, "tools": tool_names[:20]}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_mcp_tools(self, server_name: str) -> list[dict[str, Any]]:
        """List tools for an MCP server."""
        servers = self.get_mcp_servers()
        for s in servers:
            if s.get("name") == server_name:
                return s.get("tools", [])
        return []
