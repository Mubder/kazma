"""Unit tests for ReliabilityRegistry (P2-1 extracted module).

The registry owns per-worker circuit breakers, retry policies, timeout
guards, output validators, and bounded concurrency. These tests verify
lazy creation, memoization, cleanup, persistence round-trips, and the
cache sharing semantics per AGENTS.md §5 and §9.
"""

import asyncio
from unittest.mock import patch

import pytest

from kazma_core.swarm.reliability import (
    CircuitBreaker,
    CircuitState,
    OutputValidator,
    RetryPolicy,
    TimeoutGuard,
)
from kazma_core.swarm.reliability_registry import ReliabilityRegistry


@pytest.fixture
def registry():
    """Registry with a static worker list, no shared-state side effects."""
    with (
        patch("kazma_core.swarm.reliability.CircuitBreaker.load_shared",
              return_value=None),
        patch("kazma_core.swarm.reliability.CircuitBreaker.persist_shared"),
    ):
        yield ReliabilityRegistry(worker_names=lambda: ["alpha", "beta"])


class TestCircuitBreakerRegistry:
    """Per-worker circuit breaker management."""

    def test_get_creates_lazily(self, registry):
        """First access creates a closed breaker for the worker."""
        breaker = registry.get_circuit_breaker("alpha")
        assert breaker.state == CircuitState.CLOSED

    def test_get_memoizes_per_worker(self, registry):
        """Repeated access returns the same instance per worker."""
        assert registry.get_circuit_breaker("alpha") is registry.get_circuit_breaker("alpha")
        assert registry.get_circuit_breaker("alpha") is not registry.get_circuit_breaker("beta")

    def test_get_hydrates_shared_breaker(self):
        """When a shared breaker exists, it is hydrated instead of a fresh one."""
        shared = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.01)
        shared.record_failure()
        with patch("kazma_core.swarm.reliability.CircuitBreaker.load_shared",
                   return_value=shared):
            reg = ReliabilityRegistry(worker_names=lambda: [])
            assert reg.get_circuit_breaker("alpha") is shared
            assert reg.get_circuit_breaker("alpha").state == CircuitState.OPEN

    def test_set_config_replaces_and_persists(self, registry):
        """set_circuit_breaker_config replaces the breaker and persists it."""
        registry.set_circuit_breaker_config("alpha", failure_threshold=2, cooldown_seconds=0.05)
        breaker = registry.get_circuit_breaker("alpha")
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

    def test_reset_circuit_breaker_closes(self, registry):
        """reset_circuit_breaker forces a breaker back to CLOSED."""
        registry.set_circuit_breaker_config("alpha", failure_threshold=1, cooldown_seconds=0.05)
        registry.get_circuit_breaker("alpha").record_failure()
        assert registry.get_circuit_breaker("alpha").state == CircuitState.OPEN
        registry.reset_circuit_breaker("alpha")
        assert registry.get_circuit_breaker("alpha").state == CircuitState.CLOSED

    def test_status_snapshot(self, registry):
        """get_circuit_breaker_status returns a JSON-serializable dict."""
        status = registry.get_circuit_breaker_status("alpha")
        assert isinstance(status, dict)
        assert "state" in status

    def test_all_status_uses_worker_names(self, registry):
        """get_all_circuit_breaker_status enumerates registered workers."""
        registry.get_circuit_breaker("alpha").record_failure()
        statuses = registry.get_all_circuit_breaker_status()
        assert set(statuses) == {"alpha", "beta"}

    def test_save_load_round_trip(self, registry):
        """save_breaker_state/load_breaker_state preserve open/closed states."""
        registry.set_circuit_breaker_config("alpha", failure_threshold=1, cooldown_seconds=0.05)
        registry.get_circuit_breaker("alpha").record_failure()
        registry.get_circuit_breaker("beta")
        snapshot = registry.save_breaker_state()
        assert set(snapshot) == {"alpha", "beta"}

        fresh = ReliabilityRegistry(worker_names=lambda: [])
        fresh.load_breaker_state(snapshot)
        assert fresh.get_circuit_breaker("alpha").state == CircuitState.OPEN
        assert fresh.get_circuit_breaker("beta").state == CircuitState.CLOSED

    def test_cleanup_removes_all_state(self, registry):
        """cleanup_worker drops breaker, retry, timeout, and validator state."""
        registry.get_circuit_breaker("alpha")
        registry.set_retry_policy("alpha", RetryPolicy(max_retries=3))
        registry.set_timeout_guard("alpha", TimeoutGuard(default_timeout=1.0))
        registry.set_output_validator("alpha", OutputValidator(schema={"type": "object"}))

        registry.cleanup_worker("alpha")
        assert "alpha" not in registry._circuit_breakers
        assert "alpha" not in registry._retry_policies
        assert "alpha" not in registry._timeout_guards
        assert "alpha" not in registry._output_validators
        assert registry.get_retry_policy("alpha") is registry._default_retry_policy


