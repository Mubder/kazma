"""Prometheus metrics endpoint for Kazma Gateway.

Exposes gateway metrics in Prometheus text format.
No external dependencies — generates plain text from existing data.

Usage:
    GET /metrics → Prometheus scrape target
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)


def create_metrics_router(gateway: Any, session_store: Any = None) -> APIRouter:
    """Create a Prometheus metrics router.

    Args:
        gateway:       GatewayManager instance (for metrics + adapter status).
        session_store: SQLiteSessionStore (for active thread count).

    Returns:
        APIRouter with GET /metrics.
    """

    router = APIRouter(tags=["metrics"])

    @router.get("/metrics")
    async def metrics() -> PlainTextResponse:
        """Prometheus text format metrics."""
        lines: list[str] = []

        # ── Message counters ──────────────────────────────────────
        m = gateway.metrics

        lines.append("# HELP kazma_messages_inbound_total Total inbound messages")
        lines.append("# TYPE kazma_messages_inbound_total counter")
        lines.append(f"kazma_messages_inbound_total {m.inbound_total}")

        lines.append("# HELP kazma_messages_outbound_total Total outbound messages")
        lines.append("# TYPE kazma_messages_outbound_total counter")
        lines.append(f"kazma_messages_outbound_total {m.outbound_total}")

        lines.append("# HELP kazma_messages_errors_total Total message errors")
        lines.append("# TYPE kazma_messages_errors_total counter")
        lines.append(f"kazma_messages_errors_total {m.errors_total}")

        # ── Active threads ────────────────────────────────────────
        active_threads = 0
        if session_store is not None and hasattr(session_store, "list_active"):
            try:
                sessions = await session_store.list_active()
                active_threads = len(sessions)
            except Exception:
                pass

        lines.append("# HELP kazma_active_threads Current active conversation threads")
        lines.append("# TYPE kazma_active_threads gauge")
        lines.append(f"kazma_active_threads {active_threads}")

        # ── Connected adapters ────────────────────────────────────
        connected = sum(1 for a in gateway.adapters if a.is_running)

        lines.append("# HELP kazma_adapters_connected Adapters currently connected")
        lines.append("# TYPE kazma_adapters_connected gauge")
        lines.append(f"kazma_adapters_connected {connected}")

        # ── Queue depth ───────────────────────────────────────────
        lines.append("# HELP kazma_queue_depth Current message queue depth")
        lines.append("# TYPE kazma_queue_depth gauge")
        lines.append(f"kazma_queue_depth {gateway.queue.qsize()}")

        return PlainTextResponse(
            content="\n".join(lines) + "\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return router
