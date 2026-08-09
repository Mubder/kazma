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

from kazma_ui.models import MCPServerAddRequest, MCPServerTestRequest

if TYPE_CHECKING:
    from kazma_core.agent import KazmaAgent

logger = logging.getLogger(__name__)

__all__ = ["create_mcp_router"]


def _read_client_stderr(client: Any) -> str:
    """Best-effort capture of an MCPClient subprocess's stderr.

    Many MCP server failures (bad API key, missing dependency, install vs
    run command) only surface in stderr — the handshake just times out
    with no other signal. We read up to 2KB of whatever's buffered before
    the caller tears the subprocess down, so the UI can show *why* a
    server reported 0 tools instead of an opaque failure.

    Cross-platform: reads in a worker thread with a 1s cap (select() is
    unreliable on Windows pipes, so a thread is the portable way to do a
    non-blocking read with a timeout).

    Returns ``""`` when there's no subprocess (SSE transport) or the read
    fails. Never raises — diagnostics must not break the test path.
    """
    try:
        proc = getattr(client, "_process", None)
        if proc is None or not hasattr(proc, "stderr") or proc.stderr is None:
            return ""
        import threading

        result: dict[str, str] = {"data": ""}

        def _read() -> None:
            try:
                data = (
                    proc.stderr.read1(2048)
                    if hasattr(proc.stderr, "read1")
                    else proc.stderr.read(2048)
                )
                if isinstance(data, bytes):
                    result["data"] = data.decode("utf-8", errors="replace")
                elif data:
                    result["data"] = str(data)
            except Exception:
                pass

        t = threading.Thread(target=_read, daemon=True)
        t.start()
        t.join(timeout=1.0)  # bounded — never block the test path
        return result["data"]
    except Exception:
        return ""


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

    @router.get("/api/mcp/presets")
    async def api_list_presets() -> dict[str, Any]:
        """List available MCP server presets for the Add Server dropdown.

        Returns presets grouped by category, so the UI can render optgroups:
        ``{"ok": true, "categories": [{name, presets}, ...]}``
        """
        try:
            from kazma_ui.mcp_presets import list_presets_grouped

            categories = list_presets_grouped()
            return {"ok": True, "categories": categories}
        except Exception as exc:
            logger.exception("[mcp_api] list_presets failed")
            return {"ok": False, "error": str(exc), "categories": []}

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
            auth=req.auth,
            trust=req.trust,
        )
        return result

    @router.delete("/api/mcp/servers/{name}")
    async def api_remove_server(name: str) -> dict[str, str]:
        """Remove an MCP server from configuration."""
        result = agent.remove_mcp_server(name)
        if result.get("status") != "ok":
            return result

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
        except Exception as exc:
            logger.exception("[mcp_api] Failed to start MCP server %s", name)
            return {"status": "error", "error": f"Failed to start server: {exc}"}

        # connect_server → connect_from_config(raise_on_error=False) swallows
        # spawn/handshake failures into _connection_errors and returns 0 —
        # reporting "ok" here is what made Start look dead. Surface the real
        # recorded error instead.
        if not agent.tools.is_server_connected(name):
            detail = ""
            try:
                errors = getattr(agent.tools._mcp, "connection_errors", None) or {}
                detail = errors.get(name, "")
            except Exception:
                detail = ""
            return {
                "status": "error",
                "error": f"Failed to start server: {detail or 'connection failed (0 tools, not connected)'}",
            }
        return {"status": "ok", "tool_count": count}

    @router.post("/api/mcp/servers/{name}/stop")
    async def api_stop_server(name: str) -> dict[str, str]:
        """Stop/disconnect an MCP server."""
        if agent.tools.is_server_connected(name):
            try:
                await agent.tools.disconnect_server(name)
            except Exception as exc:
                logger.exception("[mcp_api] Failed to stop MCP server %s", name)
                return {"status": "error", "error": f"Failed to stop server: {exc}"}
        return {"status": "ok"}

    @router.post("/api/mcp/servers/{name}/test")
    async def api_test_server(name: str) -> dict[str, Any]:
        """Test an MCP server connection without permanently connecting.

        Returns ``{success, tool_count, tools[], error, stderr}``. The
        ``stderr`` field carries the subprocess's last stderr bytes so the
        UI can surface *why* a 0-tools server failed (a long-standing
        "saved silently, no idea why" complaint — surfaced now).
        """
        from kazma_core.mcp_client import MCPClient, MCPServerConfig

        servers = agent.get_mcp_servers_config()
        server_cfg = None
        for s in servers:
            if s.get("name") == name:
                server_cfg = s
                break

        if not server_cfg:
            return {"success": False, "error": f"Server '{name}' not found"}

        # Expand ${KAZMA_ACTIVE_WORKSPACE} exactly like the Start path
        # (UnifiedToolExecutor.connect_server → apply_workspace_to_server_config)
        # so Test exercises the same command the server would actually run.
        try:
            from kazma_core.workspace.mcp_rebind import apply_workspace_to_server_config

            server_cfg = apply_workspace_to_server_config(server_cfg)
        except Exception as exc:
            logger.debug("[mcp_api] workspace interpolation skipped for %s: %s", name, exc)

        transport = server_cfg.get("transport", "stdio")

        # stdio / sse: use MCPClient (stderr diagnostics for stdio).
        # streamable_http: MCPClient doesn't support it, so test via the
        # same AsyncMCPManager path Start uses.
        if transport in ("stdio", "sse"):
            client = MCPClient()
            try:
                config = MCPServerConfig(
                    name=server_cfg.get("name", name),
                    transport=transport,
                    command=server_cfg.get("command", []),
                    url=server_cfg.get("url", ""),
                    env=server_cfg.get("env", {}),
                    working_dir=server_cfg.get("working_dir"),
                    auth=server_cfg.get("auth", {}),
                    trust=server_cfg.get("trust", "approval_required"),
                )
                await client.connect(config)
                tools = await client.list_tools()
                stderr_text = _read_client_stderr(client)
                await client.disconnect()
                return {
                    "success": True,
                    "tool_count": len(tools),
                    "tools": [t.get("name", "") for t in tools[:10]],
                    "stderr": stderr_text,
                }
            except Exception as e:
                stderr_text = _read_client_stderr(client)
                try:
                    await client.disconnect()
                except Exception:
                    pass
                return {"success": False, "error": str(e), "stderr": stderr_text}

        # HTTP-based transports: test via the same manager path Start uses.
        try:
            from kazma_core.mcp.manager import AsyncMCPManager

            manager = AsyncMCPManager()
            count = await manager.connect_from_config([dict(server_cfg)], raise_on_error=True)
            tools = manager.get_all_tool_schemas()
            await manager.shutdown()
            return {
                "success": True,
                "tool_count": count,
                "tools": [t.get("name", "") for t in tools[:10]],
                "stderr": "",
            }
        except Exception as e:
            return {"success": False, "error": str(e), "stderr": ""}

    @router.post("/api/mcp/test-config")
    async def api_test_config(server_cfg: dict[str, Any] = None) -> dict[str, Any]:
        """Validate an MCP server config WITHOUT persisting it.

        Used by the Add Server modal's "validate-before-save" flow: we test
        the candidate config first and only POST to ``/servers`` (which
        saves) if the test passes. This catches the common "install command
        pasted instead of run command" mistake (now also auto-rewritten
        client-side) plus missing-binary / bad-API-key / 0-tools cases
        BEFORE the broken entry is written to ``kazma.yaml``.
        """
        from kazma_core.mcp_client import MCPClient, MCPServerConfig

        if not server_cfg:
            return {"success": False, "error": "Missing server config"}

        try:
            server_cfg = MCPServerTestRequest.model_validate(server_cfg).model_dump()
        except Exception:
            return {"success": False, "error": "Invalid server config"}

        try:
            from kazma_core.workspace.mcp_rebind import apply_workspace_to_server_config

            server_cfg = apply_workspace_to_server_config(server_cfg)
        except Exception as exc:
            logger.debug("[mcp_api] workspace interpolation skipped for test-config: %s", exc)

        transport = server_cfg.get("transport", "stdio")

        # MCPClient handles stdio (stderr diagnostics) and SSE. It does NOT
        # support streamable_http, which the AsyncMCPManager already implements.
        if transport in ("stdio", "sse"):
            client = MCPClient()
            try:
                config = MCPServerConfig(
                    name=server_cfg.get("name", "test"),
                    transport=transport,
                    command=server_cfg.get("command", []),
                    url=server_cfg.get("url", ""),
                    env=server_cfg.get("env", {}),
                    working_dir=server_cfg.get("working_dir"),
                    auth=server_cfg.get("auth", {}),
                    trust=server_cfg.get("trust", "approval_required"),
                )
                await client.connect(config)
                tools = await client.list_tools()
                stderr_text = _read_client_stderr(client)
                await client.disconnect()
                return {
                    "success": True,
                    "tool_count": len(tools),
                    "tools": [t.get("name", "") for t in tools[:10]],
                    "stderr": stderr_text,
                }
            except Exception as e:
                stderr_text = _read_client_stderr(client)
                try:
                    await client.disconnect()
                except Exception:
                    pass
                return {"success": False, "error": str(e), "stderr": stderr_text}

        try:
            from kazma_core.mcp.manager import AsyncMCPManager

            manager = AsyncMCPManager()
            count = await manager.connect_from_config([dict(server_cfg)], raise_on_error=True)
            tools = manager.get_all_tool_schemas()
            await manager.shutdown()
            return {
                "success": True,
                "tool_count": count,
                "tools": [t.get("name", "") for t in tools[:10]],
                "stderr": "",
            }
        except Exception as e:
            return {"success": False, "error": str(e), "stderr": ""}

    @router.get("/api/mcp/servers/{name}/tools")
    async def api_server_tools(name: str) -> list[dict[str, str]]:
        """Get tools from a connected MCP server."""
        return agent.tools.get_mcp_tools_for_server(name)

    return router
