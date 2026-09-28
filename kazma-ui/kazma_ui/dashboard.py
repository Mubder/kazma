"""Kazma Dashboard — FastAPI route for observability dashboard.

Provides a local web UI at /dashboard showing real-time traces, costs,
metrics, and circuit breaker status with auto-refresh.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from kazma_ui.auth import require_admin

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

# Sentinel so callers can update a subset of globals without clobbering the
# others (e.g. _on_startup sets only checkpoint_manager). Using None for both
# "not provided" and "clear to None" would wipe tracer/session_store on the
# second partial call — which is exactly the bug that left Session Management
# always empty.
_UNSET: Any = object()


def set_dashboard_context(
    tracer: Any = _UNSET,
    cost_breaker: Any = _UNSET,
    checkpoint_manager: Any = _UNSET,
    session_store: Any = _UNSET,
) -> None:
    """Set the tracer, cost breaker, checkpoint manager, and session store for the dashboard.

    Only arguments explicitly passed are updated; omitted arguments preserve
    their existing value. Pass ``None`` explicitly to clear one.
    """
    global _tracer, _cost_breaker, _checkpoint_manager, _session_store
    if tracer is not _UNSET:
        _tracer = tracer
    if cost_breaker is not _UNSET:
        _cost_breaker = cost_breaker
    if checkpoint_manager is not _UNSET:
        _checkpoint_manager = checkpoint_manager
    if session_store is not _UNSET:
        _session_store = session_store


def get_dashboard_context() -> dict[str, Any]:
    """What :func:`set_dashboard_context` last set, as its keyword arguments.

    The test suite restores it after every test: an app built by one test
    left its gateway session store here, and the next test that deleted a
    session opened that store's aiosqlite connection -- whose thread kept the
    process from ever exiting (2026-09-27).
    """
    return {
        "tracer": _tracer,
        "cost_breaker": _cost_breaker,
        "checkpoint_manager": _checkpoint_manager,
        "session_store": _session_store,
    }


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


def _format_uptime(uptime_seconds: float) -> str:
    """Human-readable process uptime for the dashboard card."""
    secs = max(0, int(uptime_seconds))
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    rem_m = mins % 60
    if hours < 48:
        return f"{hours}h {rem_m}m"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d {rem_h}h"


def _get_metrics() -> dict[str, Any]:
    """Get aggregate metrics from the in-memory TraceStore.

    Returns **numeric** totals for cost/tokens/calls so the dashboard JS can
    format them (legacy string forms like ``"$0.12"`` / ``"1,234"`` broke
    ``Number()`` and painted NaN/0 on refresh).
    """
    from kazma_core.tracing import get_trace_store

    store = get_trace_store()
    stats = store.stats()
    uptime_s = float(stats.get("uptime_seconds") or 0)
    return {
        "total_cost": float(stats.get("total_cost") or 0.0),
        "total_tokens": int(stats.get("total_tokens") or 0),
        "total_llm_calls": int(stats.get("total_llm_calls") or 0),
        "total_tool_calls": int(stats.get("total_tool_calls") or 0),
        "total_traces": int(stats.get("total_traces") or 0),
        "uptime_seconds": uptime_s,
        "uptime": _format_uptime(uptime_s),
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render observability dashboard with traces, costs, and metrics."""
    from kazma_ui.i18n import make_translator

    cookie_lang = request.cookies.get("kazma-lang")
    _ = make_translator(cookie_lang if cookie_lang in ("ar", "en") else "en")

    cost_current = 0.0
    cost_max = 0.50
    cost_headroom = 0.50
    breaker_status = _("dashboard.breaker_ok")
    breaker_color = "text-success"
    cost_color = "text-success"
    silence_info = _("dashboard.no_cost_threshold")

    if _cost_breaker:
        status = _cost_breaker.status()
        cost_current = status["current_cost"]
        cost_max = status["max_cost"]
        cost_headroom = status["cost_headroom"]
        is_halted = status["is_halted"]

        if is_halted:
            breaker_status = _("dashboard.breaker_halted")
            breaker_color = "text-error"
            cost_color = "text-error"
            silence_info = _("dashboard.halted_spent", amount=f"{cost_current:.4f}")
        elif cost_current >= cost_max:
            breaker_status = _("dashboard.breaker_warning")
            breaker_color = "text-warning"
            cost_color = "text-warning"
            remaining = status["silence_remaining"]
            silence_info = _("dashboard.over_budget", seconds=f"{remaining:.0f}")
        else:
            silence_info = _("dashboard.headroom_remaining", amount=f"{cost_headroom:.4f}")

    tracing_backend = "console"
    if _tracer:
        tracing_backend = _tracer.backend.value

    raw_metrics = _get_metrics()
    # Prefer TraceStore totals for the cost card when the cost breaker is
    # still at zero (e.g. breaker not yet wired) but LLM traces accumulated.
    display_cost = max(float(cost_current or 0.0), float(raw_metrics.get("total_cost") or 0.0))
    if display_cost > cost_current:
        cost_current = display_cost
        cost_headroom = max(0.0, cost_max - cost_current)

    # Template expects display strings for some fields (legacy SSR)
    metrics_ssr = {
        **raw_metrics,
        "total_cost": f"${raw_metrics['total_cost']:.4f}",
        "total_tokens": f"{raw_metrics['total_tokens']:,}",
    }

    active_model = ""
    active_provider = ""
    try:
        from kazma_core.model_registry import get_model_registry

        prof = get_model_registry().get_active_profile() or {}
        active_model = str(prof.get("model") or "")
        active_provider = str(prof.get("provider") or "")
    except Exception:
        pass

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
            "metrics": metrics_ssr,
            "active_model": active_model,
            "active_provider": active_provider,
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

    # Active LLM profile chip (reliability sprint 2)
    try:
        from kazma_core.model_registry import get_model_registry

        prof = get_model_registry().get_active_profile() or {}
        status["active_model"] = str(prof.get("model") or "")
        status["active_provider"] = str(prof.get("provider") or "")
    except Exception:
        status["active_model"] = ""
        status["active_provider"] = ""

    return JSONResponse(status)


