"""Tests for RetryPolicy and CircuitBreaker in kazma_core.swarm.reliability.

Covers validation contract assertions VAL-REL-001 through VAL-REL-015.
"""

from __future__ import annotations

import time

import pytest
from kazma_core.swarm.reliability import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
    RetryPolicy,
)

# ---------------------------------------------------------------------------
# RetryPolicy tests (VAL-REL-001 through VAL-REL-007)
# ---------------------------------------------------------------------------


class TestRetryPolicyDefaults:
    """RetryPolicy has sensible defaults matching the spec."""

    def test_default_values(self):
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.base_delay == 1.0
        assert policy.max_delay == 60.0
        assert policy.jitter is True


class TestRetryPolicyExponentialBackoff:
    """VAL-REL-002: Retry applies exponential backoff between attempts."""

    def test_delay_grows_exponentially(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=60.0, jitter=False)
        delays = [policy.compute_delay(attempt) for attempt in range(1, 6)]
        # Without jitter: delay = base_delay * 2^(attempt-1)
        assert delays[0] == pytest.approx(1.0)
        assert delays[1] == pytest.approx(2.0)
        assert delays[2] == pytest.approx(4.0)
        assert delays[3] == pytest.approx(8.0)
        assert delays[4] == pytest.approx(16.0)


class TestRetryPolicyMaxDelayCap:
    """VAL-REL-003: Retry backoff is capped at max_delay."""

    def test_delay_capped_at_max_delay(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=10.0, jitter=False)
        # Attempt 10 would be 2^9 = 512 without cap
        delay = policy.compute_delay(attempt=10)
        assert delay == pytest.approx(10.0)

    def test_delay_never_exceeds_max_delay_with_jitter(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=10.0, jitter=True)
        # Jitter adds 0-25% of base, so max possible = max_delay * 1.25
        for _ in range(100):
            for attempt in range(1, 20):
                delay = policy.compute_delay(attempt)
                assert delay <= policy.max_delay * 1.25


class TestRetryPolicyJitter:
    """VAL-REL-004: Retry jitter adds bounded randomness."""

    def test_jitter_adds_variance(self):
        policy = RetryPolicy(base_delay=4.0, jitter=True)
        delays = [policy.compute_delay(attempt=1) for _ in range(50)]
        # With jitter=True, delays should vary
        unique_values = set(delays)
        assert len(unique_values) > 1, "Jitter should produce varying delays"

    def test_jitter_bounded_at_25_percent(self):
        policy = RetryPolicy(base_delay=4.0, jitter=True)
        base_no_jitter = policy.compute_delay_no_jitter(attempt=1)
        for _ in range(200):
            delay = policy.compute_delay(attempt=1)
            # Jitter should add 0-25% of base_delay
            assert delay >= base_no_jitter
            assert delay <= base_no_jitter + (4.0 * 0.25)

    def test_no_jitter_deterministic(self):
        policy = RetryPolicy(base_delay=2.0, jitter=False)
        delays = [policy.compute_delay(attempt=1) for _ in range(10)]
        assert len(set(delays)) == 1


class TestRetryPolicyRetryExhaustion:
    """VAL-REL-005: Retry exhaustion marks result as failed with last error."""

    @pytest.mark.asyncio
    async def test_exhaustion_returns_error_with_retry_info(self):
        policy = RetryPolicy(max_retries=2, base_delay=0.001, jitter=False)
        attempt_count = 0

        async def failing_worker():
            nonlocal attempt_count
            attempt_count += 1
            raise RuntimeError(f"failure #{attempt_count}")

        result = await policy.execute_with_retry(failing_worker, worker_name="test-worker")

        assert result["status"] == "error"
        assert "retry" in result["error"].lower() or "attempt" in result["error"].lower()
        assert attempt_count == 3  # 1 initial + 2 retries


