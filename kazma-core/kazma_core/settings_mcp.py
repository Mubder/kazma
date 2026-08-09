"""MCP settings service — extracted from settings_manager (S5).

Reads and writes go through :mod:`kazma_core.mcp_servers_store` so the
Settings page and the ``/mcp`` page share one dual-written SoT
(ConfigStore + ``kazma.yaml``). The previous ConfigStore-only path was
why Settings Test reported "Server not found" for servers added via
``/mcp`` Add Server.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["MCPSettingsService"]

logger = logging.getLogger(__name__)


def _agent_config_raw() -> dict[str, Any] | None:
    """Best-effort resolve of the live agent's config.raw for dual-write sync."""
    try:
        from kazma_core.service_container import get_container

        agent = get_container().resolve("KazmaAgent")
        cfg = getattr(agent, "config", None)
        raw = getattr(cfg, "raw", None)
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return None


def _agent_yaml_path() -> str | None:
    try:
        from kazma_core.service_container import get_container

        agent = get_container().resolve("KazmaAgent")
        if hasattr(agent, "_mcp_yaml_path"):
            return agent._mcp_yaml_path()
        cfg = getattr(agent, "config", None)
        path = getattr(cfg, "config_path", None)
        if path:
            return str(path)
    except Exception:
        pass
    return None


class MCPSettingsService:
    """Service handling MCP server configuration, state, and client connection testing."""

    def __init__(self, config_store: Any) -> None:
        # config_store kept for API compatibility; reads/writes go through
        # mcp_servers_store which uses get_config_store() + yaml.
        self._cs = config_store

    def get_mcp_servers(self) -> list[dict[str, Any]]:
        """List all MCP servers (merged ConfigStore + yaml)."""
        from kazma_core.mcp_servers_store import list_mcp_servers

        raw = _agent_config_raw()
        yaml_servers = None
        if raw is not None:
            yaml_servers = (raw.get("mcp") or {}).get("servers", [])
        return list_mcp_servers(
            yaml_servers=yaml_servers,
            yaml_path=_agent_yaml_path(),
        )

    def add_mcp_server(self, data: dict[str, Any]) -> dict[str, Any]:
        """Add a new MCP server (dual-write ConfigStore + yaml)."""
        from kazma_core.mcp_servers_store import upsert_mcp_server

        name = (data.get("name") or "").strip()
        if not name:
            return {"error": "Server name is required"}
        try:
            return upsert_mcp_server(
                data,
                config_raw=_agent_config_raw(),
                yaml_path=_agent_yaml_path(),
                replace=True,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            logger.warning("[MCPSettings] add failed: %s", exc)
            return {"error": str(exc)}

    def delete_mcp_server(self, name: str) -> None:
        """Remove an MCP server from both stores."""
        from kazma_core.mcp_servers_store import delete_mcp_server

        delete_mcp_server(
            name,
            config_raw=_agent_config_raw(),
            yaml_path=_agent_yaml_path(),
        )

    def toggle_mcp_server(self, name: str, enabled: bool) -> None:
        """Enable/disable an MCP server (dual-write)."""
        from kazma_core.mcp_servers_store import set_mcp_server_enabled

        set_mcp_server_enabled(
            name,
            enabled,
            config_raw=_agent_config_raw(),
            yaml_path=_agent_yaml_path(),
        )

    async def test_mcp_server(self, name: str) -> dict[str, Any]:
        """Test an MCP server connection using the unified merged store."""
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
            try:
                count = await manager.connect_from_config([server], raise_on_error=True)
                tool_schemas = manager.get_all_tool_schemas()
                tool_names = [t.get("function", {}).get("name", "") for t in tool_schemas]
                return {"success": True, "tool_count": count, "tools": tool_names[:20]}
            finally:
                # A failed test can still have completed a stdio handshake or
                # allocated an HTTP pool.  Always release it before reporting.
                await manager.shutdown()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_mcp_tools(self, server_name: str) -> list[dict[str, Any]]:
        """List tools for an MCP server (from stored metadata if any)."""
        servers = self.get_mcp_servers()
        for s in servers:
            if s.get("name") == server_name:
                return s.get("tools", [])
        return []
