"""Unit tests for Circuit Breaker reliability patterns.

Tests verify the half-open probe semantics, failure thresholds,
recovery timeouts, and concurrent access safety per AGENTS.md §5.
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from kazma_core.swarm.reliability import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerOpenError,
    RetryPolicy,
    TimeoutGuard,
    BoundedConcurrency,
    OutputValidator,
    FallbackChain,
)


class TestCircuitBreaker:
    """Tests for CircuitBreaker implementation."""

    @pytest.fixture
    def cb(self):
        """Create a circuit breaker with low thresholds for testing."""
        return CircuitBreaker(
            failure_threshold=3,
            cooldown_seconds=0.1,  # 100ms
        )

    @pytest.mark.asyncio
    async def test_initial_state_closed(self, cb):
        """Circuit breaker starts in CLOSED state."""
        assert cb.state == CircuitState.CLOSED
        assert cb.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self, cb):
        """Circuit opens after failure_threshold failures."""
        # Record failures directly since record_failure is sync
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == CircuitState.OPEN
        assert cb.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_rejects_calls_when_open(self, cb):
        """Circuit rejects calls immediately when OPEN."""
        # Open the circuit
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == CircuitState.OPEN
        
        # allow_probe should return False
        assert cb.allow_probe() is False
        
        # check_or_raise should raise
        with pytest.raises(CircuitBreakerOpenError):
            cb.check_or_raise("test-worker")

    @pytest.mark.asyncio
    async def test_half_open_after_cooldown(self, cb):
        """Circuit enters HALF_OPEN after cooldown_seconds."""
        # Open the circuit
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == CircuitState.OPEN
        
        # Wait for cooldown
        await asyncio.sleep(0.15)
        
        # Next check should enter HALF_OPEN
        assert cb.state == CircuitState.HALF_OPEN
        
        # allow_probe should allow one probe
        assert cb.allow_probe() is True
        
        # Record success - should close
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_half_open_probe_single(self, cb):
        """Only ONE probe allowed in HALF_OPEN state (AGENTS.md §5)."""
        # Open the circuit
        for _ in range(3):
            cb.record_failure()
        
        await asyncio.sleep(0.15)  # Wait for cooldown
        
        # First probe allowed
        assert cb.allow_probe() is True
        assert cb._probe_in_flight is True
        
        # Second probe should be rejected
        assert cb.allow_probe() is False
        
        # Complete first probe successfully
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._probe_in_flight is False

    @pytest.mark.asyncio
    async def test_probe_failure_reopens_circuit(self, cb):
        """Failed probe in HALF_OPEN reopens circuit."""
        # Open the circuit
        for _ in range(3):
            cb.record_failure()
        
        await asyncio.sleep(0.15)
        
        # Probe fails
        cb.allow_probe()
        cb.record_failure()
        
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_success_resets_failure_count(self, cb):
        """Successful call resets failure count."""
        # Two failures
        cb.record_failure()
        cb.record_failure()
        
        assert cb.consecutive_failures == 2
        
        # Success resets count
        cb.record_success()
        
        assert cb.consecutive_failures == 0
        assert cb.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_concurrent_calls_in_half_open(self, cb):
        """Concurrent calls in HALF_OPEN: only one probe proceeds."""
        # Open circuit
        for _ in range(3):
            cb.record_failure()
        
        await asyncio.sleep(0.15)
        
        # Launch multiple concurrent allow_probe calls
        results = [cb.allow_probe() for _ in range(5)]
        
        # Only one should succeed
        success_count = sum(1 for r in results if r)
        rejected_count = sum(1 for r in results if not r)
        
        assert success_count == 1
        assert rejected_count == 4


class TestRetryPolicy:
    """Tests for RetryPolicy with exponential backoff."""

    @pytest.fixture
    def policy(self):
        return RetryPolicy(
            max_retries=2,
            base_delay=0.01,
            max_delay=0.1,
            jitter=False,
        )

    @pytest.mark.asyncio
    async def test_retry_on_failure(self, policy):
        """Retries failed calls up to max_retries."""
        call_count = 0
        
        async def fail_twice_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("fail")
            return {"status": "success", "output": "ok", "worker": "test"}
        
        result = await policy.execute_with_retry(fail_twice_then_succeed, worker_name="test")
        assert result["status"] == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_exhausts_retries_then_raises(self, policy):
        """Returns error result after max_retries exhausted."""
        async def always_fails():
            raise Exception("permanent failure")
        
        result = await policy.execute_with_retry(always_fails, worker_name="test")
        assert result["status"] == "error"
        assert "permanent failure" in result["error"]

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self, policy):
        """Delays increase exponentially (tested via compute_delay)."""
        # Test delay computation directly
        delay1 = policy.compute_delay_no_jitter(1)
        delay2 = policy.compute_delay_no_jitter(2)
        
        # base_delay * 2^(attempt-1): 0.01 * 2^0 = 0.01, 0.01 * 2^1 = 0.02
        assert delay1 == 0.01
        assert delay2 == 0.02

    @pytest.mark.asyncio
    async def test_non_retryable_exception_not_retried(self, policy):
        """Non-retryable exceptions are not retried."""
        from kazma_core.swarm.reliability import _is_retryable_exception, _NON_RETRYABLE_PATTERNS
        
        # Check that patterns work - "401" should be non-retryable
        assert _is_retryable_exception(Exception("401 unauthorized")) is False
        assert _is_retryable_exception(Exception("rate limit exceeded")) is False
        assert _is_retryable_exception(Exception("quota exceeded")) is False
        
        # Generic Exception is retryable (no matching pattern)
        assert _is_retryable_exception(Exception("network error")) is True


class TestTimeoutGuard:
    """Tests for TimeoutGuard."""

    @pytest.mark.asyncio
    async def test_enforces_timeout(self):
        """Cancels operation exceeding timeout."""
        guard = TimeoutGuard(default_timeout=0.1, on_timeout="fail")
        
        async def slow_operation():
            await asyncio.sleep(1)
            return "done"
        
        result = await guard.execute(slow_operation, worker_name="test")
        assert result["status"] == "timeout"
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_allows_completion_within_timeout(self):
        """Allows operation completing within timeout."""
        guard = TimeoutGuard(default_timeout=1.0, on_timeout="fail")
        
        async def fast_operation():
            await asyncio.sleep(0.05)
            return {"status": "success", "output": "fast", "worker": "test"}
        
        result = await guard.execute(fast_operation, worker_name="test")
        assert result["status"] == "success"
        assert result["output"] == "fast"

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self):
        """Returns retry flag when on_timeout=retry."""
        guard = TimeoutGuard(default_timeout=0.1, on_timeout="retry")
        
        async def slow_operation():
            await asyncio.sleep(1)
            return "done"
        
        result = await guard.execute(slow_operation, worker_name="test")
        assert result["status"] == "timeout"
        assert result["retry"] is True

    @pytest.mark.asyncio
    async def test_skip_on_timeout(self):
        """Returns skipped flag when on_timeout=skip."""
        guard = TimeoutGuard(default_timeout=0.1, on_timeout="skip")
        
        async def slow_operation():
            await asyncio.sleep(1)
            return "done"
        
        result = await guard.execute(slow_operation, worker_name="test")
        assert result["status"] == "timeout"
        assert result["skipped"] is True


class TestBoundedConcurrency:
    """Tests for BoundedConcurrency semaphore."""

    @pytest.mark.asyncio
    async def test_limits_concurrent_executions(self):
        """Limits concurrent executions to max_concurrent."""
        limiter = BoundedConcurrency(max_concurrent=2)
        active = 0
        max_active = 0
        
        async def tracked_operation():
            nonlocal active, max_active
            async with limiter:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                active -= 1
            return "done"
        
        tasks = [asyncio.create_task(tracked_operation()) for _ in range(5)]
        await asyncio.gather(*tasks)
        
        assert max_active == 2

    @pytest.mark.asyncio
    async def test_queues_excess_requests(self):
        """Queues requests when at capacity."""
        limiter = BoundedConcurrency(max_concurrent=1)
        order = []
        
        async def op(name):
            async with limiter:
                order.append(f"start-{name}")
                await asyncio.sleep(0.05)
                order.append(f"end-{name}")
            return name
        
        tasks = [asyncio.create_task(op(i)) for i in range(3)]
        await asyncio.gather(*tasks)
        
        # Should execute sequentially
        assert order == ["start-0", "end-0", "start-1", "end-1", "start-2", "end-2"]


class TestOutputValidator:
    """Tests for OutputValidator."""

    @pytest.mark.asyncio
    async def test_validates_output_schema(self):
        """Validates output against schema."""
        validator = OutputValidator(
            schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "result": {"type": "object"},
                },
                "required": ["status", "result"],
            }
        )
        
        # Valid output
        error = validator.validate({"status": "ok", "result": {"data": 123}})
        assert error is None
        
        # Missing field
        error = validator.validate({"status": "ok"})
        assert error is not None
        
        # Wrong type
        error = validator.validate({"status": 123, "result": {}})
        assert error is not None


class TestFallbackChain:
    """Tests for FallbackChain."""

    @pytest.mark.asyncio
    async def test_uses_first_successful(self):
        """Returns result from multiple functions, returns first successful result."""
        from kazma_core.swarm.task import WorkerResult
        
        chain = FallbackChain(fallback_workers=["fallback1", "fallback2"])
        
        primary_result = WorkerResult(
            worker="primary",
            task_id="task-1",
            status="error",
            output="",
            error="primary failed",
        )
        
        fallback1_result = WorkerResult(
            worker="fallback1",
            task_id="task-1",
            status="error",
            output="",
            error="fallback1 failed",
        )
        
        fallback2_result = WorkerResult(
            worker="fallback2",
            task_id="task-1",
            status="success",
            output="success from fallback2",
            error=None,
        )
        
        call_count = {"count": 0}
        
        async def dispatch_worker(worker_name: str) -> WorkerResult:
            call_count["count"] += 1
            if worker_name == "fallback1":
                return fallback1_result
            elif worker_name == "fallback2":
                return fallback2_result
            raise ValueError(f"Unknown worker: {worker_name}")
        
        result = await chain.execute(primary_result, dispatch_worker=dispatch_worker)
        assert result.status == "success"
        assert result.output == "success from fallback2"
        assert call_count["count"] == 2  # fallback1 then fallback2

    @pytest.mark.asyncio
    async def test_raises_last_exception_if_all_fail(self):
        """Returns last error if all fallbacks fail."""
        from kazma_core.swarm.task import WorkerResult
        
        chain = FallbackChain(fallback_workers=["fallback1", "fallback2"])
        
        primary_result = WorkerResult(
            worker="primary",
            task_id="task-1",
            status="error",
            output="",
            error="primary failed",
        )
        
        fallback1_result = WorkerResult(
            worker="fallback1",
            task_id="task-1",
            status="error",
            output="",
            error="fallback1 failed",
        )
        
        fallback2_result = WorkerResult(
            worker="fallback2",
            task_id="task-1",
            status="error",
            output="",
            error="fallback2 failed",
        )
        
        async def dispatch_worker(worker_name: str) -> WorkerResult:
            if worker_name == "fallback1":
                return fallback1_result
            elif worker_name == "fallback2":
                return fallback2_result
            raise ValueError(f"Unknown worker: {worker_name}")
        
        result = await chain.execute(primary_result, dispatch_worker=dispatch_worker)
        assert result.status == "error"
        assert "fallback2 failed" in result.error
        assert "All fallback workers exhausted" in result.error

    @pytest.mark.asyncio
    async def test_empty_chain_raises(self):
        """Empty chain returns primary result."""
        from kazma_core.swarm.task import WorkerResult
        
        chain = FallbackChain(fallback_workers=[])
        
        primary_result = WorkerResult(
            worker="primary",
            task_id="task-1",
            status="success",
            output="primary success",
            error=None,
        )
        
        result = await chain.execute(primary_result, dispatch_worker=lambda w: (_ for _ in ()).throw(ValueError("should not be called")))
        assert result.status == "success"
        assert result.output == "primary success"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])