class TestRetryPolicyRegistry:
    """Per-worker retry policy management."""

    def test_default_policy_when_unset(self, registry):
        """Workers without a custom policy get the zero-retry default."""
        assert registry.get_retry_policy("alpha").max_retries == 0

    def test_set_and_get(self, registry):
        """A custom policy is returned for the configured worker only."""
        policy = RetryPolicy(max_retries=4)
        registry.set_retry_policy("alpha", policy)
        assert registry.get_retry_policy("alpha") is policy
        assert registry.get_retry_policy("beta").max_retries == 0


class TestTimeoutGuardRegistry:
    """Per-worker timeout guard management."""

    def test_default_guard_when_unset(self, registry):
        """Workers without a custom guard get the shared default."""
        assert registry.get_timeout_guard("alpha") is registry._default_timeout_guard

    def test_task_timeout_creates_dedicated_guard(self, registry):
        """A positive task_timeout yields a fresh guard, not the default."""
        guard = registry.get_timeout_guard("alpha", task_timeout=2.5)
        assert guard.default_timeout == 2.5

    def test_task_timeout_ignores_stored_guard(self, registry):
        """Task-level timeout always wins over the stored per-worker guard."""
        registry.set_timeout_guard("alpha", TimeoutGuard(default_timeout=9.0))
        guard = registry.get_timeout_guard("alpha", task_timeout=0.5)
        assert guard.default_timeout == 0.5

    def test_stored_guard_returned_without_override(self, registry):
        """Without a task timeout, the stored guard is returned."""
        guard = TimeoutGuard(default_timeout=7.0)
        registry.set_timeout_guard("alpha", guard)
        assert registry.get_timeout_guard("alpha") is guard


class TestOutputValidatorRegistry:
    """Per-worker output validator management."""

    def test_none_when_unconfigured(self, registry):
        """No schema, no validator -> None (validation skipped)."""
        assert registry.get_output_validator("alpha") is None

    def test_task_schema_creates_validator(self, registry):
        """A task schema produces a working validator."""
        validator = registry.get_output_validator("alpha", task_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        })
        assert validator is not None
        assert validator.validate({"ok": True}) is None
        assert validator.validate({}) is not None

    def test_stored_validator_returned(self, registry):
        """A stored per-worker validator is returned when no task schema."""
        validator = OutputValidator(schema={"type": "object"})
        registry.set_output_validator("alpha", validator)
        assert registry.get_output_validator("alpha") is validator


class TestBoundedConcurrencyRegistry:
    """Bounded-concurrency cache sharing (audit M12)."""

    def test_cached_per_limit(self, registry):
        """The same limit returns the same semaphore instance."""
        first = registry.get_bounded_concurrency()
        second = registry.get_bounded_concurrency()
        assert first is second
        assert first is registry.get_bounded_concurrency(task_max_concurrent=5)

    def test_task_override_gets_own_slot(self, registry):
        """A task-level limit is cached separately from the default."""
        default = registry.get_bounded_concurrency()
        task = registry.get_bounded_concurrency(task_max_concurrent=1)
        assert task is not default

    @pytest.mark.asyncio
    async def test_shared_semaphore_enforced(self, registry):
        """Concurrent callers through the shared limiter stay bounded."""
        limiter = registry.get_bounded_concurrency(task_max_concurrent=2)
        active = 0
        max_active = 0

        async def op():
            nonlocal active, max_active
            async with limiter:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.05)
                active -= 1

        await asyncio.gather(*[op() for _ in range(5)])
        assert max_active == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
