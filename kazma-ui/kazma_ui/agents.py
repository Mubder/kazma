"""Kazma Agent Management — FastAPI route for agent status and control.

Provides a local web UI at /agents showing running agent status, current state
(idle/thinking/acting), tool execution history, reasoning steps from the
LangGraph trace, and start/stop controls.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def _get_trace_store() -> Any:
    """Return the global TraceStore (imported lazily to avoid import cycles)."""
    from kazma_core.tracing import get_trace_store

    return get_trace_store()


def _format_trace_entry(entry: Any) -> dict[str, Any]:
    """Format a TraceEntry dataclass for JSON / template consumption."""
    return {
        "timestamp": time.strftime("%H:%M:%S", time.localtime(entry.timestamp)),
        "epoch": entry.timestamp,
        "trace_type": entry.trace_type,
        "label": entry.label,
        "status": entry.status,
        "duration_ms": f"{entry.duration_ms:.0f}",
        "tokens": entry.tokens,
        "cost": f"${entry.cost:.4f}",
        "details": entry.details,
    }


def _derive_agent_state(running: bool, recent_traces: list[Any]) -> str:
    """Derive a high-level agent state from running flag + recent traces.

    Returns one of: "thinking", "acting", "idle".
    - "thinking" when the most recent trace is an LLM call.
    - "acting" when the most recent trace is a tool call.
    - "idle" when stopped or no recent activity.
    """
    if not running:
        return "idle"
    if not recent_traces:
        return "idle"
    latest = recent_traces[-1]
    if latest.trace_type == "llm":
        return "thinking"
    if latest.trace_type == "tool":
        return "acting"
    return "idle"


def create_agents_router(agent: Any, templates: Jinja2Templates) -> APIRouter:
    """Create a router for agent management pages."""
    router = APIRouter(tags=["agents"])

    @router.get("/agents", response_class=HTMLResponse)
    async def agents_page(request: Request) -> HTMLResponse:
        """Render agent management page."""
        agent_info = _get_agent_info(agent)
        agent_info["active_page"] = "agents"
        return templates.TemplateResponse(
            request,
            "agents.html",
            agent_info,
        )

    @router.get("/api/agents/status")
    async def agents_status() -> JSONResponse:
        """JSON endpoint for agent status (for AJAX refresh)."""
        return JSONResponse(_get_agent_info(agent))

    @router.get("/api/agents")
    async def agents_list() -> JSONResponse:
        """JSON endpoint listing the active agent(s) with status/model/sessions.

        Satisfies VAL-UX-006 requirement for a populated agent list backed by a
        real API. Returns a non-empty array containing the primary agent.
        """
        return JSONResponse({"agents": [_get_agent_info(agent)]})

    @router.get("/api/agents/traces")
    async def agents_traces(limit: int = 50) -> JSONResponse:
        """Return recent LangGraph traces (reasoning + tool steps)."""
        store = _get_trace_store()
        entries = store.recent(limit)
        return JSONResponse(
            {
                "traces": [_format_trace_entry(e) for e in entries],
                "count": len(entries),
            }
        )

    @router.get("/api/agents/tools")
    async def agents_tool_history(limit: int = 50) -> JSONResponse:
        """Return tool execution history (filtered trace of type 'tool')."""
        store = _get_trace_store()
        entries = [e for e in store.recent(limit) if e.trace_type == "tool"]
        return JSONResponse(
            {
                "tools": [_format_trace_entry(e) for e in entries],
                "count": len(entries),
            }
        )

    @router.get("/api/agents/reasoning")
    async def agents_reasoning(limit: int = 50) -> JSONResponse:
        """Return reasoning steps (LLM call traces from LangGraph)."""
        store = _get_trace_store()
        entries = [e for e in store.recent(limit) if e.trace_type == "llm"]
        return JSONResponse(
            {
                "steps": [_format_trace_entry(e) for e in entries],
                "count": len(entries),
            }
        )

    @router.post("/api/agents/{action}")
    async def agent_control(action: str) -> JSONResponse:
        """Start or stop the agent."""
        if action == "start":
            try:
                agent.set_running(True)
                logger.info("Agent started")
                return JSONResponse({"status": "ok", "running": True})
            except Exception as e:
                return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
        elif action == "stop":
            try:
                agent.set_running(False)
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
            return JSONResponse(
                {
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
                }
            )
        except Exception as e:
            logger.warning("Failed to list hub agents: %s", e)
            return JSONResponse({"agents": [], "count": 0, "error": str(e)})

    return router


def _get_agent_info(agent: Any) -> dict[str, Any]:
    """Get agent information for the management page."""
    store = _get_trace_store()
    stats = store.stats()

    config = getattr(agent, "config", None)

    # Tool info via facade method (avoids private _servers access)
    tools_info = agent.get_tools_info() if hasattr(agent, "get_tools_info") else {
        "count": 0,
        "list": [],
        "servers": 0,
    }

    # LLM config via facade method (avoids llm_config.* access)
    llm_info = agent.get_llm_config() if hasattr(agent, "get_llm_config") else {
        "model": "unknown",
        "base_url": "",
        "max_tokens": 4096,
        "temperature": 0.7,
    }

    # Derive agent state from running flag + recent traces
    recent_traces = store.recent(10)
    running = agent.is_running if hasattr(agent, "is_running") else False
    agent_state = _derive_agent_state(running, recent_traces)

    # Build a human-readable description of the last activity (if any)
    last_activity = ""
    if recent_traces:
        latest = recent_traces[-1]
        last_activity = latest.label or latest.trace_type or "activity"
    elif not running:
        last_activity = "Agent stopped"

    # Session count from trace stats (proxied by total traces)
    session_count = stats["total_traces"]

    return {
        "name": getattr(config, "name", "kazma") if config else "kazma",
        "running": running,
        "agent_state": agent_state,
        "last_activity": last_activity,
        "session_count": session_count,
        "config": {
            "name": getattr(config, "name", "kazma") if config else "kazma",
            "version": getattr(config, "version", "0.1.0") if config else "0.1.0",
            "language": getattr(config, "language", "ar") if config else "ar",
            "rtl": getattr(config, "rtl", True) if config else True,
            "default_model": getattr(config, "default_model", "gpt-4o-mini") if config else "gpt-4o-mini",
            "system_prompt": (getattr(config, "system_prompt", "") or "")[:200] if config else "",
        },
        "llm": {
            "model": llm_info.get("model", "unknown"),
            "base_url": llm_info.get("base_url", ""),
            "max_tokens": llm_info.get("max_tokens", 4096),
            "temperature": llm_info.get("temperature", 0.7),
        },
        "tools": {
            "count": tools_info["count"],
            "servers": tools_info["servers"],
            "list": tools_info["list"],
        },
        "metrics": {
            "total_cost": f"${stats['total_cost']:.4f}",
            "total_tokens": f"{stats['total_tokens']:,}",
            "total_llm_calls": stats["total_llm_calls"],
            "total_tool_calls": stats["total_tool_calls"],
        },
    }
