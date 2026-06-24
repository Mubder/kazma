"""Kazma Dashboard — FastAPI route for observability dashboard.

Provides a local web UI at /dashboard showing real-time traces, costs,
metrics, and circuit breaker status with auto-refresh.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from kazma_core.cost_breaker import CostCircuitBreaker
    from kazma_core.tracing import KazmaTracer
    from kazma_core.checkpoint import CheckpointManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_tracer: KazmaTracer | None = None
_cost_breaker: CostCircuitBreaker | None = None
_checkpoint_manager: CheckpointManager | None = None


def set_dashboard_context(
    tracer: KazmaTracer | None = None,
    cost_breaker: CostCircuitBreaker | None = None,
    checkpoint_manager: CheckpointManager | None = None,
) -> None:
    """Set the tracer, cost breaker, and checkpoint manager for the dashboard to read."""
    global _tracer, _cost_breaker, _checkpoint_manager
    _tracer = tracer
    _cost_breaker = cost_breaker
    _checkpoint_manager = checkpoint_manager


def _get_trace_data() -> list[dict[str, Any]]:
    """Get recent traces from the in-memory store, formatted for the template."""
    from kazma_core.tracing import get_trace_store

    store = get_trace_store()
    traces = []
    for entry in reversed(store.recent(50)):  # newest first
        t = time.strftime("%H:%M:%S", time.localtime(entry.timestamp))
        badge_class = {
            "success": "badge-stdio",
            "error": "badge-premium",
            "warning": "badge-standard",
        }.get(entry.status, "badge-basic")

        traces.append(
            {
                "time": t,
                "trace_type": entry.trace_type,
                "label": entry.label,
                "status": entry.status,
                "badge_class": badge_class,
                "duration_ms": f"{entry.duration_ms:.0f}",
                "tokens": entry.tokens,
                "cost": f"${entry.cost:.4f}",
                "details": entry.details,
            }
        )
    return traces


def _get_metrics() -> dict[str, Any]:
    """Get aggregate metrics from the trace store."""
    from kazma_core.tracing import get_trace_store

    store = get_trace_store()
    stats = store.stats()
    uptime_mins = int(stats["uptime_seconds"] / 60)
    return {
        "total_cost": f"${stats['total_cost']:.4f}",
        "total_tokens": f"{stats['total_tokens']:,}",
        "total_llm_calls": stats["total_llm_calls"],
        "total_tool_calls": stats["total_tool_calls"],
        "total_traces": stats["total_traces"],
        "uptime": f"{uptime_mins}m" if uptime_mins < 60 else f"{uptime_mins // 60}h {uptime_mins % 60}m",
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render observability dashboard with traces, costs, and metrics."""
    cost_current = 0.0
    cost_max = 0.50
    cost_headroom = 0.50
    breaker_status = "OK"
    breaker_color = "text-success"
    cost_color = "text-success"
    silence_info = "No cost threshold reached"

    if _cost_breaker:
        status = _cost_breaker.status()
        cost_current = status["current_cost"]
        cost_max = status["max_cost"]
        cost_headroom = status["cost_headroom"]
        is_halted = status["is_halted"]

        if is_halted:
            breaker_status = "HALTED"
            breaker_color = "text-error"
            cost_color = "text-error"
            silence_info = f"Halted — ${cost_current:.4f} spent, user silence exceeded"
        elif cost_current >= cost_max:
            breaker_status = "WARNING"
            breaker_color = "text-warning"
            cost_color = "text-warning"
            remaining = status["silence_remaining"]
            silence_info = f"Over budget — {remaining:.0f}s until halt"
        else:
            silence_info = f"${cost_headroom:.4f} headroom remaining"

    tracing_backend = "console"
    if _tracer:
        tracing_backend = _tracer.backend.value

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "cost_current": cost_current,
            "cost_max": cost_max,
            "cost_headroom": cost_headroom,
            "cost_color": cost_color,
            "breaker_status": breaker_status,
            "breaker_color": breaker_color,
            "silence_info": silence_info,
            "tracing_backend": tracing_backend,
            "traces": _get_trace_data(),
            "metrics": _get_metrics(),
        },
    )


def _safe_silence(sr: Any) -> float | None:
    """Convert float('inf') to None for JSON safety."""
    if sr is None:
        return None
    if isinstance(sr, float) and (sr == float("inf") or sr == float("-inf")):
        return None
    return round(float(sr), 2)