# ══════════════════════════════════════════════════════════════════════════
# Session Management API
# ══════════════════════════════════════════════════════════════════════════


def _chats_by_thread() -> dict[str, dict[str, Any]]:
    """Every chat of the caller's tenant, archived and empty ones included,
    keyed by its thread. Blocking store reads: callers run it in a thread."""
    from kazma_ui.thread_ownership import chats_by_thread

    return chats_by_thread()


def _delete_chats_of_thread(thread_id: str) -> None:
    """Delete every chat whose thread or id is *thread_id*. Blocking."""
    from kazma_ui.session_manager import get_session_manager

    sm = get_session_manager()
    doomed = {thread_id} | {
        s.session_id
        for s in sm.list_all(include_archived=True, include_empty=True, prune_empty=False)
        if thread_id in (s.thread_id, s.session_id)
    }
    for session_id in doomed:
        sm.delete(session_id)


@router.get("/api/sessions")
async def list_sessions(request: Request, limit: int = 50) -> JSONResponse:
    """The checkpointed threads, newest activity first, each with its chat.

    A thread's title, platform and message count come from the chat it
    belongs to (``kazma_chat_sessions``): the rows used to be enriched from
    the gateway's five-minute session cache and the checkpoint's own blob,
    so on the live install every row read "unknown / anonymous / 0 / -"
    (2026-09-28). A thread no chat owns (a deleted chat's leftovers, a
    worker's run) has an empty ``session_id`` and no title.

    Returns ``{"sessions": [...], "count": n}``; each row has ``thread_id``,
    ``checkpoint_id``, ``steps`` (checkpoints kept), ``last_activity``,
    ``session_id``, ``title``, ``platform``, ``message_count`` (``None``
    when unknown) and ``archived``.
    """
    # Every thread, every tenant: this is the instance's checkpoint store, so
    # it is an admin view. It used to be open to any role — a viewer could list
    # every thread and an operator could delete one or clear them all (audit
    # 2026-09-22). platform_rbac lists /api/sessions as admin-only as well.
    if (denied := require_admin(request)) is not None:
        return denied
    if not _checkpoint_manager:
        return JSONResponse({"sessions": [], "error": "CheckpointManager not initialized"})

    try:
        checkpoints = await _checkpoint_manager.list_checkpoints(limit=limit)
        try:
            chats = await asyncio.to_thread(_chats_by_thread)
        except Exception:
            logger.warning("[Dashboard] chat list unavailable for the session table", exc_info=True)
            chats = {}

        sessions = []
        for cp in checkpoints:
            thread_id = str(cp.get("thread_id") or "")
            chat = chats.get(thread_id) or {}
            count = chat.get("message_count") if chat else cp.get("message_count")
            sessions.append({
                "thread_id": thread_id,
                "checkpoint_id": str(cp.get("checkpoint_id") or ""),
                "steps": int(cp.get("steps") or 0),
                "last_activity": str(cp.get("last_activity") or ""),
                "session_id": str(chat.get("session_id") or ""),
                "title": str(chat.get("title") or ""),
                "platform": str(chat.get("platform") or ""),
                "message_count": count,
                "archived": bool(chat.get("archived")),
            })

        return JSONResponse({"sessions": sessions, "count": len(sessions)})
    except Exception:
        logger.exception("Failed to list sessions")
        return JSONResponse({"sessions": [], "error": "Internal error"}, status_code=500)


