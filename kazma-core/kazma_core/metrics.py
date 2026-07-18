"""Observability and Metrics for Kazma.

Provides optional Prometheus metrics endpoint and structured logging utilities.
Designed to be lightweight and not break if prometheus-client is unavailable.
"""

from __future__ import annotations

import logging
import time
from typing import Any

__all__ = ["get_metrics_response", "record_llm_call", "record_memory_op", "record_swarm_dispatch", "record_swarm_handoff"]

logger = logging.getLogger(__name__)

# Try to import prometheus_client, gracefully degrade if unavailable
try:
    from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    logger.debug("[Metrics] prometheus_client not installed — metrics disabled")

# ── Metric Definitions (only created if prometheus-client available) ─────

if _PROMETHEUS_AVAILABLE:
    # LLM call metrics
    LLM_CALLS_TOTAL = Counter(
        "kazma_llm_calls_total",
        "Total number of LLM API calls",
        ["provider", "model", "status"],
    )
    LLM_TOKENS_TOTAL = Counter(
        "kazma_llm_tokens_total",
        "Total tokens processed",
        ["provider", "type"],  # type = input|output
    )

    # Swarm dispatch metrics
    SWARM_DISPATCHES_TOTAL = Counter(
        "kazma_swarm_dispatches_total",
        "Total swarm dispatch operations",
        ["pattern", "status"],
    )
    SWARM_HANDOFFS_TOTAL = Counter(
        "kazma_swarm_handoffs_total",
        "Total swarm handoff operations",
    )

    # Memory metrics
    MEMORY_OPERATIONS_TOTAL = Counter(
        "kazma_memory_operations_total",
        "Total memory operations",
        ["operation", "layer"],  # operation = search|store|delete
    )

    # Latency histograms
    LLM_LATENCY_SECONDS = Histogram(
        "kazma_llm_latency_seconds",
        "LLM call latency distribution",
    )
else:
    # Stub objects for type checking
    LLM_CALLS_TOTAL = None
    LLM_TOKENS_TOTAL = None
    SWARM_DISPATCHES_TOTAL = None
    SWARM_HANDOFFS_TOTAL = None
    MEMORY_OPERATIONS_TOTAL = None
    LLM_LATENCY_SECONDS = None


# ── Metrics Endpoint ───────────────────────────────────────────────────


def get_metrics_response() -> tuple[bytes, int, dict[str, str]]:
    """Return Prometheus metrics in text format.

    Returns:
        Tuple of (body, status_code, headers) for FastAPI Response.
    """
    if not _PROMETHEUS_AVAILABLE:
        return (b'# HELP kazma_prometheus_unavailable prometheus_client not installed\n# TYPE kazma_prometheus_unavailable gauge\nkazma_prometheus_unavailable 1\n', 200, {"content-type": "text/plain"})

    return (generate_latest(), 200, {"content-type": CONTENT_TYPE_LATEST})


# ── Helper Functions ────────────────────────────────────────────────────


def record_llm_call(provider: str, model: str, status: str, tokens_in: int = 0, tokens_out: int = 0, duration_ms: float = 0.0) -> None:
    """Record LLM call metrics. No-op if prometheus-client unavailable."""
    if not _PROMETHEUS_AVAILABLE:
        return
    LLM_CALLS_TOTAL.labels(provider=provider, model=model, status=status).inc()
    if tokens_in:
        LLM_TOKENS_TOTAL.labels(provider=provider, type="input").inc(tokens_in)
    if tokens_out:
        LLM_TOKENS_TOTAL.labels(provider=provider, type="output").inc(tokens_out)
    if duration_ms:
        LLM_LATENCY_SECONDS.observe(duration_ms / 1000.0)


def record_swarm_dispatch(pattern: str, status: str) -> None:
    """Record swarm dispatch metrics."""
    if not _PROMETHEUS_AVAILABLE:
        return
    SWARM_DISPATCHES_TOTAL.labels(pattern=pattern, status=status).inc()


def record_swarm_handoff() -> None:
    """Record swarm handoff metrics."""
    if not _PROMETHEUS_AVAILABLE:
        return
    SWARM_HANDOFFS_TOTAL.inc()


def record_memory_op(operation: str, layer: str = "unknown") -> None:
    """Record memory operation metrics."""
    if not _PROMETHEUS_AVAILABLE:
        return
    MEMORY_OPERATIONS_TOTAL.labels(operation=operation, layer=layer).inc()