"""Kazma Dashboard — FastAPI route for observability dashboard.

Provides a local web UI at /dashboard showing real-time traces, costs,
and circuit breaker status.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

if TYPE_CHECKING:
    from kazma_core.cost_breaker import CostCircuitBreaker
    from kazma_core.tracing import KazmaTracer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

# These will be set by the app factory or agent startup
_tracer: KazmaTracer | None = None
_cost_breaker: CostCircuitBreaker | None = None


def set_dashboard_context(
    tracer: KazmaTracer | None = None,
    cost_breaker: CostCircuitBreaker | None = None,
) -> None:
    """Set the tracer and cost breaker for the dashboard to read.

    Called during agent startup to wire the dashboard to live state.
    """
    global _tracer, _cost_breaker
    _tracer = tracer
    _cost_breaker = cost_breaker


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kazma — Observability Dashboard</title>
    <style>
        :root {{
            --bg: #0d1117;
            --surface: #161b22;
            --border: #30363d;
            --text: #e6edf3;
            --text-muted: #8b949e;
            --accent: #58a6ff;
            --green: #3fb950;
            --yellow: #d29922;
            --red: #f85149;
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: var(--bg);
            color: var(--text);
            padding: 2rem;
        }}
        h1 {{
            font-size: 1.5rem;
            margin-bottom: 1.5rem;
            color: var(--accent);
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 1.25rem;
        }}
        .card h2 {{
            font-size: 0.875rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.75rem;
        }}
        .metric {{
            font-size: 2rem;
            font-weight: 600;
        }}
        .metric.green {{ color: var(--green); }}
        .metric.yellow {{ color: var(--yellow); }}
        .metric.red {{ color: var(--red); }}
        .label {{
            font-size: 0.75rem;
            color: var(--text-muted);
            margin-top: 0.25rem;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.875rem;
        }}
        th, td {{
            text-align: left;
            padding: 0.5rem 0.75rem;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            color: var(--text-muted);
            font-weight: 500;
        }}
        .badge {{
            display: inline-block;
            padding: 0.125rem 0.5rem;
            border-radius: 12px;
            font-size: 0.75rem;
            font-weight: 500;
        }}
        .badge-ok {{ background: rgba(63,185,80,0.15); color: var(--green); }}
        .badge-warn {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
        .badge-err {{ background: rgba(248,81,73,0.15); color: var(--red); }}
        .empty {{ color: var(--text-muted); font-style: italic; padding: 1rem; }}
        .refresh {{ color: var(--text-muted); font-size: 0.75rem; margin-top: 1rem; }}
    </style>
</head>
<body>
    <h1>🌊 Kazma Observability Dashboard</h1>

    <div class="grid">
        <div class="card">
            <h2>Cost</h2>
            <div class="metric {cost_color}">${cost_current:.4f}</div>
            <div class="label">of ${cost_max:.2f} budget ({cost_headroom:.4f} remaining)</div>
        </div>
        <div class="card">
            <h2>Circuit Breaker</h2>
            <div class="metric {breaker_color}">{breaker_status}</div>
            <div class="label">{silence_info}</div>
        </div>
        <div class="card">
            <h2>Tracing Backend</h2>
            <div class="metric">{tracing_backend}</div>
            <div class="label">Langfuse dashboard: localhost:3000</div>
        </div>
    </div>

    <div class="card">
        <h2>Recent Traces</h2>
        {traces_html}
    </div>

    <div class="refresh">Auto-refresh: 30s | Backend: {tracing_backend}</div>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    """Render observability dashboard with traces, costs, and metrics."""
    # Cost metrics
    cost_current = 0.0
    cost_max = 0.50
    cost_headroom = 0.50
    breaker_status = "OK"
    breaker_color = "green"
    cost_color = "green"
    silence_info = "No cost threshold reached"

    if _cost_breaker:
        status = _cost_breaker.status()
        cost_current = status["current_cost"]
        cost_max = status["max_cost"]
        cost_headroom = status["cost_headroom"]
        is_halted = status["is_halted"]

        if is_halted:
            breaker_status = "HALTED"
            breaker_color = "red"
            cost_color = "red"
            silence_info = f"Halted — ${cost_current:.4f} spent, user silence exceeded"
        elif cost_current >= cost_max:
            breaker_status = "WARNING"
            breaker_color = "yellow"
            cost_color = "yellow"
            remaining = status["silence_remaining"]
            silence_info = f"Over budget — {remaining:.0f}s until halt"
        else:
            silence_info = f"${cost_headroom:.4f} headroom remaining"

    # Tracing backend
    tracing_backend = "console"
    if _tracer:
        tracing_backend = _tracer.backend.value

    # Recent traces (placeholder — in production, query Langfuse or in-memory store)
    traces_html = '<div class="empty">No traces recorded yet. Start the agent to see traces.</div>'

    html = DASHBOARD_HTML.format(
        cost_current=cost_current,
        cost_max=cost_max,
        cost_headroom=cost_headroom,
        cost_color=cost_color,
        breaker_status=breaker_status,
        breaker_color=breaker_color,
        silence_info=silence_info,
        tracing_backend=tracing_backend,
        traces_html=traces_html,
    )
    return HTMLResponse(content=html)


@router.get("/api/dashboard/status")
async def dashboard_status() -> dict:
    """JSON endpoint for dashboard status (for AJAX refresh)."""
    status: dict = {
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

    return status
