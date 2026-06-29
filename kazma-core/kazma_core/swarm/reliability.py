"""Reliability layer for swarm worker dispatch.

Provides two core components:

* **RetryPolicy** -- configurable retry with exponential backoff, jitter, and
  transient-failure detection.
* **CircuitBreaker** -- per-worker failure tracking with closed/open/half-open
  state machine and manual reset support.

Both are designed for use inside :class:`kazma_core.swarm.engine.SwarmEngine`
dispatch paths.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------


@dataclass
class RetryPolicy:
    """Retry policy with exponential backoff and optional jitter.

    Args:
        max_retries: Maximum number of retry attempts after the initial call.
        base_delay:  Base delay in seconds before the first retry.
        max_delay:   Maximum delay cap in seconds.
        jitter:      If ``True``, adds random jitter of 0--25 % of ``base_delay``.
    """

    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True

    # ------------------------------------------------------------------
    # Delay computation
    # ------------------------------------------------------------------

    def compute_delay_no_jitter(self, attempt: int) -> float:
        """Return the exponential backoff delay without jitter.

        Args:
            attempt: 1-based attempt number (1 = first retry).
        """
        delay = self.base_delay * (2 ** (attempt - 1))
        return min(delay, self.max_delay)

    def compute_delay(self, attempt: int) -> float:
        """Return the exponential backoff delay, optionally with jitter.

        Jitter adds a random value in ``[0, 0.25 * base_delay]``.

        Args:
            attempt: 1-based attempt number (1 = first retry).
        """
        base = self.compute_delay_no_jitter(attempt)
        if self.jitter:
            jitter_amount = random.uniform(0, self.base_delay * 0.25)
            return min(base + jitter_amount, self.max_delay * 1.25)
        return base

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute_with_retry(
        self,
        fn: Callable[..., Awaitable[Any]],
        *,
        worker_name: str = "",
    ) -> dict[str, Any]:
        """Execute ``fn`` with retry semantics.

        Retries on:
        * Exceptions raised by ``fn``
        * Return values with ``status == "error"`` or ``status == "timeout"``

        Returns the first successful result or the final error after exhausting
        retries.  The returned dict always contains ``status`` and ``error``
        keys (plus ``output`` on success).
        """
        last_error: str | None = None
        last_result: dict[str, Any] | None = None
        total_attempts = self.max_retries + 1

        for attempt in range(1, total_attempts + 1):
            try:
                result = await fn()
            except Exception as exc:
                last_error = str(exc)[:500]
                last_result = None
                logger.debug(
                    "[RetryPolicy] worker=%s attempt=%d/%d raised %s",
                    worker_name,
                    attempt,
                    total_attempts,
                    last_error,
                )
            else:
                # If fn returns a dict, check status
                if isinstance(result, dict):
                    status = result.get("status", "")
                    if status in ("success",):
                        return result
                    last_error = result.get("error") or f"Worker '{worker_name}' returned status={status}"
                    last_result = result
                    logger.debug(
                        "[RetryPolicy] worker=%s attempt=%d/%d returned status=%s",
                        worker_name,
                        attempt,
                        total_attempts,
                        status,
                    )
                else:
                    # Non-dict return treated as success
                    return {
                        "worker": worker_name,
                        "task_id": "",
                        "status": "success",
                        "output": result if isinstance(result, str) else str(result),
                        "error": None,
                    }

            # Backoff before next attempt (skip after last)
            if attempt < total_attempts:
                delay = self.compute_delay(attempt)
                logger.debug(
                    "[RetryPolicy] worker=%s backing off %.3fs before attempt %d",
                    worker_name,
                    delay,
                    attempt + 1,
                )
                await asyncio.sleep(delay)

        # All retries exhausted — pass through original result dict if available.
        logger.warning(
            "[RetryPolicy] worker=%s exhausted %d retries after %d attempts",
            worker_name,
            self.max_retries,
            total_attempts,
        )
        if last_result is not None:
            return last_result
        return {
            "worker": worker_name,
            "task_id": "",
            "status": "error",
            "output": "",
            "error": f"Worker '{worker_name}' failed after {total_attempts} retry attempts. Last error: {last_error}",
        }


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitState(StrEnum):
    """States for the circuit breaker state machine."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half-open"


class CircuitBreakerOpenError(Exception):
    """Raised when a dispatch is attempted against an open circuit breaker."""

    def __init__(self, worker_name: str) -> None:
        self.worker_name = worker_name
        super().__init__(
            f"Circuit breaker is open for worker '{worker_name}'. "
            f"Dispatch rejected. Wait for cooldown or reset manually."
        )


@dataclass
class CircuitBreaker:
    """Per-worker circuit breaker with closed/open/half-open states.

    Args:
        failure_threshold:  Consecutive failures before tripping to ``open``.
        cooldown_seconds:   Seconds in ``open`` before transitioning to
                            ``half-open`` for a probe attempt.
    """

    failure_threshold: int = 5
    cooldown_seconds: float = 60.0

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False, repr=False)
    consecutive_failures: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # State access
    # ------------------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        """Return the current state, auto-transitioning open -> half-open."""
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.cooldown_seconds:
                logger.debug(
                    "[CircuitBreaker] cooldown elapsed (%.1fs >= %.1fs), transitioning to half-open",
                    elapsed,
                    self.cooldown_seconds,
                )
                self._state = CircuitState.HALF_OPEN
        return self._state

    # ------------------------------------------------------------------
    # Probe gating
    # ------------------------------------------------------------------

    def allow_probe(self) -> bool:
        """Return ``True`` if a dispatch is allowed.

        * ``closed`` -- always allowed
        * ``half-open`` -- allowed (single probe)
        * ``open`` -- rejected
        """
        current = self.state
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return True
        return False

    def check_or_raise(self, worker_name: str) -> None:
        """Raise :class:`CircuitBreakerOpenError` if the breaker is open."""
        if not self.allow_probe():
            raise CircuitBreakerOpenError(worker_name)

    # ------------------------------------------------------------------
    # Recording outcomes
    # ------------------------------------------------------------------

    def record_success(self) -> None:
        """Record a successful dispatch.

        * ``closed`` -- resets consecutive failure counter
        * ``half-open`` -- transitions to ``closed`` and resets counter
        """
        # Use the property accessor to trigger open -> half-open auto-transition.
        current = self.state
        self.consecutive_failures = 0
        if current == CircuitState.HALF_OPEN:
            logger.info("[CircuitBreaker] half-open probe succeeded, closing breaker")
            self._state = CircuitState.CLOSED
            self._opened_at = None

    def record_failure(self) -> None:
        """Record a failed dispatch.

        * ``closed`` -- increments counter; trips to ``open`` at threshold
        * ``half-open`` -- trips back to ``open``
        """
        # Use the property accessor to trigger open -> half-open auto-transition.
        current = self.state
        if current == CircuitState.HALF_OPEN:
            logger.warning("[CircuitBreaker] half-open probe failed, re-opening breaker")
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            return

        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            logger.warning(
                "[CircuitBreaker] threshold reached (%d >= %d), tripping to open",
                self.consecutive_failures,
                self.failure_threshold,
            )
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    # ------------------------------------------------------------------
    # Manual reset
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Manually reset the breaker to ``closed``.

        Clears the failure counter and transitions from any state to ``closed``.
        """
        logger.info("[CircuitBreaker] manual reset to closed")
        self._state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self._opened_at = None

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of this breaker."""
        return {
            "state": self.state.value,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }
