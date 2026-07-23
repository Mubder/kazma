"""MCP server management UI routes for the Kazma WebUI.

Provides a visual interface for managing MCP servers — add, remove,
start, stop, test connections, and view available tools.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from kazma_ui.models import MCPServerAddRequest

if TYPE_CHECKING:
    from kazma_core.agent import KazmaAgent

logger = logging.getLogger(__name__)

__all__ = ["create_mcp_router"]


def create_mcp_router(agent: KazmaAgent, templates: Jinja2Templates) -> APIRouter:
    """Create the MCP management router."""

    router = APIRouter(tags=["mcp"])

    def _get_configured_servers() -> list[dict[str, Any]]:
        """Get MCP servers from the agent via the facade method."""
        return agent.get_mcp_servers()

    @router.get("/mcp", response_class=HTMLResponse)
    async def mcp_page(request: Request) -> HTMLResponse:
        """Render the MCP server management page."""
        servers = _get_configured_servers()
        return templates.TemplateResponse(
            request,
            "mcp.html",
            {
                "servers": servers,
                "config": agent.config,
                "active_page": "mcp",
            },
        )

    @router.get("/api/mcp/servers")
    async def api_list_servers() -> list[dict[str, Any]]:
        """List configured MCP servers."""
        return _get_configured_servers()

    @router.post("/api/mcp/servers")
    async def api_add_server(req: MCPServerAddRequest) -> dict[str, str]:
        """Add a new MCP server to the configuration."""
        result = agent.add_mcp_server(
            name=req.name,
            transport=req.transport,
            command=req.command,
            url=req.url,
            env=req.env,
            working_dir=req.working_dir,
        )
        return result

    @router.delete("/api/mcp/servers/{name}")
    async def api_remove_server(name: str) -> dict[str, str]:
        """Remove an MCP server from configuration."""
        agent.remove_mcp_server(name)

        # Disconnect if running — use the unified executor's public API.
        if agent.tools.is_server_connected(name):
            try:
                await agent.tools.disconnect_server(name)
            except Exception as exc:
                logger.debug("MCP server disconnect failed for %s: %s", name, exc)

        return {"status": "ok"}

    @router.post("/api/mcp/servers/{name}/start")
    async def api_start_server(name: str) -> dict[str, Any]:
        """Start/connect an MCP server."""
        servers = agent.get_mcp_servers_config()
        server_cfg = None
        for s in servers:
            if s.get("name") == name:
                server_cfg = s
                break

        if not server_cfg:
            return {"status": "error", "error": f"Server '{name}' not found in config"}

        try:
            count = await agent.tools.connect_server(server_cfg)
            return {"status": "ok", "tool_count": count}
        except Exception:
            return {"status": "error", "error": "Internal error"}

    @router.post("/api/mcp/servers/{name}/stop")
    async def api_stop_server(name: str) -> dict[str, str]:
        """Stop/disconnect an MCP server."""
        if agent.tools.is_server_connected(name):
            try:
                await agent.tools.disconnect_server(name)
            except Exception:
                return {"status": "error", "error": "Internal error"}
        return {"status": "ok"}

    @router.post("/api/mcp/servers/{name}/test")
    async def api_test_server(name: str) -> dict[str, Any]:
        """Test an MCP server connection without permanently connecting."""
        from kazma_core.mcp_client import MCPClient, MCPServerConfig

        servers = agent.get_mcp_servers_config()
        server_cfg = None
        for s in servers:
            if s.get("name") == name:
                server_cfg = s
                break

        if not server_cfg:
            return {"success": False, "error": f"Server '{name}' not found"}

        try:
            config = MCPServerConfig(
                name=server_cfg.get("name", name),
                transport=server_cfg.get("transport", "stdio"),
                command=server_cfg.get("command", []),
                url=server_cfg.get("url", ""),
                env=server_cfg.get("env", {}),
                working_dir=server_cfg.get("working_dir"),
            )
            client = MCPClient()
            await client.connect(config)
            tools = await client.list_tools()
            await client.disconnect()
            return {
                "success": True,
                "tool_count": len(tools),
                "tools": [t.get("name", "") for t in tools[:10]],
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @router.get("/api/mcp/servers/{name}/tools")
    async def api_server_tools(name: str) -> list[dict[str, str]]:
        """Get tools from a connected MCP server."""
        return agent.tools.get_mcp_tools_for_server(name)

    return router