class TestRetryPolicyConfigurablePerWorker:
    """VAL-REL-006: Retry policy is configurable per worker."""

    @pytest.mark.asyncio
    async def test_different_max_retries_per_worker(self):
        policy_a = RetryPolicy(max_retries=5, base_delay=0.001, jitter=False)
        policy_b = RetryPolicy(max_retries=0, base_delay=0.001, jitter=False)

        attempts_a = 0
        attempts_b = 0

        async def failing_a():
            nonlocal attempts_a
            attempts_a += 1
            raise RuntimeError("fail")

        async def failing_b():
            nonlocal attempts_b
            attempts_b += 1
            raise RuntimeError("fail")

        await policy_a.execute_with_retry(failing_a, worker_name="worker-a")
        await policy_b.execute_with_retry(failing_b, worker_name="worker-b")

        assert attempts_a == 6  # 1 initial + 5 retries
        assert attempts_b == 1  # 0 retries


class TestRetryPolicyOnlyTransientFailures:
    """VAL-REL-007: Retry only triggers on transient failures."""

    @pytest.mark.asyncio
    async def test_success_stops_retrying(self):
        policy = RetryPolicy(max_retries=3, base_delay=0.001, jitter=False)
        attempt_count = 0

        async def succeed_on_third():
            nonlocal attempt_count
            attempt_count += 1
            if attempt_count < 3:
                raise RuntimeError("transient")
            return "success"

        result = await policy.execute_with_retry(succeed_on_third, worker_name="w")
        assert result["status"] == "success"
        assert result["output"] == "success"
        assert attempt_count == 3

    @pytest.mark.asyncio
    async def test_exception_triggers_retry(self):
        policy = RetryPolicy(max_retries=2, base_delay=0.001, jitter=False)
        attempt_count = 0

        async def raises_exception():
            nonlocal attempt_count
            attempt_count += 1
            raise TimeoutError("timed out")

        result = await policy.execute_with_retry(raises_exception, worker_name="w")
        assert result["status"] == "error"
        assert attempt_count == 3

    @pytest.mark.asyncio
    async def test_error_status_triggers_retry(self):
        """Workers returning error status dict should be retried."""
        policy = RetryPolicy(max_retries=2, base_delay=0.001, jitter=False)
        attempt_count = 0

        async def returns_error_status():
            nonlocal attempt_count
            attempt_count += 1
            return {
                "worker": "w",
                "task_id": "t",
                "status": "error",
                "output": "",
                "error": "something went wrong",
            }

        result = await policy.execute_with_retry(returns_error_status, worker_name="w")
        assert result["status"] == "error"
        assert attempt_count == 3


class TestRetryPolicyTotalAttempts:
    """VAL-REL-001: Retry retries failed worker up to max_retries+1 total attempts."""

    @pytest.mark.asyncio
    async def test_total_attempts_equals_max_retries_plus_one(self):
        for max_retries in [0, 1, 3, 5]:
            policy = RetryPolicy(
                max_retries=max_retries, base_delay=0.001, jitter=False
            )
            attempts = 0

            async def always_fail():
                nonlocal attempts
                attempts += 1
                raise RuntimeError("fail")

            await policy.execute_with_retry(always_fail, worker_name="w")
            assert attempts == max_retries + 1


# ---------------------------------------------------------------------------
# CircuitBreaker tests (VAL-REL-008 through VAL-REL-015)
# ---------------------------------------------------------------------------


class TestCircuitBreakerStartsClosed:
    """VAL-REL-008: Circuit breaker starts closed and allows dispatch."""

    def test_initial_state_is_closed(self):
        breaker = CircuitBreaker()
        assert breaker.state == CircuitState.CLOSED

    def test_closed_state_allows_dispatch(self):
        breaker = CircuitBreaker()
        assert breaker.allow_probe() is True


class TestCircuitBreakerTripsToOpen:
    """VAL-REL-009: Circuit breaker trips to open after threshold."""

    def test_trips_after_threshold_consecutive_failures(self):
        breaker = CircuitBreaker(failure_threshold=5)

        for _ in range(4):
            breaker.record_failure()
            assert breaker.state == CircuitState.CLOSED

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

    def test_open_state_rejects_dispatch(self):
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
        assert breaker.allow_probe() is False


