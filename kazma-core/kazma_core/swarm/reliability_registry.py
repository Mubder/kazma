"""ReliabilityRegistry — extracted from SwarmEngine (P2-1 refactor).

Owns the per-worker reliability configuration: circuit breakers, retry
policies, timeout guards, output validators, and bounded concurrency.
SwarmEngine delegates to this module to keep the god class focused on
dispatch orchestration.

The registry is self-contained — it does not reference the engine's
worker dispatch or task management. The only external dependency is a
``worker_names`` callable used by ``get_all_circuit_breaker_status()``
to enumerate workers (passed by the engine).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from kazma_core.swarm.reliability import (
    BoundedConcurrency,
    CircuitBreaker,
    OutputValidator,
    RetryPolicy,
    TimeoutGuard,
)

logger = logging.getLogger(__name__)


class ReliabilityRegistry:
    """Per-worker reliability configuration: breakers, retries, timeouts, validators.

    Args:
        worker_names: A callable returning the current list of registered
            worker names (used by ``get_all_circuit_breaker_status``).
        default_max_concurrent: Default bounded-concurrency limit.
    """

    def __init__(
        self,
        worker_names: Callable[[], list[str]],
        default_max_concurrent: int = 5,
    ) -> None:
        self._worker_names = worker_names
        self._default_max_concurrent = default_max_concurrent

        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._retry_policies: dict[str, RetryPolicy] = {}
        self._default_retry_policy = RetryPolicy(max_retries=0)
        self._timeout_guards: dict[str, TimeoutGuard] = {}
        self._default_timeout_guard = TimeoutGuard()
        self._output_validators: dict[str, OutputValidator] = {}

    # ── Worker removal cleanup ──────────────────────────────────────

    def cleanup_worker(self, worker_name: str) -> None:
        """Remove all reliability state for a worker (call on unregister)."""
        self._circuit_breakers.pop(worker_name, None)
        self._retry_policies.pop(worker_name, None)
        self._timeout_guards.pop(worker_name, None)
        self._output_validators.pop(worker_name, None)

    # ── Circuit breakers ────────────────────────────────────────────

    def get_circuit_breaker(self, worker_name: str) -> CircuitBreaker:
        """Return (or create) the circuit breaker for a worker."""
        if worker_name not in self._circuit_breakers:
            self._circuit_breakers[worker_name] = CircuitBreaker()
        return self._circuit_breakers[worker_name]

    def reset_circuit_breaker(self, worker_name: str) -> CircuitBreaker:
        """Manually reset a worker's circuit breaker to closed state."""
        breaker = self.get_circuit_breaker(worker_name)
        breaker.reset()
        logger.info("[Reliability] circuit breaker reset for worker '%s'", worker_name)
        return breaker

    def set_circuit_breaker_config(
        self,
        worker_name: str,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
    ) -> CircuitBreaker:
        """Create or reconfigure a per-worker circuit breaker."""
        self._circuit_breakers[worker_name] = CircuitBreaker(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )
        return self._circuit_breakers[worker_name]

    def get_circuit_breaker_status(self, worker_name: str) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of a worker's circuit breaker."""
        breaker = self.get_circuit_breaker(worker_name)
        return breaker.to_dict()

    def get_all_circuit_breaker_status(self) -> dict[str, dict[str, Any]]:
        """Return circuit breaker status for all registered workers."""
        return {
            name: self.get_circuit_breaker(name).to_dict()
            for name in self._worker_names()
        }

    # ── Retry policies ──────────────────────────────────────────────

    def get_retry_policy(self, worker_name: str) -> RetryPolicy:
        """Return the retry policy for a worker (or the default)."""
        return self._retry_policies.get(worker_name, self._default_retry_policy)

    def set_retry_policy(
        self,
        worker_name: str,
        policy: RetryPolicy,
    ) -> None:
        """Set a per-worker retry policy."""
        self._retry_policies[worker_name] = policy

    # ── Timeout guards ──────────────────────────────────────────────

    def get_timeout_guard(
        self,
        worker_name: str,
        task_timeout: float | None = None,
    ) -> TimeoutGuard:
        """Return (or create) the timeout guard for a worker."""
        if task_timeout is not None and task_timeout > 0:
            return TimeoutGuard(default_timeout=task_timeout)
        if worker_name not in self._timeout_guards:
            self._timeout_guards[worker_name] = self._default_timeout_guard
        return self._timeout_guards[worker_name]

    def set_timeout_guard(
        self,
        worker_name: str,
        guard: TimeoutGuard,
    ) -> None:
        """Set a per-worker timeout guard."""
        self._timeout_guards[worker_name] = guard

    # ── Output validators ───────────────────────────────────────────

    def get_output_validator(
        self,
        worker_name: str,
        task_schema: dict[str, Any] | None = None,
    ) -> OutputValidator | None:
        """Return the output validator for a worker or task schema.

        Returns ``None`` when no schema is configured (validation skipped).
        """
        if task_schema is not None:
            return OutputValidator(schema=task_schema)
        return self._output_validators.get(worker_name)

    def set_output_validator(
        self,
        worker_name: str,
        validator: OutputValidator,
    ) -> None:
        """Set a per-worker output validator."""
        self._output_validators[worker_name] = validator

    # ── Bounded concurrency ─────────────────────────────────────────

    def get_bounded_concurrency(
        self,
        task_max_concurrent: int | None = None,
    ) -> BoundedConcurrency:
        """Return a BoundedConcurrency instance for the given concurrency limit.

        Task-level override takes precedence over the engine default.
        """
        limit = task_max_concurrent or self._default_max_concurrent
        return BoundedConcurrency(max_concurrent=limit)
