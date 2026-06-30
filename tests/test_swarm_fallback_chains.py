"""Tests for FallbackChain in kazma_core.swarm.reliability.

Covers validation contract assertions:
  VAL-REL-034 through VAL-REL-040, VAL-ORCH-005, VAL-ORCH-050.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from kazma_core.swarm import (
    SwarmConfig,
    SwarmTask,
    TaskType,
    WorkerConfig,
)
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.reliability import (
    CircuitState,
    FallbackChain,
    RetryPolicy,
)
from kazma_core.swarm.task import WorkerResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok(worker: str, output: str = "output") -> dict[str, str | None]:
    return {
        "worker": worker,
        "task_id": f"{worker}-task",
        "status": "success",
        "output": output,
        "error": None,
    }


def _fail(worker: str, error: str = "boom") -> dict[str, str | None]:
    return {
        "worker": worker,
        "task_id": f"{worker}-task",
        "status": "error",
        "output": "",
        "error": error,
    }


def _worker_result(
    worker: str,
    *,
    status: str = "success",
    output: str = "",
    error: str | None = None,
) -> WorkerResult:
    return WorkerResult(
        worker=worker,
        task_id=f"{worker}-task",
        status=status,
        output=output,
        error=error,
    )


# ---------------------------------------------------------------------------
# FallbackChain unit tests
# ---------------------------------------------------------------------------


class TestFallbackChainEmptyChain:
    """VAL-REL-039: Empty fallback chain means no fallback."""

    @pytest.mark.asyncio
    async def test_empty_chain_returns_primary_result(self):
        chain = FallbackChain(fallback_workers=[])
        primary = _worker_result("primary", status="error", error="primary failed")

        async def dispatch(name: str) -> WorkerResult:
            return _worker_result(name, output=f"{name} output")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.worker == "primary"
        assert result.status == "error"
        assert result.error == "primary failed"

    @pytest.mark.asyncio
    async def test_empty_chain_on_success_returns_primary(self):
        chain = FallbackChain(fallback_workers=[])
        primary = _worker_result("primary", output="primary ok")

        async def dispatch(name: str) -> WorkerResult:
            return _worker_result(name, output=f"{name} output")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.worker == "primary"
        assert result.status == "success"
        assert result.output == "primary ok"


class TestFallbackChainPrimarySuccess:
    """Primary success means no fallback invoked."""

    @pytest.mark.asyncio
    async def test_primary_success_skips_fallbacks(self):
        chain = FallbackChain(fallback_workers=["fb1", "fb2"])
        primary = _worker_result("primary", output="primary ok")

        dispatch = AsyncMock(return_value=_worker_result("fb1"))

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.worker == "primary"
        assert result.status == "success"
        dispatch.assert_not_awaited()


class TestFallbackChainPrimaryFailureTriggersFirstFallback:
    """VAL-REL-034: Primary failure triggers first fallback."""

    @pytest.mark.asyncio
    async def test_primary_fail_invokes_first_fallback(self):
        chain = FallbackChain(fallback_workers=["fb1", "fb2"])
        primary = _worker_result("primary", status="error", error="primary failed")

        async def dispatch(name: str) -> WorkerResult:
            if name == "fb1":
                return _worker_result("fb1", output="fb1 saved the day")
            return _worker_result("fb2", output="fb2 output")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.worker == "fb1"
        assert result.status == "success"
        assert result.output == "fb1 saved the day"

    @pytest.mark.asyncio
    async def test_first_fallback_failure_triggers_second(self):
        chain = FallbackChain(fallback_workers=["fb1", "fb2"])
        primary = _worker_result("primary", status="error", error="primary failed")

        async def dispatch(name: str) -> WorkerResult:
            if name == "fb1":
                return _worker_result("fb1", status="error", error="fb1 also failed")
            return _worker_result("fb2", output="fb2 saved the day")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.worker == "fb2"
        assert result.status == "success"
        assert result.output == "fb2 saved the day"


class TestFallbackChainOrderRespected:
    """VAL-REL-035: Fallback order respected (sequential, not parallel)."""

    @pytest.mark.asyncio
    async def test_fallbacks_tried_in_order(self):
        chain = FallbackChain(fallback_workers=["fb1", "fb2", "fb3"])
        primary = _worker_result("primary", status="error", error="primary failed")
        invocation_order: list[str] = []

        async def dispatch(name: str) -> WorkerResult:
            invocation_order.append(name)
            if name == "fb2":
                return _worker_result("fb2", output="fb2 ok")
            return _worker_result(name, status="error", error=f"{name} failed")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert invocation_order == ["fb1", "fb2"]
        assert result.worker == "fb2"
        assert result.status == "success"

    @pytest.mark.asyncio
    async def test_fallbacks_are_sequential_not_parallel(self):
        """Ensure fallbacks execute one at a time, not concurrently."""
        chain = FallbackChain(fallback_workers=["fb1", "fb2"])
        primary = _worker_result("primary", status="error", error="primary failed")
        call_log: list[str] = []

        async def dispatch(name: str) -> WorkerResult:
            call_log.append(f"start-{name}")
            await asyncio.sleep(0.01)
            if name == "fb1":
                call_log.append(f"end-{name}")
                return _worker_result("fb1", status="error", error="fb1 failed")
            call_log.append(f"end-{name}")
            return _worker_result("fb2", output="fb2 ok")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        # fb1 should fully complete before fb2 starts
        assert call_log == ["start-fb1", "end-fb1", "start-fb2", "end-fb2"]


class TestFallbackChainAllFailTerminalFailure:
    """VAL-REL-036: All fallbacks failing results in terminal failure."""

    @pytest.mark.asyncio
    async def test_all_fail_returns_last_fallback_result(self):
        chain = FallbackChain(fallback_workers=["fb1", "fb2"])
        primary = _worker_result("primary", status="error", error="primary failed")

        async def dispatch(name: str) -> WorkerResult:
            return _worker_result(name, status="error", error=f"{name} failed")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.status == "error"
        assert result.worker == "fb2"
        assert "fb2 failed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_all_fail_includes_exhaustion_summary(self):
        chain = FallbackChain(fallback_workers=["fb1", "fb2", "fb3"])
        primary = _worker_result("primary", status="error", error="primary failed")

        async def dispatch(name: str) -> WorkerResult:
            return _worker_result(name, status="error", error=f"{name} failed")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.status == "error"
        # Error should mention fallback exhaustion
        assert "fallback" in (result.error or "").lower() or "exhaust" in (result.error or "").lower()


class TestFallbackChainOwnRetryAndCircuitBreaker:
    """VAL-REL-037: Each fallback uses its own retry policy and circuit breaker."""

    @pytest.mark.asyncio
    async def test_fallback_dispatched_through_engine_with_own_breaker(self):
        """When dispatching through the engine, each fallback worker gets
        its own circuit breaker and retry policy."""
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        for name in ("primary", "fb1", "fb2"):
            engine.add_worker(WorkerConfig(name=name, type="in_process"))

        call_counts: dict[str, int] = {}

        async def make_dispatch(name: str):
            async def dispatch(task: str, context: str = "") -> dict[str, str | None]:
                call_counts[name] = call_counts.get(name, 0) + 1
                if name == "primary":
                    return _fail("primary")
                if name == "fb1":
                    return _fail("fb1")
                return _ok("fb2", "fb2 saved it")

            return dispatch

        engine.get_worker("primary").dispatch = await make_dispatch("primary")  # type: ignore[assignment,union-attr]
        engine.get_worker("fb1").dispatch = await make_dispatch("fb1")  # type: ignore[assignment,union-attr]
        engine.get_worker("fb2").dispatch = await make_dispatch("fb2")  # type: ignore[assignment,union-attr]

        result = await engine.dispatch(
            SwarmTask(
                prompt="test fallback",
                workers=["primary"],
                fallback_chain=["fb1", "fb2"],
            )
        )

        assert result.status == "success"
        assert result.worker_results[-1].worker == "fb2"
        # All three workers should have been called
        assert call_counts.get("primary", 0) >= 1
        assert call_counts.get("fb1", 0) >= 1
        assert call_counts.get("fb2", 0) >= 1


class TestFallbackChainWorksInPipeline:
    """VAL-REL-038: Fallback works within pipeline pattern."""

    @pytest.mark.asyncio
    async def test_pipeline_step_failure_recovers_via_fallback(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        for name in ("step1", "step2", "step3", "fb_step2"):
            engine.add_worker(WorkerConfig(name=name, type="in_process"))

        engine.get_worker("step1").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("step1", "step1 output")
        )
        # step2 fails
        engine.get_worker("step2").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_fail("step2", "step2 crashed")
        )
        # fb_step2 succeeds
        engine.get_worker("fb_step2").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("fb_step2", "fb_step2 output")
        )
        engine.get_worker("step3").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("step3", "step3 output")
        )

        result = await engine.dispatch(
            SwarmTask(
                prompt="pipeline with fallback",
                workers=["step1", "step2", "step3"],
                type=TaskType.PIPELINE,
                fallback_chain=["fb_step2"],
            )
        )

        assert result.status == "success"
        assert result.aggregated_output == "step3 output"
        # step1, fb_step2 (fallback for step2), step3
        worker_names = [wr.worker for wr in result.worker_results]
        assert "step1" in worker_names
        assert "fb_step2" in worker_names
        assert "step3" in worker_names


class TestFallbackChainWorksInFanOut:
    """VAL-REL-038: Fallback works within fan_out pattern."""

    @pytest.mark.asyncio
    async def test_fan_out_worker_failure_recovers_via_fallback(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        for name in ("w1", "w2", "w3", "fb_w2"):
            engine.add_worker(WorkerConfig(name=name, type="in_process"))

        engine.get_worker("w1").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("w1", "w1 output")
        )
        engine.get_worker("w2").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_fail("w2", "w2 crashed")
        )
        engine.get_worker("fb_w2").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("fb_w2", "fb_w2 output")
        )
        engine.get_worker("w3").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("w3", "w3 output")
        )

        result = await engine.dispatch(
            SwarmTask(
                prompt="fan-out with fallback",
                workers=["w1", "w2", "w3"],
                type=TaskType.FAN_OUT,
                aggregation="merge_all",
                fallback_chain=["fb_w2"],
            )
        )

        assert result.status == "success"
        worker_names = [wr.worker for wr in result.worker_results]
        assert "fb_w2" in worker_names


class TestFallbackChainTracing:
    """VAL-REL-040: Fallback invocation recorded for tracing."""

    @pytest.mark.asyncio
    async def test_handoff_records_created_for_fallback(self):
        chain = FallbackChain(fallback_workers=["fb1"])
        primary = _worker_result("primary", status="error", error="primary failed")

        async def dispatch(name: str) -> WorkerResult:
            return _worker_result(name, output="fb1 ok")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.status == "success"
        assert len(result.handoffs) >= 1
        handoff = result.handoffs[0]
        assert handoff.from_worker == "primary"
        assert handoff.to_worker == "fb1"
        assert handoff.context_transferred  # non-empty

    @pytest.mark.asyncio
    async def test_multiple_fallback_handoffs_recorded(self):
        chain = FallbackChain(fallback_workers=["fb1", "fb2"])
        primary = _worker_result("primary", status="error", error="primary failed")

        async def dispatch(name: str) -> WorkerResult:
            if name == "fb1":
                return _worker_result("fb1", status="error", error="fb1 failed")
            return _worker_result("fb2", output="fb2 ok")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.status == "success"
        assert len(result.handoffs) >= 2
        assert result.handoffs[0].from_worker == "primary"
        assert result.handoffs[0].to_worker == "fb1"
        assert result.handoffs[1].from_worker == "fb1"
        assert result.handoffs[1].to_worker == "fb2"

    @pytest.mark.asyncio
    async def test_fallback_chain_error_summary_includes_all_workers(self):
        """All-fail case includes summary listing the full chain and errors."""
        chain = FallbackChain(fallback_workers=["fb1", "fb2"])
        primary = _worker_result("primary", status="error", error="primary boom")

        async def dispatch(name: str) -> WorkerResult:
            return _worker_result(name, status="error", error=f"{name} boom")

        result = await chain.execute(primary, dispatch_worker=dispatch)
        assert result.status == "error"
        error_msg = result.error or ""
        assert "primary" in error_msg
        assert "fb1" in error_msg
        assert "fb2" in error_msg
        assert "exhausted" in error_msg.lower()


class TestFallbackChainWithRetryInFallback:
    """Fallback worker that fails then succeeds on retry."""

    @pytest.mark.asyncio
    async def test_fallback_worker_retried_by_engine(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        engine.add_worker(WorkerConfig(name="primary", type="in_process"))
        engine.add_worker(WorkerConfig(name="fb1", type="in_process"))

        # Set retry policy for fb1
        engine.set_retry_policy("fb1", RetryPolicy(max_retries=2, base_delay=0.001, jitter=False))

        primary_attempts = 0
        fb1_attempts = 0

        async def primary_dispatch(task: str, context: str = "") -> dict[str, str | None]:
            nonlocal primary_attempts
            primary_attempts += 1
            return _fail("primary", "primary always fails")

        async def fb1_dispatch(task: str, context: str = "") -> dict[str, str | None]:
            nonlocal fb1_attempts
            fb1_attempts += 1
            if fb1_attempts < 2:
                return _fail("fb1", "fb1 transient error")
            return _ok("fb1", "fb1 recovered")

        engine.get_worker("primary").dispatch = primary_dispatch  # type: ignore[assignment,union-attr]
        engine.get_worker("fb1").dispatch = fb1_dispatch  # type: ignore[assignment,union-attr]

        result = await engine.dispatch(
            SwarmTask(
                prompt="test retry in fallback",
                workers=["primary"],
                fallback_chain=["fb1"],
            )
        )

        assert result.status == "success"
        assert fb1_attempts == 2  # first fail, then success on retry


class TestFallbackChainEngineIntegration:
    """End-to-end tests through the SwarmEngine."""

    @pytest.mark.asyncio
    async def test_dispatch_with_empty_fallback_chain_no_fallback(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        engine.add_worker(WorkerConfig(name="primary", type="in_process"))
        engine.get_worker("primary").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_fail("primary", "primary failed")
        )

        result = await engine.dispatch(
            SwarmTask(
                prompt="no fallback",
                workers=["primary"],
                fallback_chain=[],
            )
        )

        assert result.status == "failed"
        assert len(result.worker_results) == 1
        assert result.worker_results[0].worker == "primary"

    @pytest.mark.asyncio
    async def test_dispatch_primary_success_no_fallback_attempted(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        engine.add_worker(WorkerConfig(name="primary", type="in_process"))
        engine.add_worker(WorkerConfig(name="fb1", type="in_process"))

        engine.get_worker("primary").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("primary", "primary ok")
        )
        fb1_mock = AsyncMock(return_value=_ok("fb1", "fb1 ok"))
        engine.get_worker("fb1").dispatch = fb1_mock  # type: ignore[assignment,union-attr]

        result = await engine.dispatch(
            SwarmTask(
                prompt="primary succeeds",
                workers=["primary"],
                fallback_chain=["fb1"],
            )
        )

        assert result.status == "success"
        fb1_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_all_fallbacks_fail_terminal(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        for name in ("primary", "fb1", "fb2"):
            engine.add_worker(WorkerConfig(name=name, type="in_process"))
            engine.get_worker(name).dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
                return_value=_fail(name, f"{name} failed")
            )

        result = await engine.dispatch(
            SwarmTask(
                prompt="all fail",
                workers=["primary"],
                fallback_chain=["fb1", "fb2"],
            )
        )

        assert result.status == "failed"
        worker_names = [wr.worker for wr in result.worker_results]
        assert "primary" in worker_names
        assert "fb1" in worker_names
        assert "fb2" in worker_names
        # Error should mention all failed
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_fallback_circuit_breaker_prevents_dispatch_to_open_breaker(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        for name in ("primary", "fb1", "fb2"):
            engine.add_worker(WorkerConfig(name=name, type="in_process"))

        # Trip fb1's circuit breaker
        engine.set_circuit_breaker_config("fb1", failure_threshold=1)
        engine.get_circuit_breaker("fb1").record_failure()
        assert engine.get_circuit_breaker("fb1").state == CircuitState.OPEN

        engine.get_worker("primary").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_fail("primary", "primary failed")
        )
        fb1_mock = AsyncMock(return_value=_ok("fb1", "fb1 ok"))
        engine.get_worker("fb1").dispatch = fb1_mock  # type: ignore[assignment,union-attr]
        engine.get_worker("fb2").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("fb2", "fb2 ok")
        )

        result = await engine.dispatch(
            SwarmTask(
                prompt="breaker test",
                workers=["primary"],
                fallback_chain=["fb1", "fb2"],
            )
        )

        # fb1 should be skipped (circuit breaker open), fb2 should succeed
        assert result.status == "success"
        fb1_mock.assert_not_awaited()  # breaker open, not called
        worker_names = [wr.worker for wr in result.worker_results]
        assert "fb2" in worker_names

    @pytest.mark.asyncio
    async def test_handoff_records_in_final_result(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        for name in ("primary", "fb1"):
            engine.add_worker(WorkerConfig(name=name, type="in_process"))

        engine.get_worker("primary").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_fail("primary", "primary failed")
        )
        engine.get_worker("fb1").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_ok("fb1", "fb1 ok")
        )

        result = await engine.dispatch(
            SwarmTask(
                prompt="handoff test",
                workers=["primary"],
                fallback_chain=["fb1"],
            )
        )

        assert result.status == "success"
        # The fallback worker result should have handoff records
        fb1_result = next(
            (wr for wr in result.worker_results if wr.worker == "fb1"), None
        )
        assert fb1_result is not None
        assert len(fb1_result.handoffs) >= 1
        assert fb1_result.handoffs[0].from_worker == "primary"
        assert fb1_result.handoffs[0].to_worker == "fb1"


class TestFallbackChainNonexistentWorker:
    """Fallback to a nonexistent worker should produce an error result."""

    @pytest.mark.asyncio
    async def test_nonexistent_fallback_worker_returns_error(self):
        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        engine.add_worker(WorkerConfig(name="primary", type="in_process"))
        engine.get_worker("primary").dispatch = AsyncMock(  # type: ignore[assignment,union-attr]
            return_value=_fail("primary", "primary failed")
        )

        result = await engine.dispatch(
            SwarmTask(
                prompt="missing fallback",
                workers=["primary"],
                fallback_chain=["nonexistent"],
            )
        )

        assert result.status == "failed"
        # Error should mention the missing worker
        assert result.error is not None
        assert "nonexistent" in result.error.lower() or "not found" in result.error.lower()
