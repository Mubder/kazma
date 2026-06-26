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


def create_mcp_router(agent: KazmaAgent, templates: Jinja2Templates) -> APIRouter:
    """Create the MCP management router."""

    router = APIRouter(tags=["mcp"])

    def _get_configured_servers() -> list[dict[str, Any]]:
        """Get MCP servers from the agent config."""
        servers = agent.config.raw.get("mcp", {}).get("servers", [])
        result = []
        for s in servers:
            name = s.get("name", "unknown")
            # Check if server is connected
            is_connected = name in agent.tools._clients if hasattr(agent.tools, "_clients") else False
            tools = []
            if is_connected and hasattr(agent.tools, "_tools"):
                tools = [
                    {"name": t.name, "description": t.description}
                    for t in agent.tools._tools.values()
                    if t.server_name == name
                ]

            result.append(
                {
                    "name": name,
                    "transport": s.get("transport", "stdio"),
                    "command": s.get("command", []),
                    "url": s.get("url", ""),
                    "env": s.get("env", {}),
                    "working_dir": s.get("working_dir"),
                    "status": "running" if is_connected else "stopped",
                    "tool_count": len(tools),
                    "tools": tools,
                }
            )
        return result

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
        new_server = {
            "name": req.name,
            "transport": req.transport,
        }
        if req.transport == "stdio":
            new_server["command"] = req.command
            if req.working_dir:
                new_server["working_dir"] = req.working_dir
        else:
            new_server["url"] = req.url
        if req.env:
            new_server["env"] = req.env

        # Add to config raw
        mcp_section = agent.config.raw.setdefault("mcp", {})
        servers_list = mcp_section.setdefault("servers", [])

        # Check for duplicate name
        for s in servers_list:
            if s.get("name") == req.name:
                return {"status": "error", "error": f"Server '{req.name}' already exists"}

        servers_list.append(new_server)
        return {"status": "ok"}

    @router.delete("/api/mcp/servers/{name}")
    async def api_remove_server(name: str) -> dict[str, str]:
        """Remove an MCP server from configuration."""
        servers = agent.config.raw.get("mcp", {}).get("servers", [])
        agent.config.raw["mcp"]["servers"] = [s for s in servers if s.get("name") != name]

        # Disconnect if running
        if hasattr(agent.tools, "_clients") and name in agent.tools._clients:
            try:
                client = agent.tools._clients.pop(name)
                await client.disconnect()
            except Exception:
                pass

        return {"status": "ok"}

    @router.post("/api/mcp/servers/{name}/start")
    async def api_start_server(name: str) -> dict[str, Any]:
        """Start/connect an MCP server."""
        servers = agent.config.raw.get("mcp", {}).get("servers", [])
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
        except Exception as e:
            return {"status": "error", "error": str(e)}

    @router.post("/api/mcp/servers/{name}/stop")
    async def api_stop_server(name: str) -> dict[str, str]:
        """Stop/disconnect an MCP server."""
        if hasattr(agent.tools, "_clients") and name in agent.tools._clients:
            try:
                client = agent.tools._clients.pop(name)
                await client.disconnect()
            except Exception as e:
                return {"status": "error", "error": str(e)}
        return {"status": "ok"}

    @router.post("/api/mcp/servers/{name}/test")
    async def api_test_server(name: str) -> dict[str, Any]:
        """Test an MCP server connection without permanently connecting."""
        from kazma_core.mcp_client import MCPClient, MCPServerConfig

        servers = agent.config.raw.get("mcp", {}).get("servers", [])
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
        if not hasattr(agent.tools, "_tools"):
            return []
        return [
            {"name": t.name, "description": t.description} for t in agent.tools._tools.values() if t.server_name == name
        ]

    return router
