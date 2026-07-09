"""Unit tests for KazmaTracer console backend and TraceStore.add()."""

from __future__ import annotations

from kazma_core.config_schema import TracingConfig
from kazma_core.tracing import KazmaTracer, TraceEntry, TraceStore, get_trace_store


def test_console_backend_init():
    tracer = KazmaTracer(TracingConfig(enabled=True, backend="console"))
    assert tracer.backend.value == "console"
    assert tracer._client is None


def test_trace_store_add_and_recent():
    store = TraceStore(max_entries=10)
    store.add(
        TraceEntry(
            timestamp=1.0,
            trace_type="llm",
            label="chat",
            status="success",
            duration_ms=12.0,
            tokens=5,
            cost=0.001,
        )
    )
    store.add(
        TraceEntry(
            timestamp=2.0,
            trace_type="tool",
            label="web_search",
            status="success",
            duration_ms=3.0,
        )
    )
    recent = store.recent(limit=5)
    assert len(recent) == 2
    stats = store.stats()
    assert stats["total_llm_calls"] == 1
    assert stats["total_tool_calls"] == 1
    assert stats["total_tokens"] == 5


def test_get_trace_store_singleton():
    a = get_trace_store()
    b = get_trace_store()
    assert a is b
