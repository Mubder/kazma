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

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_tracer: KazmaTracer | None = None
_cost_breaker: CostCircuitBreaker | None = None


def set_dashboard_context(
    tracer: KazmaTracer | None = None,
    cost_breaker: CostCircuitBreaker | None = None,
) -> None:
    """Set the tracer and cost breaker for the dashboard to read."""
    global _tracer, _cost_breaker
    _tracer = tracer
    _cost_breaker = cost_breaker


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

        traces.append({
            "time": t,
            "trace_type": entry.trace_type,
            "label": entry.label,
            "status": entry.status,
            "badge_class": badge_class,
            "duration_ms": f"{entry.duration_ms:.0f}",
            "tokens": entry.tokens,
            "cost": f"${entry.cost:.4f}",
            "details": entry.details,
        })
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
            "silence_remaining": float("inf"),
        },
    }

    if _tracer:
        status["tracing_backend"] = _tracer.backend.value

    if _cost_breaker:
        cb_status = _cost_breaker.status()
        status["cost"] = {
            "current": cb_status["current_cost"],
            "max": cb_status["max_cost"],
            "headroom": cb_status["cost_headroom"],
        }
        status["circuit_breaker"] = {
            "is_halted": cb_status["is_halted"],
            "seconds_since_user": cb_status["seconds_since_user"],
            "silence_remaining": cb_status["silence_remaining"],
        }

    status["metrics"] = _get_metrics()
    status["traces"] = _get_trace_data()

    return JSONResponse(status)
