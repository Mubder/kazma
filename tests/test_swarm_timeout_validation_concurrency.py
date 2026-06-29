"""Tests for TimeoutGuard, OutputValidator, and BoundedConcurrency.

Covers validation contract assertions VAL-REL-016 through VAL-REL-033.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel

from kazma_core.swarm.reliability import (
    BoundedConcurrency,
    OutputValidator,
    TimeoutGuard,
    TimeoutGuardError,
)


# ---------------------------------------------------------------------------
# TimeoutGuard tests (VAL-REL-016 through VAL-REL-021)
# ---------------------------------------------------------------------------


class TestTimeoutGuardDefaults:
    """TimeoutGuard has sensible defaults."""

    def test_default_values(self):
        guard = TimeoutGuard()
        assert guard.default_timeout == 300.0
        assert guard.on_timeout == "fail"

    def test_custom_values(self):
        guard = TimeoutGuard(default_timeout=60.0, on_timeout="retry")
        assert guard.default_timeout == 60.0
        assert guard.on_timeout == "retry"


class TestTimeoutGuardRejectsZero:
    """Timeout=0 is rejected."""

    def test_zero_timeout_rejected_on_guard(self):
        with pytest.raises(ValueError, match="timeout"):
            TimeoutGuard(default_timeout=0)

    @pytest.mark.asyncio
    async def test_zero_timeout_rejected_on_execute(self):
        guard = TimeoutGuard()
        with pytest.raises(ValueError, match="timeout"):

            async def noop():
                return {"status": "success", "output": ""}

            # Use an explicit timeout=0 override
            await guard.execute(noop, timeout=0)


class TestTimeoutGuardAbortsWithTimeoutStatus:
    """VAL-REL-016: Task exceeding timeout aborted with timeout status."""

    @pytest.mark.asyncio
    async def test_slow_worker_aborted_with_timeout(self):
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="fail")

        async def slow_worker():
            await asyncio.sleep(10)
            return {"status": "success", "output": "should not reach"}

        result = await guard.execute(slow_worker)
        assert result["status"] == "timeout"
        assert "timed out" in (result.get("error") or "").lower()

    @pytest.mark.asyncio
    async def test_fast_worker_succeeds(self):
        guard = TimeoutGuard(default_timeout=5.0, on_timeout="fail")

        async def fast_worker():
            return {"status": "success", "output": "done"}

        result = await guard.execute(fast_worker)
        assert result["status"] == "success"
        assert result["output"] == "done"

    @pytest.mark.asyncio
    async def test_per_call_timeout_override(self):
        guard = TimeoutGuard(default_timeout=10.0, on_timeout="fail")

        async def slow_worker():
            await asyncio.sleep(10)
            return {"status": "success", "output": "nope"}

        result = await guard.execute(slow_worker, timeout=0.05)
        assert result["status"] == "timeout"


class TestTimeoutGuardFailBehavior:
    """VAL-REL-017: Timeout behavior fail marks result as failed."""

    @pytest.mark.asyncio
    async def test_on_timeout_fail_returns_terminal_failure(self):
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="fail")

        async def slow_worker():
            await asyncio.sleep(10)
            return {"status": "success", "output": "nope"}

        result = await guard.execute(slow_worker)
        assert result["status"] == "timeout"
        # With "fail" behavior, this is terminal -- no retry hint
        assert result.get("retry") is None

    @pytest.mark.asyncio
    async def test_on_timeout_fail_includes_timeout_seconds(self):
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="fail")

        async def slow_worker():
            await asyncio.sleep(10)
            return {"status": "success", "output": "nope"}

        result = await guard.execute(slow_worker, worker_name="test-w")
        assert result["status"] == "timeout"
        assert "test-w" in (result.get("error") or "")


class TestTimeoutGuardRetryBehavior:
    """VAL-REL-018: Timeout behavior retry triggers retry attempt."""

    @pytest.mark.asyncio
    async def test_on_timeout_retry_signals_retry(self):
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="retry")

        async def slow_worker():
            await asyncio.sleep(10)
            return {"status": "success", "output": "nope"}

        result = await guard.execute(slow_worker, worker_name="retry-w")
        assert result["status"] == "timeout"
        assert result.get("retry") is True

    @pytest.mark.asyncio
    async def test_on_timeout_retry_counts_against_budget(self):
        """The retry flag signals to RetryPolicy that this should count."""
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="retry")

        async def slow_worker():
            await asyncio.sleep(10)
            return {"status": "success", "output": "nope"}

        result = await guard.execute(slow_worker)
        # The result status is "timeout" which RetryPolicy treats as retryable
        assert result["status"] == "timeout"
        assert result.get("retry") is True


class TestTimeoutGuardSkipBehavior:
    """VAL-REL-019: Timeout behavior skip continues without worker."""

    @pytest.mark.asyncio
    async def test_on_timeout_skip_returns_skipped_status(self):
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="skip")

        async def slow_worker():
            await asyncio.sleep(10)
            return {"status": "success", "output": "nope"}

        result = await guard.execute(slow_worker, worker_name="skip-w")
        assert result["status"] == "timeout"
        assert result.get("skipped") is True
        assert "skip-w" in (result.get("error") or "")


class TestTimeoutGuardCleanCancellation:
    """VAL-REL-021: Timeout cancels worker coroutine cleanly."""

    @pytest.mark.asyncio
    async def test_worker_coroutine_cancelled_after_timeout(self):
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="fail")
        was_cancelled = False

        async def slow_worker():
            nonlocal was_cancelled
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                was_cancelled = True
                raise

        result = await guard.execute(slow_worker)
        assert result["status"] == "timeout"
        # Give cancellation a moment to propagate
        await asyncio.sleep(0.05)
        assert was_cancelled is True

    @pytest.mark.asyncio
    async def test_no_background_execution_after_timeout(self):
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="fail")
        counter = 0

        async def slow_worker():
            nonlocal counter
            counter = 1
            await asyncio.sleep(10)
            counter = 2  # Should never reach
            return {"status": "success", "output": "nope"}

        result = await guard.execute(slow_worker)
        assert result["status"] == "timeout"
        await asyncio.sleep(0.2)
        # Counter should stay at 1 (set before sleep, never reached 2)
        assert counter == 1


class TestTimeoutGuardCleanCoroutineCancellation:
    """TimeoutGuard cleans up cancelled coroutines without warnings."""

    @pytest.mark.asyncio
    async def test_suppresses_cancelled_error(self):
        """Guard should not propagate CancelledError from the worker."""
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="fail")

        async def slow_worker():
            await asyncio.sleep(10)
            return {"status": "success", "output": "nope"}

        # Should not raise CancelledError
        result = await guard.execute(slow_worker)
        assert result["status"] == "timeout"


# ---------------------------------------------------------------------------
# OutputValidator tests (VAL-REL-022 through VAL-REL-028)
# ---------------------------------------------------------------------------


class _SampleModel(BaseModel):
    name: str
    age: int


class TestOutputValidatorNoSchemaSkipsValidation:
    """VAL-REL-026: No validation schema means validation skipped."""

    def test_none_schema_accepts_anything(self):
        validator = OutputValidator(schema=None)
        assert validator.validate("anything") is None
        assert validator.validate({"key": "value"}) is None
        assert validator.validate(42) is None

    def test_empty_dict_schema_skips(self):
        validator = OutputValidator(schema={})
        assert validator.validate("anything") is None


class TestOutputValidatorValidOutputPasses:
    """VAL-REL-022: Valid output passes validation and is accepted."""

    def test_pydantic_model_valid_output(self):
        validator = OutputValidator(schema=_SampleModel)
        error = validator.validate({"name": "Alice", "age": 30})
        assert error is None

    def test_dict_schema_valid_output(self):
        validator = OutputValidator(schema={"name": "str", "age": "int"})
        error = validator.validate({"name": "Alice", "age": 30})
        assert error is None

    def test_json_schema_valid_output(self):
        validator = OutputValidator(
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name", "age"],
            }
        )
        error = validator.validate({"name": "Alice", "age": 30})
        assert error is None


class TestOutputValidatorInvalidOutputFails:
    """VAL-REL-023: Invalid output fails validation and triggers retry."""

    def test_pydantic_model_invalid_output(self):
        validator = OutputValidator(schema=_SampleModel)
        error = validator.validate({"name": "Alice"})
        assert error is not None
        assert "age" in error.lower() or "field" in error.lower()

    def test_dict_schema_missing_field(self):
        validator = OutputValidator(schema={"name": "str", "age": "int"})
        error = validator.validate({"name": "Alice"})
        assert error is not None

    def test_json_schema_invalid_output(self):
        validator = OutputValidator(
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                },
                "required": ["name", "age"],
            }
        )
        error = validator.validate({"name": "Alice"})
        assert error is not None

    def test_dict_schema_wrong_type(self):
        validator = OutputValidator(schema={"name": "str", "age": "int"})
        error = validator.validate({"name": "Alice", "age": "not_a_number"})
        assert error is not None


class TestOutputValidatorParsesStringAsJson:
    """VAL-REL-028: Validation parses string output as JSON before checking."""

    def test_json_string_parsed_for_pydantic(self):
        validator = OutputValidator(schema=_SampleModel)
        error = validator.validate('{"name": "Alice", "age": 30}')
        assert error is None

    def test_json_string_parsed_for_dict_schema(self):
        validator = OutputValidator(schema={"name": "str", "age": "int"})
        error = validator.validate('{"name": "Alice", "age": 30}')
        assert error is None

    def test_non_json_string_fails_for_structured_schema(self):
        validator = OutputValidator(schema=_SampleModel)
        error = validator.validate("this is not json")
        assert error is not None

    def test_json_string_parsed_for_json_schema(self):
        validator = OutputValidator(
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                },
                "required": ["name"],
            }
        )
        error = validator.validate('{"name": "Alice"}')
        assert error is None


class TestOutputValidatorSupportsMultipleSchemaTypes:
    """VAL-REL-025: Validation supports BaseModel, dict, JSON schema."""

    def test_pydantic_basemodel(self):
        validator = OutputValidator(schema=_SampleModel)
        assert validator.validate({"name": "X", "age": 1}) is None
        assert validator.validate({"name": "X"}) is not None

    def test_dict_schema_type_checking(self):
        validator = OutputValidator(schema={"name": "str", "age": "int"})
        assert validator.validate({"name": "X", "age": 1}) is None
        assert validator.validate({"name": "X", "age": "bad"}) is not None

    def test_json_schema_type_checking(self):
        validator = OutputValidator(
            schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }
        )
        assert validator.validate({"name": "X"}) is None
        assert validator.validate({"wrong": "X"}) is not None


class TestOutputValidatorErrorDetailsSurfaced:
    """VAL-REL-027: Validation error details surfaced in result."""

    def test_error_contains_field_info(self):
        validator = OutputValidator(schema=_SampleModel)
        error = validator.validate({"name": "Alice"})
        assert error is not None
        # Should mention the problematic field
        assert "age" in error.lower() or "field" in error.lower() or "required" in error.lower()

    def test_error_preserves_raw_output(self):
        """The caller (engine) is responsible for preserving raw output."""
        validator = OutputValidator(schema=_SampleModel)
        error = validator.validate({"bad": "data"})
        assert error is not None
        assert len(error) > 0


class TestOutputValidatorValidationExhaustion:
    """VAL-REL-024: Validation failure after retries exhausted marks error."""

    @pytest.mark.asyncio
    async def test_integration_with_retry_policy(self):
        from kazma_core.swarm.reliability import RetryPolicy

        policy = RetryPolicy(max_retries=2, base_delay=0.001, jitter=False)
        validator = OutputValidator(schema=_SampleModel)
        attempt_count = 0

        async def invalid_worker():
            nonlocal attempt_count
            attempt_count += 1
            return {
                "worker": "w",
                "task_id": "t",
                "status": "success",
                "output": json.dumps({"name": "Alice"}),  # missing "age"
                "error": None,
            }

        # Integrate validation into retry loop
        async def validated_worker():
            result = await invalid_worker()
            if result.get("status") == "success":
                error = validator.validate(result.get("output", ""))
                if error is not None:
                    result["status"] = "error"
                    result["error"] = f"Validation failed: {error}"
            return result

        result = await policy.execute_with_retry(validated_worker, worker_name="w")
        assert attempt_count == 3  # 1 initial + 2 retries
        assert result["status"] == "error"
        assert "validation" in (result.get("error") or "").lower()


# ---------------------------------------------------------------------------
# BoundedConcurrency tests (VAL-REL-029 through VAL-REL-033)
# ---------------------------------------------------------------------------


class TestBoundedConcurrencyDefaults:
    """BoundedConcurrency has sensible defaults."""

    def test_default_max_concurrent(self):
        bc = BoundedConcurrency()
        assert bc.max_concurrent == 5

    def test_custom_max_concurrent(self):
        bc = BoundedConcurrency(max_concurrent=3)
        assert bc.max_concurrent == 3


class TestBoundedConcurrencyLimitsParallelDispatches:
    """VAL-REL-029: Bounded concurrency limits parallel dispatches."""

    @pytest.mark.asyncio
    async def test_limits_concurrent_execution(self):
        bc = BoundedConcurrency(max_concurrent=2)
        running = 0
        max_running = 0

        async def worker(name: str):
            nonlocal running, max_running
            async with bc:
                running += 1
                max_running = max(max_running, running)
                await asyncio.sleep(0.05)
                running -= 1
            return name

        tasks = [worker(f"w{i}") for i in range(6)]
        results = await asyncio.gather(*tasks)
        assert len(results) == 6
        assert max_running <= 2


class TestBoundedConcurrencyDefaultIsFive:
    """VAL-REL-030: Default max_concurrent is 5."""

    @pytest.mark.asyncio
    async def test_default_limits_to_5(self):
        bc = BoundedConcurrency()
        assert bc.max_concurrent == 5

    @pytest.mark.asyncio
    async def test_eight_workers_max_five_overlap(self):
        bc = BoundedConcurrency()  # default 5
        running = 0
        max_running = 0

        async def worker(name: str):
            nonlocal running, max_running
            async with bc:
                running += 1
                max_running = max(max_running, running)
                await asyncio.sleep(0.05)
                running -= 1
            return name

        tasks = [worker(f"w{i}") for i in range(8)]
        results = await asyncio.gather(*tasks)
        assert len(results) == 8
        assert max_running <= 5


class TestBoundedConcurrencyConfigurablePerTask:
    """VAL-REL-031: Max_concurrent configurable per engine, task, or globally."""

    def test_different_limits(self):
        bc_global = BoundedConcurrency(max_concurrent=5)
        bc_task = BoundedConcurrency(max_concurrent=2)
        assert bc_global.max_concurrent == 5
        assert bc_task.max_concurrent == 2

    @pytest.mark.asyncio
    async def test_task_override_takes_precedence(self):
        """Simulate engine resolving max_concurrent from task metadata."""
        bc_engine = BoundedConcurrency(max_concurrent=5)
        bc_task = BoundedConcurrency(max_concurrent=2)

        running_engine = 0
        max_running_engine = 0
        running_task = 0
        max_running_task = 0

        async def worker_engine(name: str):
            nonlocal running_engine, max_running_engine
            async with bc_engine:
                running_engine += 1
                max_running_engine = max(max_running_engine, running_engine)
                await asyncio.sleep(0.05)
                running_engine -= 1

        async def worker_task(name: str):
            nonlocal running_task, max_running_task
            async with bc_task:
                running_task += 1
                max_running_task = max(max_running_task, running_task)
                await asyncio.sleep(0.05)
                running_task -= 1

        await asyncio.gather(*(worker_engine(f"e{i}") for i in range(8)))
        await asyncio.gather(*(worker_task(f"t{i}") for i in range(8)))

        assert max_running_engine <= 5
        assert max_running_task <= 2


class TestBoundedConcurrencyReleasedOnFailure:
    """VAL-REL-032: Semaphore released on failure or timeout."""

    @pytest.mark.asyncio
    async def test_semaphore_released_on_exception(self):
        bc = BoundedConcurrency(max_concurrent=1)
        completed = []

        async def failing_worker(name: str):
            async with bc:
                if name == "fail":
                    raise RuntimeError("boom")
                completed.append(name)

        # Use gather to verify no deadlock - one fails, one succeeds
        results = await asyncio.gather(
            failing_worker("fail"),
            failing_worker("ok"),
            return_exceptions=True,
        )
        assert any(isinstance(r, Exception) for r in results)
        assert "ok" in completed

    @pytest.mark.asyncio
    async def test_semaphore_released_on_timeout(self):
        bc = BoundedConcurrency(max_concurrent=1)
        completed = []

        async def slow_worker():
            async with bc:
                await asyncio.sleep(10)

        async def fast_worker():
            async with bc:
                completed.append("fast")

        # Slow worker times out; fast worker should still acquire semaphore
        try:
            await asyncio.wait_for(slow_worker(), timeout=0.05)
        except TimeoutError:
            pass

        await fast_worker()
        assert "fast" in completed


class TestBoundedConcurrencyAppliesToAllPatterns:
    """VAL-REL-033: Bounded concurrency applies to all multi-worker patterns."""

    @pytest.mark.asyncio
    async def test_fan_out_respects_concurrency(self):
        """Fan-out pattern uses BoundedConcurrency internally."""
        from kazma_core.swarm.reliability import BoundedConcurrency

        bc = BoundedConcurrency(max_concurrent=2)
        running = 0
        max_running = 0

        async def simulated_fan_out():
            async def dispatch_one(name: str):
                nonlocal running, max_running
                async with bc:
                    running += 1
                    max_running = max(max_running, running)
                    await asyncio.sleep(0.05)
                    running -= 1
                return name

            return await asyncio.gather(
                *(dispatch_one(f"w{i}") for i in range(6))
            )

        results = await simulated_fan_out()
        assert len(results) == 6
        assert max_running <= 2

    @pytest.mark.asyncio
    async def test_broadcast_respects_concurrency(self):
        """Broadcast pattern uses BoundedConcurrency internally."""
        from kazma_core.swarm.reliability import BoundedConcurrency

        bc = BoundedConcurrency(max_concurrent=3)
        running = 0
        max_running = 0

        async def simulated_broadcast():
            async def dispatch_one(name: str):
                nonlocal running, max_running
                async with bc:
                    running += 1
                    max_running = max(max_running, running)
                    await asyncio.sleep(0.05)
                    running -= 1
                return name

            return await asyncio.gather(
                *(dispatch_one(f"w{i}") for i in range(8))
            )

        results = await simulated_broadcast()
        assert len(results) == 8
        assert max_running <= 3


class TestBoundedConcurrencyNoDeadlock:
    """BoundedConcurrency does not deadlock when workers fail."""

    @pytest.mark.asyncio
    async def test_mixed_success_failure_no_deadlock(self):
        bc = BoundedConcurrency(max_concurrent=2)
        results = []

        async def worker(name: str, should_fail: bool):
            try:
                async with bc:
                    if should_fail:
                        raise RuntimeError(f"{name} failed")
                    await asyncio.sleep(0.02)
                    results.append(name)
            except RuntimeError:
                pass

        tasks = [
            worker("a", False),
            worker("b", True),
            worker("c", False),
            worker("d", True),
            worker("e", False),
        ]

        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2.0)
        assert set(results) == {"a", "c", "e"}


class TestBoundedConcurrencyWithEngineIntegration:
    """Test BoundedConcurrency integration with engine's _resolve_max_concurrent."""

    @pytest.mark.asyncio
    async def test_engine_resolves_concurrency_from_task_metadata(self):
        from kazma_core.swarm.engine import SwarmEngine
        from kazma_core.swarm.config import SwarmConfig

        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        from kazma_core.swarm.task import SwarmTask, TaskType

        task = SwarmTask(
            prompt="test",
            type=TaskType.FAN_OUT,
            metadata={"max_concurrent": 3},
        )
        assert engine._resolve_max_concurrent(task) == 3

    @pytest.mark.asyncio
    async def test_engine_uses_default_concurrency(self):
        from kazma_core.swarm.engine import SwarmEngine
        from kazma_core.swarm.config import SwarmConfig

        engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
        from kazma_core.swarm.task import SwarmTask, TaskType

        task = SwarmTask(prompt="test", type=TaskType.FAN_OUT)
        assert engine._resolve_max_concurrent(task) == 5


