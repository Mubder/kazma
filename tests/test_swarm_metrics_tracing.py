"""Tests for MetricsCollector and TracingEmitter.

Covers:
- MetricsCollector: record, get_worker_metrics, get_all_metrics, get_task_totals
- TracingEmitter: span lifecycle, all span factories, exporter, tree, handoff chain
- Integration: SwarmEngine emits spans and records metrics on dispatch
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_core.swarm.metrics import MetricsCollector, WorkerMetricSnapshot
from kazma_core.swarm.task import WorkerResult
from kazma_core.swarm.tracing import InMemorySpanExporter, Span, TracingEmitter

# ---------------------------------------------------------------------------
# MetricsCollector tests
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    """Unit tests for MetricsCollector."""

    def test_record_single_success(self) -> None:
        collector = MetricsCollector()
        collector.record(
            worker="analyst", tokens=100, cost=0.005, duration=1.2, success=True
        )
        snap = collector.get_worker_metrics("analyst")
        assert snap is not None
        assert snap.worker == "analyst"
        assert snap.tasks_completed == 1
        assert snap.tasks_failed == 0
        assert snap.total_tokens == 100
        assert abs(snap.total_cost - 0.005) < 1e-9
        assert abs(snap.avg_latency - 1.2) < 1e-4

    def test_record_single_failure(self) -> None:
        collector = MetricsCollector()
        collector.record(
            worker="coder", tokens=50, cost=0.002, duration=0.5, success=False
        )
        snap = collector.get_worker_metrics("coder")
        assert snap is not None
        assert snap.tasks_completed == 0
        assert snap.tasks_failed == 1

    def test_record_multiple_accumulates(self) -> None:
        collector = MetricsCollector()
        collector.record(worker="a", tokens=100, cost=0.01, duration=1.0, success=True)
        collector.record(worker="a", tokens=200, cost=0.02, duration=2.0, success=True)
        snap = collector.get_worker_metrics("a")
        assert snap is not None
        assert snap.tasks_completed == 2
        assert snap.total_tokens == 300
        assert abs(snap.total_cost - 0.03) < 1e-9
        # Weighted average: (1.0*1 + 2.0*1) / 2 = 1.5
        assert abs(snap.avg_latency - 1.5) < 1e-4

    def test_record_different_workers_isolated(self) -> None:
        collector = MetricsCollector()
        collector.record(worker="x", tokens=100, cost=0.01, duration=1.0, success=True)
        collector.record(worker="y", tokens=200, cost=0.02, duration=2.0, success=False)
        snap_x = collector.get_worker_metrics("x")
        snap_y = collector.get_worker_metrics("y")
        assert snap_x is not None and snap_y is not None
        assert snap_x.total_tokens == 100
        assert snap_y.total_tokens == 200
        assert snap_x.tasks_completed == 1
        assert snap_y.tasks_failed == 1

    def test_record_different_dates_isolated(self) -> None:
        collector = MetricsCollector()
        collector.record(
            worker="a", tokens=100, cost=0.01, duration=1.0, success=True, date="2026-01-01"
        )
        collector.record(
            worker="a", tokens=200, cost=0.02, duration=2.0, success=True, date="2026-01-02"
        )
        snap1 = collector.get_worker_metrics("a", date="2026-01-01")
        snap2 = collector.get_worker_metrics("a", date="2026-01-02")
        assert snap1 is not None and snap2 is not None
        assert snap1.total_tokens == 100
        assert snap2.total_tokens == 200

    def test_get_worker_metrics_missing_returns_none(self) -> None:
        collector = MetricsCollector()
        assert collector.get_worker_metrics("nonexistent") is None

    def test_get_worker_aggregate_in_memory(self) -> None:
        collector = MetricsCollector()
        collector.record(
            worker="a", tokens=100, cost=0.01, duration=1.0, success=True, date="2026-01-01"
        )
        collector.record(
            worker="a", tokens=200, cost=0.02, duration=2.0, success=False, date="2026-01-02"
        )
        agg = collector.get_worker_aggregate("a")
        assert agg["worker"] == "a"
        assert agg["tasks_completed"] == 1
        assert agg["tasks_failed"] == 1
        assert agg["total_tokens"] == 300

    def test_get_all_metrics_returns_sorted(self) -> None:
        collector = MetricsCollector()
        collector.record(worker="b", tokens=10, cost=0.001, duration=0.5, success=True)
        collector.record(worker="a", tokens=20, cost=0.002, duration=0.6, success=True)
        all_metrics = collector.get_all_metrics()
        assert len(all_metrics) == 2
        assert all_metrics[0]["worker"] == "a"
        assert all_metrics[1]["worker"] == "b"

    def test_get_task_totals(self) -> None:
        collector = MetricsCollector()

        @dataclass
        class FakeResult:
            tokens_used: int = 0
            cost: float = 0.0
            duration_seconds: float = 0.0

        results = [
            FakeResult(tokens_used=100, cost=0.01, duration_seconds=1.0),
            FakeResult(tokens_used=200, cost=0.02, duration_seconds=2.0),
        ]
        totals = collector.get_task_totals(results)
        assert totals["total_tokens"] == 300
        assert abs(totals["total_cost"] - 0.03) < 1e-9
        assert abs(totals["duration_seconds"] - 3.0) < 1e-4

    def test_record_worker_result(self) -> None:
        collector = MetricsCollector()
        wr = WorkerResult(
            worker="test-worker",
            task_id="task-1",
            status="success",
            output="hello",
            tokens_used=150,
            cost=0.003,
            duration_seconds=2.5,
        )
        collector.record_worker_result(wr)
        snap = collector.get_worker_metrics("test-worker")
        assert snap is not None
        assert snap.tasks_completed == 1
        assert snap.total_tokens == 150

    def test_reset_clears_in_memory(self) -> None:
        collector = MetricsCollector()
        collector.record(worker="a", tokens=100, cost=0.01, duration=1.0, success=True)
        collector.reset()
        assert collector.get_worker_metrics("a") is None
        assert collector.get_all_metrics() == []

    def test_record_flushes_to_task_store(self) -> None:
        mock_store = MagicMock()
        collector = MetricsCollector(task_store=mock_store)
        collector.record(
            worker="a", tokens=100, cost=0.01, duration=1.0, success=True, date="2026-06-29"
        )
        mock_store.record_worker_metric.assert_called_once_with(
            worker="a",
            tasks_completed=1,
            tasks_failed=0,
            latency=1.0,
            tokens=100,
            cost=0.01,
            date="2026-06-29",
        )

    def test_worker_metric_snapshot_to_dict(self) -> None:
        snap = WorkerMetricSnapshot(
            worker="w", date="2026-01-01", tasks_completed=3, total_tokens=500, total_cost=0.05
        )
        d = snap.to_dict()
        assert d["worker"] == "w"
        assert d["tasks_completed"] == 3
        assert d["total_tokens"] == 500

    def test_get_worker_aggregate_uses_task_store(self) -> None:
        mock_store = MagicMock()
        mock_store.get_worker_metrics.return_value = [
            {"tasks_completed": 3, "tasks_failed": 1, "avg_latency": 1.5, "total_tokens": 500, "total_cost": 0.05},
            {"tasks_completed": 2, "tasks_failed": 0, "avg_latency": 2.0, "total_tokens": 300, "total_cost": 0.03},
        ]
        collector = MetricsCollector(task_store=mock_store)
        agg = collector.get_worker_aggregate("w")
        assert agg["tasks_completed"] == 5
        assert agg["tasks_failed"] == 1
        assert agg["total_tokens"] == 800


# ---------------------------------------------------------------------------
# InMemorySpanExporter tests
# ---------------------------------------------------------------------------


class TestInMemorySpanExporter:
    """Unit tests for InMemorySpanExporter."""

    def test_export_and_get_finished(self) -> None:
        exporter = InMemorySpanExporter()
        span = Span(trace_id="t1", name="test.span")
        span.end()
        exporter.export(span)
        assert len(exporter) == 1
        assert exporter.get_finished_spans()[0].name == "test.span"

    def test_get_spans_by_trace(self) -> None:
        exporter = InMemorySpanExporter()
        s1 = Span(trace_id="t1", name="a")
        s2 = Span(trace_id="t2", name="b")
        s1.end()
        s2.end()
        exporter.export(s1)
        exporter.export(s2)
        assert len(exporter.get_spans_by_trace("t1")) == 1

    def test_get_spans_by_name(self) -> None:
        exporter = InMemorySpanExporter()
        s1 = Span(trace_id="t1", name="swarm.task.x")
        s2 = Span(trace_id="t1", name="swarm.dispatch.y")
        s1.end()
        s2.end()
        exporter.export(s1)
        exporter.export(s2)
        assert len(exporter.get_spans_by_name("swarm.task.x")) == 1

    def test_clear(self) -> None:
        exporter = InMemorySpanExporter()
        s = Span(trace_id="t1", name="x")
        s.end()
        exporter.export(s)
        exporter.clear()
        assert len(exporter) == 0


# ---------------------------------------------------------------------------
# Span tests
# ---------------------------------------------------------------------------


class TestSpan:
    """Unit tests for the Span dataclass."""

    def test_span_defaults(self) -> None:
        span = Span(trace_id="trace-123")
        assert span.trace_id == "trace-123"
        assert span.status == "unset"
        assert span.is_ended is False
        assert span.end_time is None

    def test_span_set_attribute(self) -> None:
        span = Span(trace_id="t1")
        span.set_attribute("key", "value")
        assert span.attributes["key"] == "value"

    def test_span_set_status(self) -> None:
        span = Span(trace_id="t1")
        span.set_status("error", "something broke")
        assert span.status == "error"
        assert span.status_msg == "something broke"

    def test_span_end(self) -> None:
        span = Span(trace_id="t1")
        span.end()
        assert span.is_ended is True
        assert span.end_time is not None

    def test_span_to_dict(self) -> None:
        span = Span(trace_id="t1", name="test")
        span.set_attribute("k", "v")
        span.end()
        d = span.to_dict()
        assert d["trace_id"] == "t1"
        assert d["name"] == "test"
        assert d["attributes"]["k"] == "v"
        assert d["status"] == "unset"

    def test_span_duration_ms(self) -> None:
        span = Span(trace_id="t1")
        import time
        time.sleep(0.01)
        span.end()
        dur = span.duration_ms()
        assert dur is not None
        assert dur >= 0


# ---------------------------------------------------------------------------
# TracingEmitter tests
# ---------------------------------------------------------------------------


class TestTracingEmitter:
    """Unit tests for TracingEmitter."""

    def test_start_and_end_span(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        span = emitter.start_span("test.op", trace_id="trace-1")
        assert span.name == "test.op"
        assert span.trace_id == "trace-1"
        assert not span.is_ended
        emitter.end_span(span, status="ok")
        assert span.is_ended
        assert len(exporter) == 1

    def test_auto_parenting(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_span("root", trace_id="t1")
        child = emitter.start_span("child", trace_id="t1")
        assert child.parent_id == root.span_id
        emitter.end_span(child)
        emitter.end_span(root)

    def test_no_parent_when_first_span(self) -> None:
        emitter = TracingEmitter()
        span = emitter.start_span("first", trace_id="new-trace")
        assert span.parent_id is None
        emitter.end_span(span)

    def test_start_task_span_creates_root(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        span = emitter.start_task_span("task-abc", task_type="dispatch", workers=["w1"])
        assert span.name == "swarm.task.task-abc"
        assert span.parent_id is None
        assert span.attributes["swarm.task.type"] == "dispatch"
        assert span.attributes["swarm.task.workers"] == "w1"
        emitter.end_span(span)

    def test_start_dispatch_span(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        dispatch = emitter.start_dispatch_span(root.trace_id, "analyst", task_id="task-1")
        assert dispatch.name == "swarm.dispatch.analyst"
        assert dispatch.parent_id == root.span_id
        emitter.end_span(dispatch)
        emitter.end_span(root)

    def test_start_llm_span(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        dispatch = emitter.start_dispatch_span(root.trace_id, "worker")
        llm = emitter.start_llm_span(root.trace_id, "gpt-4o", parent_id=dispatch.span_id)
        assert llm.name == "llm.call.gpt-4o"
        assert llm.parent_id == dispatch.span_id
        assert llm.attributes["llm.model"] == "gpt-4o"
        emitter.end_span(llm)
        emitter.end_span(dispatch)
        emitter.end_span(root)

    def test_start_tool_span(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        tool = emitter.start_tool_span(root.trace_id, "web_search")
        assert tool.name == "tool.execute.web_search"
        assert tool.attributes["tool.name"] == "web_search"
        emitter.end_span(tool)
        emitter.end_span(root)

    def test_start_aggregate_span(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        agg = emitter.start_aggregate_span(root.trace_id, "vote")
        assert agg.name == "swarm.aggregate.vote"
        assert agg.attributes["swarm.aggregation.strategy"] == "vote"
        emitter.end_span(agg)
        emitter.end_span(root)

    def test_start_synthesize_span(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        synth = emitter.start_synthesize_span(root.trace_id)
        assert synth.name == "swarm.synthesize"
        emitter.end_span(synth)
        emitter.end_span(root)

    def test_start_handoff_span(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        handoff = emitter.start_handoff_span(root.trace_id, "worker-a", "worker-b")
        assert handoff.name == "swarm.handoff.worker-a->worker-b"
        assert handoff.attributes["swarm.handoff.from"] == "worker-a"
        assert handoff.attributes["swarm.handoff.to"] == "worker-b"
        emitter.end_span(handoff)
        emitter.end_span(root)

    def test_record_exception(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        span = emitter.start_span("op", trace_id="t1")
        try:
            raise ValueError("test error")
        except ValueError as exc:
            emitter.record_exception(span, exc)
        emitter.end_span(span, status="error")
        exported = exporter.get_finished_spans()[0]
        assert exported.attributes["exception.type"] == "ValueError"
        assert exported.attributes["exception.message"] == "test error"
        assert exported.status == "error"

    def test_get_trace_spans(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        child = emitter.start_dispatch_span(root.trace_id, "w1")
        emitter.end_span(child)
        emitter.end_span(root)
        spans = emitter.get_trace_spans(root.trace_id)
        assert len(spans) == 2

    def test_get_active_span(self) -> None:
        emitter = TracingEmitter()
        root = emitter.start_span("root", trace_id="t1")
        assert emitter.get_active_span("t1") == root
        emitter.end_span(root)
        assert emitter.get_active_span("t1") is None

    def test_build_span_tree(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        dispatch = emitter.start_dispatch_span(root.trace_id, "w1")
        llm = emitter.start_llm_span(root.trace_id, "gpt-4o", parent_id=dispatch.span_id)
        emitter.end_span(llm)
        emitter.end_span(dispatch)
        emitter.end_span(root)
        tree = emitter.build_span_tree(root.trace_id)
        assert len(tree) == 1  # one root
        assert tree[0]["name"] == "swarm.task.task-1"
        assert len(tree[0]["children"]) == 1  # one dispatch child
        assert len(tree[0]["children"][0]["children"]) == 1  # one llm child

    def test_reconstruct_handoff_chain(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        root = emitter.start_task_span("task-1")
        h1 = emitter.start_handoff_span(root.trace_id, "a", "b")
        emitter.end_span(h1)
        h2 = emitter.start_handoff_span(root.trace_id, "b", "c")
        emitter.end_span(h2)
        emitter.end_span(root)
        chain = emitter.reconstruct_handoff_chain(root.trace_id)
        assert len(chain) == 2
        assert chain[0]["from"] == "a"
        assert chain[0]["to"] == "b"
        assert chain[1]["from"] == "b"
        assert chain[1]["to"] == "c"

    def test_reset_clears_state(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        span = emitter.start_span("op", trace_id="t1")
        emitter.end_span(span)
        emitter.reset()
        assert len(exporter) == 0
        assert emitter.get_active_span("t1") is None

    def test_end_span_twice_is_noop(self) -> None:
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)
        span = emitter.start_span("op", trace_id="t1")
        emitter.end_span(span)
        emitter.end_span(span)  # second end should be a no-op
        assert len(exporter) == 1

    def test_full_trace_hierarchy(self) -> None:
        """Simulate a full task trace: root -> dispatch -> llm + tool, aggregate, handoff."""
        exporter = InMemorySpanExporter()
        emitter = TracingEmitter(exporter=exporter)

        # Root task span.
        root = emitter.start_task_span(
            "task-xyz", task_type="fan_out", workers=["a", "b"]
        )

        # Dispatch span for worker a.
        dispatch_a = emitter.start_dispatch_span(root.trace_id, "a", task_id="task-xyz")
        llm_a = emitter.start_llm_span(root.trace_id, "gpt-4o", parent_id=dispatch_a.span_id)
        emitter.end_span(llm_a, status="ok")
        tool_a = emitter.start_tool_span(root.trace_id, "code_exec", parent_id=dispatch_a.span_id)
        emitter.end_span(tool_a, status="ok")
        emitter.end_span(dispatch_a, status="ok")

        # Dispatch span for worker b.
        dispatch_b = emitter.start_dispatch_span(root.trace_id, "b", task_id="task-xyz")
        emitter.end_span(dispatch_b, status="ok")

        # Aggregate span.
        agg = emitter.start_aggregate_span(root.trace_id, "vote")
        emitter.end_span(agg, status="ok")

        # Synthesize span.
        synth = emitter.start_synthesize_span(root.trace_id)
        emitter.end_span(synth, status="ok")

        # Handoff span.
        handoff = emitter.start_handoff_span(root.trace_id, "a", "b")
        emitter.end_span(handoff, status="ok")

        # End root.
        emitter.end_span(root, status="ok")

        # Verify all spans exported.
        all_spans = exporter.get_spans_by_trace(root.trace_id)
        assert len(all_spans) == 8

        # Verify hierarchy.
        tree = emitter.build_span_tree(root.trace_id)
        assert len(tree) == 1
        root_node = tree[0]
        assert root_node["name"] == "swarm.task.task-xyz"
        # Root has: dispatch_a, dispatch_b, aggregate, synthesize, handoff as children.
        child_names = {c["name"] for c in root_node["children"]}
        assert "swarm.dispatch.a" in child_names
        assert "swarm.dispatch.b" in child_names
        assert "swarm.aggregate.vote" in child_names
        assert "swarm.synthesize" in child_names
        assert "swarm.handoff.a->b" in child_names

        # Verify dispatch_a has llm and tool children.
        dispatch_a_node = next(
            c for c in root_node["children"] if c["name"] == "swarm.dispatch.a"
        )
        sub_child_names = {c["name"] for c in dispatch_a_node["children"]}
        assert "llm.call.gpt-4o" in sub_child_names
        assert "tool.execute.code_exec" in sub_child_names


# ---------------------------------------------------------------------------
# Integration tests: SwarmEngine with metrics and tracing
# ---------------------------------------------------------------------------


class TestSwarmEngineMetricsTracingIntegration:
    """Integration tests verifying SwarmEngine emits spans and records metrics."""

    @pytest.fixture
    def engine_components(self) -> tuple:
        """Create an engine with mock workers and instrumented metrics/tracing."""
        from kazma_core.swarm.config import SwarmConfig, WorkerConfig
        from kazma_core.swarm.engine import SwarmEngine
        from kazma_core.swarm.metrics import MetricsCollector
        from kazma_core.swarm.tracing import InMemorySpanExporter, TracingEmitter

        exporter = InMemorySpanExporter()
        tracing = TracingEmitter(exporter=exporter)
        metrics = MetricsCollector()

        config = SwarmConfig(
            enabled=True,
            workers=[
                WorkerConfig(name="worker-a", type="in_process", model="test"),
                WorkerConfig(name="worker-b", type="in_process", model="test"),
            ],
        )
        engine = SwarmEngine(
            config=config,
            metrics_collector=metrics,
            tracing_emitter=tracing,
        )

        # Mock the workers to return predictable results.
        for name, worker in engine._workers.items():
            worker.dispatch = AsyncMock(
                return_value={
                    "worker": name,
                    "task_id": f"task-{name}",
                    "status": "success",
                    "output": f"Output from {name}",
                    "error": None,
                }
            )

        return engine, metrics, exporter, tracing

    @pytest.mark.asyncio
    async def test_dispatch_emits_task_span(
        self, engine_components: tuple
    ) -> None:
        engine, metrics, exporter, tracing = engine_components
        from kazma_core.swarm.task import SwarmTask, TaskType

        task = SwarmTask(
            prompt="test task",
            workers=["worker-a"],
            type=TaskType.DISPATCH,
        )
        result = await engine.dispatch(task)

        task_spans = exporter.get_spans_by_name(f"swarm.task.{task.id}")
        assert len(task_spans) == 1
        assert task_spans[0].status == "ok"

    @pytest.mark.asyncio
    async def test_dispatch_emits_dispatch_span(
        self, engine_components: tuple
    ) -> None:
        engine, metrics, exporter, tracing = engine_components
        from kazma_core.swarm.task import SwarmTask, TaskType

        task = SwarmTask(
            prompt="test task",
            workers=["worker-a"],
            type=TaskType.DISPATCH,
        )
        result = await engine.dispatch(task)

        dispatch_spans = exporter.get_spans_by_name("swarm.dispatch.worker-a")
        assert len(dispatch_spans) == 1

    @pytest.mark.asyncio
    async def test_dispatch_records_metrics(
        self, engine_components: tuple
    ) -> None:
        engine, metrics, exporter, tracing = engine_components
        from kazma_core.swarm.task import SwarmTask, TaskType

        task = SwarmTask(
            prompt="test task",
            workers=["worker-a"],
            type=TaskType.DISPATCH,
        )
        result = await engine.dispatch(task)

        # The mock worker returns tokens_used=0, cost=0 by default.
        snap = metrics.get_worker_metrics("worker-a")
        assert snap is not None
        assert snap.tasks_completed == 1

    @pytest.mark.asyncio
    async def test_task_result_totals_from_metrics(
        self, engine_components: tuple
    ) -> None:
        engine, metrics, exporter, tracing = engine_components
        from kazma_core.swarm.task import SwarmTask, TaskType

        # Mock worker to return specific tokens/cost.
        engine._workers["worker-a"].dispatch = AsyncMock(
            return_value={
                "worker": "worker-a",
                "task_id": "t1",
                "status": "success",
                "output": "result",
                "error": None,
            }
        )

        task = SwarmTask(
            prompt="test",
            workers=["worker-a"],
            type=TaskType.DISPATCH,
        )
        result = await engine.dispatch(task)

        # TaskResult should have totals.
        assert result.total_tokens >= 0
        assert result.total_cost >= 0.0
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_broadcast_emits_task_span(
        self, engine_components: tuple
    ) -> None:
        engine, metrics, exporter, tracing = engine_components
        from kazma_core.swarm.task import SwarmTask, TaskType

        task = SwarmTask(
            prompt="broadcast test",
            type=TaskType.BROADCAST,
        )
        result = await engine.broadcast(task)

        task_spans = exporter.get_spans_by_name(f"swarm.task.{task.id}")
        assert len(task_spans) == 1

    @pytest.mark.asyncio
    async def test_engine_has_metrics_collector_property(
        self, engine_components: tuple
    ) -> None:
        engine, metrics, _, _ = engine_components
        assert engine.metrics_collector is metrics

    @pytest.mark.asyncio
    async def test_engine_has_tracing_emitter_property(
        self, engine_components: tuple
    ) -> None:
        engine, _, _, tracing = engine_components
        assert engine.tracing_emitter is tracing

    @pytest.mark.asyncio
    async def test_failed_dispatch_emits_error_span(
        self, engine_components: tuple
    ) -> None:
        engine, _, exporter, _ = engine_components

        # Mock worker to fail.
        engine._workers["worker-a"].dispatch = AsyncMock(
            side_effect=RuntimeError("boom")
        )

        from kazma_core.swarm.task import SwarmTask, TaskType

        task = SwarmTask(
            prompt="failing task",
            workers=["worker-a"],
            type=TaskType.DISPATCH,
        )
        result = await engine.dispatch(task)

        task_spans = exporter.get_spans_by_name(f"swarm.task.{task.id}")
        assert len(task_spans) == 1
        assert task_spans[0].status == "error"
