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

__all__ = ["create_metrics_router"]


def create_metrics_router(gateway: Any, session_store: Any = None) -> APIRouter:
    """Create a Prometheus metrics router.

    Args:
        gateway:       GatewayManager instance (for metrics + adapter status).
        session_store: SQLiteSessionStore (for active thread count).

    Returns:
        APIRouter with GET /metrics and GET /api/metrics.
    """

    router = APIRouter(tags=["metrics"])

    @router.get("/metrics")
    @router.get("/api/metrics")
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
            except Exception as exc:
                logger.debug("list_active failed for metrics: %s", exc)

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

        # ── Swarm indicators ──────────────────────────────────────
        try:
            from kazma_core.swarm import get_swarm_engine
            engine = get_swarm_engine()
            if engine is not None:
                # 1. Active tasks gauge
                active_tasks = len(engine.list_active_tasks()) if hasattr(engine, "list_active_tasks") else 0
                lines.append("# HELP kazma_active_tasks Current in-flight swarm tasks")
                lines.append("# TYPE kazma_active_tasks gauge")
                lines.append(f"kazma_active_tasks {active_tasks}")

                # 2. Worker status gauge
                lines.append("# HELP kazma_worker_status Swarm worker status gauge")
                lines.append("# TYPE kazma_worker_status gauge")
                workers = engine.list_workers() if hasattr(engine, "list_workers") else getattr(engine, "_workers", {}).values()
                for worker in workers:
                    status = "offline"
                    if getattr(worker, "_running", False):
                        status = "busy" if getattr(worker, "busy", False) else "online"
                    for possible_status in ("offline", "online", "busy"):
                        val = 1 if status == possible_status else 0
                        lines.append(
                            f'kazma_worker_status{{worker="{worker.name}",status="{possible_status}"}} {val}'
                        )

                # 3. Circuit breaker failures
                lines.append("# HELP kazma_circuit_breaker_failures_total Cumulative consecutive failures of worker circuit breakers")
                lines.append("# TYPE kazma_circuit_breaker_failures_total counter")
                for worker in workers:
                    failures = 0
                    if hasattr(engine, "_reliability"):
                        try:
                            breaker = (engine.get_circuit_breaker(worker.name) if hasattr(engine, "get_circuit_breaker")
                                       else getattr(engine, "_reliability", None).get_circuit_breaker(worker.name) if getattr(engine, "_reliability", None) else None)
                            failures = getattr(breaker, "consecutive_failures", 0)
                        except Exception as exc:
                            logger.debug("breaker metric for %s: %s", worker.name, exc)
                    lines.append(f'kazma_circuit_breaker_failures_total{{worker="{worker.name}"}} {failures}')
        except Exception as exc:
            logger.debug("Failed to append Swarm metrics: %s", exc)

        # ── Commitment Layer metrics ─────────────────────────────
        try:
            import asyncio as _aio

            def _commitment_metric_lines() -> list[str]:
                import sqlite3 as _sqlite3
                from kazma_core.paths import memory_ops_db as _ops_db

                out: list[str] = []
                _cconn = _sqlite3.connect(_ops_db(), check_same_thread=False)
                try:
                    _rows = _cconn.execute(
                        "SELECT policy_decision, COUNT(*) FROM commitments "
                        "WHERE policy_decision IS NOT NULL GROUP BY policy_decision"
                    ).fetchall()
                    out.append("# HELP kazma_commitment_decisions_total Commitment gate decisions by type")
                    out.append("# TYPE kazma_commitment_decisions_total counter")
                    for _dec, _cnt in _rows:
                        out.append(f'kazma_commitment_decisions_total{{decision="{_dec}"}} {_cnt}')

                    _pending = _cconn.execute(
                        "SELECT COUNT(*) FROM commitments "
                        "WHERE status IN ('draft','needs_clarify','needs_confirm','ready')"
                    ).fetchone()[0]
                    out.append("# HELP kazma_commitment_pending Current pending commitments")
                    out.append("# TYPE kazma_commitment_pending gauge")
                    out.append(f"kazma_commitment_pending {_pending}")
                finally:
                    _cconn.close()
                return out

            lines.extend(await _aio.to_thread(_commitment_metric_lines))
        except Exception as exc:
            logger.debug("Failed to append Commitment metrics: %s", exc)

        # ── Intent engine metrics (in-process counters) ────────────────
        try:
            from kazma_core.agent.intent.metrics import get_intent_counters

            counters = get_intent_counters()
            if counters:
                lines.append(
                    "# HELP kazma_intent_decisions_total Intent engine decisions by route and act"
                )
                lines.append("# TYPE kazma_intent_decisions_total counter")
                for (route, act), count in sorted(counters.items()):
                    lines.append(
                        f'kazma_intent_decisions_total{{route="{route}",act="{act}"}} {count}'
                    )
        except Exception as exc:
            logger.debug("Failed to append intent engine metrics: %s", exc)

        # ── In-process prometheus_client registry (kazma-core Counters) ──
        # Exposes counters that aren't DB-derivable — notably
        # kazma_commitment_terminal_total (PR3 loop-prevention signal) and the
        # long-task / llm / swarm / memory counters. No name overlap with the
        # hand-built lines above (those are DB-derived under different names).
        # No-op + a single unavailable gauge if prometheus_client is absent.
        try:
            from kazma_core.metrics import get_metrics_response as _core_metrics

            _body, _status, _ = _core_metrics()
            if _status == 200:
                _core_text = _body.decode("utf-8") if isinstance(_body, (bytes, bytearray)) else str(_body)
                if _core_text.strip():
                    lines.append("# kazma-core in-process prometheus registry")
                    lines.append(_core_text.rstrip())
        except Exception as exc:
            logger.debug("Failed to merge kazma-core prometheus registry: %s", exc)

        return PlainTextResponse(
            content="\n".join(lines) + "\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return router
