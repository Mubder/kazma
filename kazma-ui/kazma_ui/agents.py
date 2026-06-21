"""Kazma Agent Management — FastAPI route for agent status and control.

Provides a local web UI at /agents showing running agent status,
discovered hub agents, and start/stop controls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def create_agents_router(agent: Any, templates: Jinja2Templates) -> APIRouter:
    """Create a router for agent management pages."""
    router = APIRouter(tags=["agents"])

    @router.get("/agents", response_class=HTMLResponse)
    async def agents_page(request: Request) -> HTMLResponse:
        """Render agent management page."""
        agent_info = _get_agent_info(agent)
        return templates.TemplateResponse(
            request,
            "agents.html",
            agent_info,
        )

    @router.get("/api/agents/status")
    async def agents_status() -> JSONResponse:
        """JSON endpoint for agent status (for AJAX refresh)."""
        return JSONResponse(_get_agent_info(agent))

    @router.post("/api/agents/{action}")
    async def agent_control(action: str) -> JSONResponse:
        """Start or stop the agent."""
        if action == "start":
            try:
                agent._running = True
                logger.info("Agent started")
                return JSONResponse({"status": "ok", "running": True})
            except Exception as e:
                return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
        elif action == "stop":
            try:
                agent._running = False
                logger.info("Agent stopped")
                return JSONResponse({"status": "ok", "running": False})
            except Exception as e:
                return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
        else:
            return JSONResponse({"status": "error", "message": f"Unknown action: {action}"}, status_code=400)

    @router.get("/api/agents/hub")
    async def agents_hub() -> JSONResponse:
        """List all agents from the hub registry."""
        try:
            from kazma_core.hub.registry import KazmaHub

            hub = KazmaHub()
            hub_agents = await hub.list_agents()
            await hub.close()
            return JSONResponse({
                "agents": [
                    {
                        "agent_id": a.agent_id,
                        "capabilities": a.capabilities,
                        "endpoint": a.endpoint,
                        "reputation": a.reputation,
                        "metadata": a.metadata,
                    }
                    for a in hub_agents
                ],
                "count": len(hub_agents),
            })
        except Exception as e:
            logger.warning("Failed to list hub agents: %s", e)
            return JSONResponse({"agents": [], "count": 0, "error": str(e)})

    return router


def _get_agent_info(agent: Any) -> dict[str, Any]:
    """Get agent information for the management page."""
    from kazma_core.tracing import get_trace_store

    store = get_trace_store()
    stats = store.stats()

    config = getattr(agent, "config", None)
    llm_cfg = getattr(agent, "llm_config", None)
    tools = getattr(agent, "tools", None)

    # Tool count
    tool_count = 0
    tool_list = []
    if tools:
        try:
            tool_list = tools.list_tools()
            tool_count = len(tool_list)
        except Exception:
            pass

    # MCP servers count
    mcp_servers = 0
    if tools:
        mcp_servers = len(getattr(tools, "_servers", {}))

    return {
        "running": getattr(agent, "_running", False),
        "config": {
            "name": getattr(config, "name", "kazma"),
            "version": getattr(config, "version", "0.1.0"),
            "language": getattr(config, "language", "ar"),
            "rtl": getattr(config, "rtl", True),
            "default_model": getattr(config, "default_model", "gpt-4o-mini"),
            "system_prompt": (getattr(config, "system_prompt", "") or "")[:200],
        },
        "llm": {
            "model": getattr(llm_cfg, "model", "unknown"),
            "base_url": getattr(llm_cfg, "base_url", ""),
            "max_tokens": getattr(llm_cfg, "max_tokens", 4096),
            "temperature": getattr(llm_cfg, "temperature", 0.7),
        },
        "tools": {
            "count": tool_count,
            "servers": mcp_servers,
            "list": [{"name": t.get("name", t.get("function", {}).get("name", "?")), "description": t.get("description", t.get("function", {}).get("description", ""))[:80]} for t in tool_list[:20]],
        },
        "metrics": {
            "total_cost": f"${stats['total_cost']:.4f}",
            "total_tokens": f"{stats['total_tokens']:,}",
            "total_llm_calls": stats["total_llm_calls"],
            "total_tool_calls": stats["total_tool_calls"],
        },
    }