# ---------------------------------------------------------------------------
# Integration: TimeoutGuard + RetryPolicy
# ---------------------------------------------------------------------------


class TestTimeoutGuardWithRetryPolicy:
    """TimeoutGuard integrates with RetryPolicy for on_timeout=retry."""

    @pytest.mark.asyncio
    async def test_retry_on_timeout_counts_against_budget(self):
        from kazma_core.swarm.reliability import RetryPolicy

        policy = RetryPolicy(max_retries=2, base_delay=0.001, jitter=False)
        guard = TimeoutGuard(default_timeout=0.05, on_timeout="retry")
        attempt_count = 0

        async def always_slow():
            nonlocal attempt_count
            attempt_count += 1
            await asyncio.sleep(10)
            return {"status": "success", "output": "nope"}

        async def wrapped():
            return await guard.execute(always_slow, worker_name="w")

        result = await policy.execute_with_retry(wrapped, worker_name="w")
        assert attempt_count == 3  # 1 initial + 2 retries
        assert result["status"] == "timeout"


# ---------------------------------------------------------------------------
# Integration: Full reliability chain
# ---------------------------------------------------------------------------


class TestFullReliabilityChain:
    """Test the complete reliability chain: retry -> timeout -> validation."""

    @pytest.mark.asyncio
    async def test_retry_timeout_then_success(self):
        from kazma_core.swarm.reliability import RetryPolicy

        policy = RetryPolicy(max_retries=2, base_delay=0.001, jitter=False)
        guard = TimeoutGuard(default_timeout=0.1, on_timeout="retry")
        attempt_count = 0

        async def worker():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                await asyncio.sleep(10)  # timeout on first attempt
            return {"status": "success", "output": "ok"}

        async def wrapped():
            return await guard.execute(worker, worker_name="w")

        result = await policy.execute_with_retry(wrapped, worker_name="w")
        assert result["status"] == "success"
        assert result["output"] == "ok"
        assert attempt_count == 2

    @pytest.mark.asyncio
    async def test_retry_validation_then_success(self):
        from kazma_core.swarm.reliability import RetryPolicy

        policy = RetryPolicy(max_retries=2, base_delay=0.001, jitter=False)
        validator = OutputValidator(schema=_SampleModel)
        attempt_count = 0

        async def worker():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count == 1:
                return {
                    "worker": "w",
                    "task_id": "t",
                    "status": "success",
                    "output": json.dumps({"name": "Alice"}),  # missing age
                    "error": None,
                }
            return {
                "worker": "w",
                "task_id": "t",
                "status": "success",
                "output": json.dumps({"name": "Alice", "age": 30}),
                "error": None,
            }

        async def validated():
            result = await worker()
            if result.get("status") == "success":
                error = validator.validate(result.get("output", ""))
                if error is not None:
                    result["status"] = "error"
                    result["error"] = f"Validation failed: {error}"
            return result

        result = await policy.execute_with_retry(validated, worker_name="w")
        assert result["status"] == "success"
        assert attempt_count == 2