@router.get("/api/dashboard/status")
async def dashboard_status() -> JSONResponse:
    """JSON endpoint for dashboard status (for AJAX refresh)."""
    status: dict[str, Any] = {
        "tracing_backend": "console",
        "cost": {
            "current": 0.0,
            "max": 0.50,
            "headroom": 0.50,
        },
        "circuit_breaker": {
            "is_halted": False,
            "seconds_since_user": 0.0,
            "silence_remaining": None,
        },
    }

    if _tracer:
        status["tracing_backend"] = _tracer.backend.value

    if _cost_breaker:
        cb_status = _cost_breaker.status()
        status["cost"] = {
            "current": cb_status.get("current_cost", 0.0),
            "max": cb_status.get("max_cost", 0.50),
            "headroom": cb_status.get("cost_headroom", 0.50),
        }
        status["circuit_breaker"] = {
            "is_halted": cb_status.get("is_halted", False),
            "seconds_since_user": cb_status.get("seconds_since_user", 0.0),
            "silence_remaining": _safe_silence(cb_status.get("silence_remaining")),
        }

    status["metrics"] = _get_metrics()
    status["traces"] = _get_trace_data()

    return JSONResponse(status)


# ══════════════════════════════════════════════════════════════════════════
# Session Management API
# ══════════════════════════════════════════════════════════════════════════


@router.get("/api/sessions")
async def list_sessions(limit: int = 50) -> JSONResponse:
    """List all checkpointed sessions with metadata.
    
    Args:
        limit: Maximum number of sessions to return (default 50).
    
    Returns:
        JSONResponse with list of sessions:
        [
            {
                "thread_id": str,
                "checkpoint_id": str,
                "created_at": str,
                "context_tokens": int,
                "message_count": int,
                "platform": str | None,
                "display_name": str | None,
            }
        ]
    """
    if not _checkpoint_manager:
        return JSONResponse({"sessions": [], "error": "CheckpointManager not initialized"})
    
    try:
        checkpoints = await _checkpoint_manager.list_checkpoints(limit=limit)
        
        # Enrich with platform/display_name from session store if available
        # For now, return raw checkpoint data
        sessions = []
        for cp in checkpoints:
            sessions.append({
                "thread_id": cp.get("thread_id", cp.get("id", "unknown")),
                "checkpoint_id": cp.get("id", ""),
                "created_at": cp.get("created_at", ""),
                "context_tokens": cp.get("context_tokens", 0),
                "message_count": cp.get("message_count", 0),
                "platform": None,  # TODO: Extract from session store
                "display_name": None,  # TODO: Extract from session store
            })
        
        return JSONResponse({"sessions": sessions, "count": len(sessions)})
    except Exception as e:
        logger.exception("Failed to list sessions")
        return JSONResponse({"sessions": [], "error": str(e)}, status_code=500)


@router.delete("/api/sessions/{thread_id}")
async def delete_session(thread_id: str) -> JSONResponse:
    """Delete all checkpoints for a specific thread.
    
    Args:
        thread_id: The thread ID to delete.
    
    Returns:
        JSONResponse with deletion result:
        {"deleted": bool, "thread_id": str, "message": str}
    """
    if not _checkpoint_manager:
        return JSONResponse({"deleted": False, "error": "CheckpointManager not initialized"}, status_code=500)
    
    try:
        # Use prune() with a filter or implement delete_thread() in CheckpointManager
        # For now, we'll delete all checkpoints for this thread
        conn = _checkpoint_manager._conn
        if not conn:
            return JSONResponse({"deleted": False, "error": "Database not initialized"}, status_code=500)
        
        # Delete all checkpoints for this thread_id
        await conn.execute(
            "DELETE FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        await conn.commit()
        
        logger.info("Deleted session: %s", thread_id)
        return JSONResponse({
            "deleted": True,
            "thread_id": thread_id,
            "message": f"Session {thread_id} deleted successfully",
        })
    except Exception as e:
        logger.exception("Failed to delete session %s", thread_id)
        return JSONResponse({"deleted": False, "error": str(e)}, status_code=500)


@router.post("/api/sessions/clear-all")
async def clear_all_sessions() -> JSONResponse:
    """Delete ALL checkpointed sessions.
    
    WARNING: This is a destructive operation. Use with caution.
    
    Returns:
        JSONResponse with deletion result:
        {"deleted": bool, "count": int, "message": str}
    """
    if not _checkpoint_manager:
        return JSONResponse({"deleted": False, "error": "CheckpointManager not initialized"}, status_code=500)
    
    try:
        conn = _checkpoint_manager._conn
        if not conn:
            return JSONResponse({"deleted": False, "error": "Database not initialized"}, status_code=500)
        
        # Count before deletion
        cursor = await conn.execute("SELECT COUNT(*) FROM checkpoints")
        count = (await cursor.fetchone())[0]
        
        # Delete all checkpoints
        await conn.execute("DELETE FROM checkpoints")
        await conn.commit()
        
        logger.warning("Cleared ALL sessions: %d checkpoints deleted", count)
        return JSONResponse({
            "deleted": True,
            "count": count,
            "message": f"Cleared {count} checkpoint(s)",
        })
    except Exception as e:
        logger.exception("Failed to clear all sessions")
        return JSONResponse({"deleted": False, "error": str(e)}, status_code=500)