@router.delete("/api/sessions/{thread_id}")
async def delete_session(request: Request, thread_id: str) -> JSONResponse:
    """Delete all checkpoints for a specific thread.
    
    Args:
        thread_id: The thread ID to delete.
    
    Returns:
        JSONResponse with deletion result:
        {"deleted": bool, "thread_id": str, "message": str}
    """
    # Every thread, every tenant: this is the instance's checkpoint store, so
    # it is an admin view. It used to be open to any role — a viewer could list
    # every thread and an operator could delete one or clear them all (audit
    # 2026-09-22). platform_rbac lists /api/sessions as admin-only as well.
    if (denied := require_admin(request)) is not None:
        return denied
    if not _checkpoint_manager:
        return JSONResponse({"deleted": False, "error": "CheckpointManager not initialized"}, status_code=500)
    
    try:
        # Postgres checkpointers reject the SQLite '?' placeholder. The
        # saver's own delete knows the dialect and the related tables.
        if hasattr(_checkpoint_manager, "adelete_thread"):
            await _checkpoint_manager.adelete_thread(thread_id)
        else:
            conn = _checkpoint_manager.conn if hasattr(_checkpoint_manager, "conn") else None
            if not conn:
                return JSONResponse({"deleted": False, "error": "Database not initialized"}, status_code=500)
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

        # The chat the thread belongs to (web and platform ids alike). Store
        # writes: off the event loop.
        try:
            await asyncio.to_thread(_delete_chats_of_thread, thread_id)
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
async def clear_all_sessions(request: Request) -> JSONResponse:
    """Delete ALL checkpointed sessions.
    
    WARNING: This is a destructive operation. Use with caution.
    
    Returns:
        JSONResponse with deletion result:
        {"deleted": bool, "count": int, "message": str}
    """
    # Every thread, every tenant: this is the instance's checkpoint store, so
    # it is an admin view. It used to be open to any role — a viewer could list
    # every thread and an operator could delete one or clear them all (audit
    # 2026-09-22). platform_rbac lists /api/sessions as admin-only as well.
    if (denied := require_admin(request)) is not None:
        return denied
    if not _checkpoint_manager:
        return JSONResponse({"deleted": False, "error": "CheckpointManager not initialized"}, status_code=500)
    
    try:
        if hasattr(_checkpoint_manager, "adelete_all_threads"):
            count = await _checkpoint_manager.adelete_all_threads()
        else:
            conn = _checkpoint_manager.conn if hasattr(_checkpoint_manager, "conn") else None
            if not conn:
                return JSONResponse({"deleted": False, "error": "Database not initialized"}, status_code=500)
            cursor = await conn.execute("SELECT COUNT(*) FROM checkpoints")
            count = (await cursor.fetchone())[0]
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