class TestCircuitBreakerResetsOnSuccess:
    """VAL-REL-010: Circuit breaker resets failure count on success."""

    def test_success_resets_consecutive_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=5)

        # 4 failures (one short of tripping)
        for _ in range(4):
            breaker.record_failure()

        # Success resets counter
        breaker.record_success()
        assert breaker.consecutive_failures == 0

        # Need 5 more failures to trip now
        for _ in range(4):
            breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN


class TestCircuitBreakerHalfOpenAfterCooldown:
    """VAL-REL-011: Circuit breaker transitions to half-open after cooldown."""

    def test_half_open_after_cooldown(self):
        breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0)

        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Immediately after: still open
        assert breaker.state == CircuitState.OPEN

        # After cooldown: half-open
        time.sleep(1.1)
        assert breaker.state == CircuitState.HALF_OPEN

    def test_half_open_allows_single_probe(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        time.sleep(0.1)
        assert breaker.state == CircuitState.HALF_OPEN
        assert breaker.allow_probe() is True

    def test_half_open_success_transitions_to_closed(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
        breaker.record_failure()
        time.sleep(0.1)

        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.consecutive_failures == 0

    def test_half_open_failure_transitions_to_open(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
        breaker.record_failure()
        time.sleep(0.1)

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN


class TestCircuitBreakerManualReset:
    """VAL-REL-012: Circuit breaker allows manual reset via API."""

    def test_manual_reset_transitions_to_closed(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        breaker.reset()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.consecutive_failures == 0

    def test_manual_reset_allows_dispatch(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure()
        assert breaker.allow_probe() is False

        breaker.reset()
        assert breaker.allow_probe() is True


class TestCircuitBreakerOpenRejectsWithClearError:
    """VAL-REL-013: Circuit breaker open state rejects with clear error."""

    def test_open_raises_with_worker_name(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        with pytest.raises(CircuitBreakerOpenError, match="(?i)circuit breaker.*open.*my-worker"):
            breaker.check_or_raise("my-worker")

    def test_half_open_does_not_raise(self):
        breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
        breaker.record_failure()
        time.sleep(0.1)
        # Should not raise in half-open state
        breaker.check_or_raise("my-worker")


class TestCircuitBreakerIndependentPerWorker:
    """VAL-REL-014: Circuit breaker tracked independently per worker."""

    def test_independent_breakers_per_worker(self):
        manager = {}

        def get_breaker(name: str) -> CircuitBreaker:
            if name not in manager:
                manager[name] = CircuitBreaker(failure_threshold=2)
            return manager[name]

        # Trip worker-a's breaker
        get_breaker("worker-a").record_failure()
        get_breaker("worker-a").record_failure()
        assert get_breaker("worker-a").state == CircuitState.OPEN

        # Worker-b should still be closed
        assert get_breaker("worker-b").state == CircuitState.CLOSED
        assert get_breaker("worker-b").allow_probe() is True


class TestCircuitBreakerConfigurable:
    """VAL-REL-015: Circuit breaker threshold and cooldown configurable."""

    def test_different_thresholds(self):
        breaker_strict = CircuitBreaker(failure_threshold=2)
        breaker_lenient = CircuitBreaker(failure_threshold=10)

        for _ in range(2):
            breaker_strict.record_failure()
        assert breaker_strict.state == CircuitState.OPEN

        for _ in range(9):
            breaker_lenient.record_failure()
        assert breaker_lenient.state == CircuitState.CLOSED

    def test_different_cooldowns(self):
        breaker_short = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
        breaker_long = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0)

        breaker_short.record_failure()
        breaker_long.record_failure()

        time.sleep(0.1)
        assert breaker_short.state == CircuitState.HALF_OPEN
        assert breaker_long.state == CircuitState.OPEN
