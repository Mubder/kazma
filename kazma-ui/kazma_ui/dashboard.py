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
    from kazma_gateway.gateway import SessionStore
    from kazma_gateway.stores.checkpoint import CheckpointManager

logger = logging.getLogger(__name__)

__all__ = [
    "clear_all_sessions",
    "dashboard",
    "dashboard_status",
    "delete_session",
    "list_sessions",
    "router",
    "set_dashboard_context",
    "set_templates",
]

router = APIRouter(tags=["dashboard"])

# Start with a fallback templates instance (gets English defaults from the
# i18n Jinja2 patch).  ``create_app()`` will replace this with the shared
# app-level instance via ``set_templates()`` so the dashboard uses the
# correct per-request language globals.
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


def set_templates(tmpl: Jinja2Templates) -> None:
    """Replace the module-level templates instance with the app's shared one.

    Called by ``create_app()`` after building the main Jinja2Templates so
    that the dashboard route renders with the correct per-request i18n
    globals (t, lang, dir) instead of its own isolated instance.
    """
    global templates
    templates = tmpl

_tracer: KazmaTracer | None = None
_cost_breaker: CostCircuitBreaker | None = None
_checkpoint_manager: CheckpointManager | None = None
_session_store: SessionStore | None = None


def set_dashboard_context(
    tracer: KazmaTracer | None = None,
    cost_breaker: CostCircuitBreaker | None = None,
    checkpoint_manager: CheckpointManager | None = None,
    session_store: SessionStore | None = None,
) -> None:
    """Set the tracer, cost breaker, checkpoint manager, and session store for the dashboard."""
    global _tracer, _cost_breaker, _checkpoint_manager, _session_store
    _tracer = tracer
    _cost_breaker = cost_breaker
    _checkpoint_manager = checkpoint_manager
    _session_store = session_store


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
            "active_page": "dashboard",
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

        # Build a lookup of session metadata from the session store so we can
        # enrich checkpointed sessions with platform and display_name.
        session_meta: dict[str, dict[str, Any]] = {}
        if _session_store is not None:
            try:
                for entry in await _session_store.list_active():
                    session_meta[entry["thread_id"]] = {
                        "platform": entry.get("platform", "unknown"),
                        "display_name": entry.get("display_name", "unknown"),
                    }
            except Exception:
                logger.exception("Failed to load session metadata")

        sessions = []
        for cp in checkpoints:
            thread_id = cp.get("thread_id", cp.get("id", "unknown"))
            meta = session_meta.get(thread_id, {})
            sessions.append({
                "thread_id": thread_id,
                "checkpoint_id": cp.get("id", ""),
                "created_at": cp.get("created_at", ""),
                "context_tokens": cp.get("context_tokens", 0),
                "message_count": cp.get("message_count", 0),
                "platform": meta.get("platform"),
                "display_name": meta.get("display_name"),
            })

        return JSONResponse({"sessions": sessions, "count": len(sessions)})
    except Exception:
        logger.exception("Failed to list sessions")
        return JSONResponse({"sessions": [], "error": "Internal error"}, status_code=500)


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
        # Use the CheckpointManager's public conn property instead of
        # the private _conn attribute.
        conn = _checkpoint_manager.conn if hasattr(_checkpoint_manager, "conn") else None
        if not conn:
            return JSONResponse({"deleted": False, "error": "Database not initialized"}, status_code=500)
        
        # Delete all checkpoints for this thread_id
        await conn.execute(
            "DELETE FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        await conn.commit()

        # Gateway platform SessionStore (chat_id mapping)
        try:
            if _session_store is not None:
                await _session_store.delete(thread_id)
        except Exception as exc:
            logger.debug("gateway session store delete skipped: %s", exc)

        # Web UI chat projection
        try:
            from kazma_ui.session_manager import get_session_manager

            sm = get_session_manager()
            sm.delete(thread_id)
            # Also try platform-prefixed ids that share this thread
            for s in list(sm.list_all(include_archived=True)):
                if s.thread_id == thread_id or s.session_id == thread_id:
                    sm.delete(s.session_id)
        except Exception as exc:
            logger.debug("SessionManager delete skipped: %s", exc)

        logger.info("Deleted session: %s (checkpoints + stores)", thread_id)
        return JSONResponse({
            "deleted": True,
            "thread_id": thread_id,
            "message": f"Session {thread_id} deleted successfully",
        })
    except Exception:
        logger.exception("Failed to delete session %s", thread_id)
        return JSONResponse({"deleted": False, "error": "Internal error"}, status_code=500)


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
        # Use the CheckpointManager's public conn property instead of
        # the private _conn attribute.
        conn = _checkpoint_manager.conn if hasattr(_checkpoint_manager, "conn") else None
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
    except Exception:
        logger.exception("Failed to clear all sessions")
        return JSONResponse({"deleted": False, "error": "Internal error"}, status_code=500)
