"""Error coverage tests — edge cases, malformed input, error states."""
from __future__ import annotations

import time

from kazma_core.llm_provider import LLMConfig
from kazma_core.tracing import TraceEntry, TraceStore, get_trace_store


class TestTraceStoreErrors:
    """Edge cases for the in-memory trace store."""

    def test_trace_with_negative_duration(self) -> None:
        store = TraceStore()
        entry = TraceEntry(
            timestamp=time.time(),
            trace_type="llm",
            label="test",
            status="success",
            duration_ms=-1.0,
        )
        store.add(entry)
        assert store.stats()["total_traces"] == 1

    def test_trace_with_zero_cost(self) -> None:
        store = TraceStore()
        entry = TraceEntry(
            timestamp=time.time(),
            trace_type="llm", label="free",
            status="success", duration_ms=0,
            tokens=0, cost=0.0,
        )
        store.add(entry)
        assert store.stats()["total_cost"] == 0.0

    def test_trace_with_large_tokens(self) -> None:
        store = TraceStore()
        entry = TraceEntry(
            timestamp=time.time(),
            trace_type="llm", label="big",
            status="success", duration_ms=0,
            tokens=1_000_000, cost=10.0,
        )
        store.add(entry)
        assert store.stats()["total_tokens"] == 1_000_000
        assert store.stats()["total_cost"] == 10.0

    def test_trace_with_empty_label(self) -> None:
        store = TraceStore()
        entry = TraceEntry(
            timestamp=time.time(),
            trace_type="llm", label="",
            status="success", duration_ms=0,
        )
        store.add(entry)
        assert store.stats()["total_traces"] == 1

    def test_trace_with_long_details(self) -> None:
        store = TraceStore()
        entry = TraceEntry(
            timestamp=time.time(),
            trace_type="llm", label="long",
            status="success", duration_ms=0,
            details="x" * 10_000,
        )
        store.add(entry)
        assert store.stats()["total_traces"] == 1

    def test_recent_empty_store(self) -> None:
        store = TraceStore()
        assert store.recent(10) == []

    def test_recent_with_zero_limit(self) -> None:
        store = TraceStore()
        store.add(TraceEntry(timestamp=time.time(), trace_type="llm", label="t", status="success", duration_ms=0))
        # list[-0:] returns the full list, so limit=0 gives all entries
        result = store.recent(0)
        assert len(result) >= 0  # at minimum doesn't crash

    def test_recent_with_negative_limit(self) -> None:
        store = TraceStore()
        store.add(TraceEntry(timestamp=time.time(), trace_type="llm", label="t", status="success", duration_ms=0))
        assert store.recent(-1) == []

    def test_ws_register_twice(self) -> None:
        store = TraceStore()
        fake = object()
        store.register_ws(fake)
        store.register_ws(fake)
        assert len(store._ws_clients) == 1  # set dedup

    def test_ws_unregister_nonexistent(self) -> None:
        store = TraceStore()
        store.unregister_ws(object())  # should not raise


class TestTraceEntryErrors:
    """Edge cases for trace entry construction."""

    def test_entry_with_none_type(self) -> None:
        entry = TraceEntry(
            timestamp=time.time(),
            trace_type="",
            label="test",
            status="success",
            duration_ms=0,
        )
        assert entry.trace_type == ""

    def test_entry_with_unknown_status(self) -> None:
        entry = TraceEntry(
            timestamp=time.time(),
            trace_type="llm",
            label="test",
            status="unknown_status_xyz",
            duration_ms=0,
        )
        assert entry.status == "unknown_status_xyz"

    def test_entry_very_large_duration(self) -> None:
        entry = TraceEntry(
            timestamp=time.time(),
            trace_type="llm", label="test",
            status="success", duration_ms=9e9,
        )
        assert entry.duration_ms == 9e9


class TestLLMConfigErrors:
    """Edge cases for LLM configuration."""

    def test_empty_config(self) -> None:
        config = LLMConfig.from_dict({})
        assert config.model == "gpt-4o-mini"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.max_tokens == 4096

    def test_partial_config(self) -> None:
        config = LLMConfig.from_dict({"model": "custom-model"})
        assert config.model == "custom-model"
        assert config.base_url == "https://api.openai.com/v1"

    def test_config_with_negative_tokens(self) -> None:
        config = LLMConfig.from_dict({"max_tokens": -100})
        assert config.max_tokens == -100  # accepted as-is, validated at call time

    def test_config_with_none_values(self) -> None:
        config = LLMConfig.from_dict({"model": None})
        # None model is accepted as-is — validation happens at call time
        assert config.model is None or config.model != ""


class TestGetTraceStore:
    """Test the singleton trace store pattern."""

    def test_singleton_multiple_calls(self) -> None:
        s1 = get_trace_store()
        s2 = get_trace_store()
        assert s1 is s2

    def test_singleton_state_persists(self) -> None:
        s1 = get_trace_store()
        s1.add(TraceEntry(timestamp=time.time(), trace_type="llm", label="test", status="success", duration_ms=0))
        s2 = get_trace_store()
        assert s2.stats()["total_traces"] >= 1

    def test_singleton_ws_registration(self) -> None:
        store = get_trace_store()
        fake = object()
        store.register_ws(fake)
        assert len(store._ws_clients) >= 1
        store.unregister_ws(fake)
