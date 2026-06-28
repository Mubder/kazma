"""Tests for the in-memory TraceStore and WebSocket broadcasting."""

from __future__ import annotations

import time

from kazma_core.tracing import TraceEntry, TraceStore, get_trace_store


class TestTraceEntry:
    def test_defaults(self) -> None:
        t = time.time()
        entry = TraceEntry(
            timestamp=t,
            trace_type="llm",
            label="gpt-4o",
            status="success",
            duration_ms=100.0,
        )
        assert entry.timestamp == t
        assert entry.trace_type == "llm"
        assert entry.label == "gpt-4o"
        assert entry.status == "success"
        assert entry.duration_ms == 100.0
        assert entry.tokens == 0
        assert entry.cost == 0.0
        assert entry.details == ""

    def test_full_constructor(self) -> None:
        entry = TraceEntry(
            timestamp=1234.0,
            trace_type="tool",
            label="web_search",
            status="success",
            duration_ms=500.0,
            tokens=0,
            cost=0.0,
            details="search results",
        )
        assert entry.trace_type == "tool"
        assert entry.details == "search results"


class TestTraceStore:
    def test_empty_store(self) -> None:
        store = TraceStore()
        assert store.recent() == []
        stats = store.stats()
        assert stats["total_cost"] == 0.0
        assert stats["total_tokens"] == 0
        assert stats["total_llm_calls"] == 0
        assert stats["total_tool_calls"] == 0
        assert stats["total_traces"] == 0

    def test_add_llm_trace(self) -> None:
        store = TraceStore()
        store.add(
            TraceEntry(
                timestamp=time.time(),
                trace_type="llm",
                label="gpt-4o-mini",
                status="success",
                duration_ms=1200.0,
                tokens=150,
                cost=0.0015,
            )
        )
        stats = store.stats()
        assert stats["total_cost"] == 0.0015
        assert stats["total_tokens"] == 150
        assert stats["total_llm_calls"] == 1
        assert stats["total_tool_calls"] == 0
        assert stats["total_traces"] == 1

    def test_add_tool_trace(self) -> None:
        store = TraceStore()
        store.add(
            TraceEntry(
                timestamp=time.time(),
                trace_type="tool",
                label="web_search",
                status="success",
                duration_ms=800.0,
            )
        )
        stats = store.stats()
        assert stats["total_tool_calls"] == 1
        assert stats["total_traces"] == 1

    def test_recent_returns_newest_first(self) -> None:
        store = TraceStore(max_entries=100)
        store.add(TraceEntry(timestamp=1.0, trace_type="llm", label="first", status="success", duration_ms=0))
        store.add(TraceEntry(timestamp=2.0, trace_type="llm", label="second", status="success", duration_ms=0))
        store.add(TraceEntry(timestamp=3.0, trace_type="llm", label="third", status="success", duration_ms=0))
        recent = store.recent(2)
        assert len(recent) == 2
        # deque is oldest-first, so [-2:] returns [second, third]
        assert recent[0].label == "second"
        assert recent[1].label == "third"

    def test_max_entries(self) -> None:
        store = TraceStore(max_entries=5)
        for i in range(10):
            store.add(
                TraceEntry(
                    timestamp=float(i),
                    trace_type="llm",
                    label=f"trace_{i}",
                    status="success",
                    duration_ms=0,
                )
            )
        assert len(store.recent()) == 5
        # The last 5 entries should be trace_5 through trace_9
        labels = [e.label for e in store.recent()]
        assert "trace_0" not in labels
        assert "trace_9" in labels

    def test_mixed_types(self) -> None:
        store = TraceStore()
        store.add(
            TraceEntry(timestamp=time.time(), trace_type="llm", label="gpt-4o", status="success", duration_ms=100)
        )
        store.add(
            TraceEntry(timestamp=time.time(), trace_type="tool", label="file_read", status="success", duration_ms=50)
        )
        store.add(
            TraceEntry(
                timestamp=time.time(), trace_type="state", label="idle → thinking", status="success", duration_ms=0
            )
        )
        store.add(
            TraceEntry(
                timestamp=time.time(), trace_type="compaction", label="1000 → 500", status="success", duration_ms=0
            )
        )
        stats = store.stats()
        assert stats["total_llm_calls"] == 1
        assert stats["total_tool_calls"] == 1
        assert stats["total_traces"] == 4

    def test_ws_register_unregister(self) -> None:
        store = TraceStore()
        assert len(store._ws_clients) == 0
        fake_ws = object()
        store.register_ws(fake_ws)
        assert len(store._ws_clients) == 1
        store.unregister_ws(fake_ws)
        assert len(store._ws_clients) == 0

    def test_global_trace_store_singleton(self) -> None:
        store1 = get_trace_store()
        store2 = get_trace_store()
        assert store1 is store2


class TestTraceStoreWithStats:
    def test_uptime_increases(self) -> None:
        store = TraceStore()
        stats1 = store.stats()
        time.sleep(0.01)
        stats2 = store.stats()
        assert stats2["uptime_seconds"] > stats1["uptime_seconds"]
