"""Reliability layer for swarm worker dispatch.

Provides five core components:

* **RetryPolicy** -- configurable retry with exponential backoff, jitter, and
  transient-failure detection.
* **CircuitBreaker** -- per-worker failure tracking with closed/open/half-open
  state machine and manual reset support.
* **TimeoutGuard** -- per-task timeout enforcement via ``asyncio.wait_for`` with
  configurable on-timeout behaviors (fail, retry, skip).
* **OutputValidator** -- Pydantic / dict / JSON-schema validation on worker
  output before acceptance.
* **BoundedConcurrency** -- asyncio.Semaphore wrapper for limiting parallel
  dispatches across fan-out, broadcast, and consult patterns.

All components are designed for use inside
:class:`kazma_core.swarm.engine.SwarmEngine` dispatch paths.
"""

from __future__ import annotations

import asyncio
import json
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


# ---------------------------------------------------------------------------
# TimeoutGuard
# ---------------------------------------------------------------------------


class TimeoutGuardError(Exception):
    """Raised when a worker exceeds its configured timeout."""


@dataclass
class TimeoutGuard:
    """Per-task timeout enforcement via ``asyncio.wait_for``.

    Wraps a worker coroutine with a timeout.  When the timeout fires the
    worker's coroutine is cleanly cancelled and a result dict is returned
    whose ``status`` is ``"timeout"`` and whose ``retry`` / ``skipped``
    flags indicate how the caller (typically :class:`RetryPolicy`) should
    proceed.

    Args:
        default_timeout: Default timeout in seconds applied when the caller
            does not supply an explicit ``timeout`` value.  Must be > 0.
        on_timeout: Behaviour when the timeout fires.

            ``"fail"``   -- terminal failure; the result has
                ``status="timeout"`` and no retry hint.
            ``"retry"``  -- signals that the timeout should count against
                the retry budget (``result["retry"] = True``).
            ``"skip"``   -- signals that the worker should be skipped
                (``result["skipped"] = True``).
    """

    default_timeout: float = 300.0
    on_timeout: str = "fail"

    def __post_init__(self) -> None:
        if self.default_timeout <= 0:
            raise ValueError(
                f"default_timeout must be > 0, got {self.default_timeout}"
            )
        if self.on_timeout not in ("fail", "retry", "skip"):
            raise ValueError(
                f"on_timeout must be 'fail', 'retry', or 'skip', got '{self.on_timeout}'"
            )

    async def execute(
        self,
        fn: Callable[..., Awaitable[Any]],
        *,
        timeout: float | None = None,
        worker_name: str = "",
    ) -> dict[str, Any]:
        """Execute *fn* with a timeout.

        Args:
            fn: The async callable to execute (typically a worker dispatch).
            timeout: Override the default timeout.  Must be > 0 if provided.
            worker_name: Name of the worker for error messages.

        Returns:
            On success, whatever *fn* returns.
            On timeout, a dict with ``status="timeout"`` and appropriate
            ``retry`` / ``skipped`` flags.
        """
        effective_timeout = timeout if timeout is not None else self.default_timeout
        if effective_timeout <= 0:
            raise ValueError(
                f"timeout must be > 0, got {effective_timeout}"
            )

        try:
            return await asyncio.wait_for(fn(), timeout=effective_timeout)
        except TimeoutError:
            logger.warning(
                "[TimeoutGuard] worker '%s' timed out after %.2fs (on_timeout=%s)",
                worker_name,
                effective_timeout,
                self.on_timeout,
            )
            result: dict[str, Any] = {
                "worker": worker_name,
                "task_id": "",
                "status": "timeout",
                "output": "",
                "error": (
                    f"Worker '{worker_name}' timed out after {effective_timeout:g}s."
                ),
            }
            if self.on_timeout == "retry":
                result["retry"] = True
            elif self.on_timeout == "skip":
                result["skipped"] = True
            return result


# ---------------------------------------------------------------------------
# OutputValidator
# ---------------------------------------------------------------------------


class OutputValidator:
    """Validate worker output against a schema before acceptance.

    Supports three schema forms:

    * **Pydantic BaseModel subclass** -- validated via ``model_validate``.
    * **JSON Schema dict** (has ``"type": "object"`` or ``"properties"``)
      -- validated via ``jsonschema.validate`` if available, otherwise a
      lightweight built-in check.
    * **Simple type dict** (e.g. ``{"name": "str", "age": "int"}``) -- each
      key must be present and its value must match the declared Python type
      name.

    When the schema expects a structured type and the output is a string,
    the validator attempts to parse the string as JSON first.

    Args:
        schema: The schema to validate against.  ``None`` or ``{}`` means
            validation is skipped entirely.
    """

    def __init__(self, schema: Any | None = None) -> None:
        self.schema = schema

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, output: Any) -> str | None:
        """Validate *output* against the configured schema.

        Returns:
            ``None`` if the output is valid.
            A human-readable error string describing the validation failure
            if the output is invalid.
        """
        if not self.schema:
            return None

        # If output is a string and the schema expects a structure, parse JSON.
        parsed_output = self._maybe_parse_json(output)

        # Try Pydantic BaseModel first.
        if self._is_pydantic_model(self.schema):
            return self._validate_pydantic(self.schema, parsed_output)

        # Try JSON Schema dict.
        if self._is_json_schema(self.schema):
            return self._validate_json_schema(self.schema, parsed_output)

        # Fall back to simple type dict.
        if isinstance(self.schema, dict):
            return self._validate_dict_schema(self.schema, parsed_output)

        return None

    # ------------------------------------------------------------------
    # Schema detection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_pydantic_model(schema: Any) -> bool:
        """Return True if *schema* is a Pydantic BaseModel subclass."""
        try:
            import pydantic

            return isinstance(schema, type) and issubclass(schema, pydantic.BaseModel)
        except ImportError:
            return False

    @staticmethod
    def _is_json_schema(schema: Any) -> bool:
        """Return True if *schema* looks like a JSON Schema object."""
        if not isinstance(schema, dict):
            return False
        return "type" in schema or "properties" in schema or "$schema" in schema

    # ------------------------------------------------------------------
    # JSON parsing
    # ------------------------------------------------------------------

    def _maybe_parse_json(self, output: Any) -> Any:
        """If *output* is a string and the schema expects a structure, parse."""
        if not isinstance(output, str):
            return output
        if not self.schema:
            return output
        # Only attempt JSON parsing if schema expects an object/array.
        if self._is_pydantic_model(self.schema):
            try:
                return json.loads(output)
            except (json.JSONDecodeError, ValueError):
                return output
        if isinstance(self.schema, dict):
            schema_type = self.schema.get("type", "")
            # JSON schema with explicit type/properties.
            if schema_type in ("object", "array") or "properties" in self.schema:
                try:
                    return json.loads(output)
                except (json.JSONDecodeError, ValueError):
                    return output
            # Simple type dict (e.g. {"name": "str"}) also expects a dict.
            if not self._is_json_schema(self.schema):
                try:
                    return json.loads(output)
                except (json.JSONDecodeError, ValueError):
                    return output
        return output

    # ------------------------------------------------------------------
    # Pydantic validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_pydantic(model_class: Any, output: Any) -> str | None:
        """Validate *output* against a Pydantic BaseModel subclass."""
        try:
            model_class.model_validate(output)
            return None
        except Exception as exc:
            return f"Pydantic validation failed: {exc}"

    # ------------------------------------------------------------------
    # JSON Schema validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_json_schema(schema: dict[str, Any], output: Any) -> str | None:
        """Validate *output* against a JSON Schema dict."""
        # Try the jsonschema library first if available.
        try:
            import jsonschema

            jsonschema.validate(output, schema)
            return None
        except ImportError:
            pass  # Fall through to built-in.
        except Exception as exc:
            return f"JSON schema validation failed: {exc}"

        # Lightweight built-in check: verify required fields and basic types.
        return OutputValidator._builtin_json_schema_check(schema, output)

    @staticmethod
    def _builtin_json_schema_check(
        schema: dict[str, Any],
        output: Any,
    ) -> str | None:
        """Perform a minimal JSON Schema validation without external deps."""
        schema_type = schema.get("type")
        if schema_type == "object" or "properties" in schema:
            if not isinstance(output, dict):
                return (
                    f"Expected object, got {type(output).__name__}."
                )
            required = schema.get("required", [])
            for field_name in required:
                if field_name not in output:
                    return f"Missing required field: '{field_name}'"
            properties = schema.get("properties", {})
            for field_name, field_schema in properties.items():
                if field_name not in output:
                    continue
                expected_type = field_schema.get("type")
                if expected_type and not _json_type_matches(
                    output[field_name], expected_type
                ):
                    return (
                        f"Field '{field_name}': expected {expected_type}, "
                        f"got {type(output[field_name]).__name__}"
                    )
        return None

    # ------------------------------------------------------------------
    # Simple dict schema validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_dict_schema(
        schema: dict[str, Any],
        output: Any,
    ) -> str | None:
        """Validate *output* against a simple type dict schema.

        Each key in the schema must be present in the output, and the
        output value's type name must match the schema value (a string
        like ``"str"``, ``"int"``, ``"float"``, ``"bool"``, ``"list"``,
        ``"dict"``).
        """
        if not isinstance(output, dict):
            return f"Expected dict, got {type(output).__name__}."

        for field_name, expected_type_name in schema.items():
            if field_name not in output:
                return f"Missing required field: '{field_name}'"
            actual_value = output[field_name]
            if not _python_type_matches(actual_value, str(expected_type_name)):
                return (
                    f"Field '{field_name}': expected {expected_type_name}, "
                    f"got {type(actual_value).__name__}"
                )
        return None


def _json_type_matches(value: Any, json_type: str) -> bool:
    """Check if *value* matches a JSON Schema type string."""
    mapping: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "array": (list, tuple),
        "object": (dict,),
        "null": (type(None),),
    }
    expected_types = mapping.get(json_type)
    if expected_types is None:
        return True  # Unknown type -> accept anything.
    return isinstance(value, expected_types)


def _python_type_matches(value: Any, type_name: str) -> bool:
    """Check if *value* matches a Python type name string."""
    mapping: dict[str, type] = {
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        "none": type(None),
        "nonetype": type(None),
    }
    expected_type = mapping.get(type_name.strip().lower())
    if expected_type is None:
        return True  # Unknown type -> accept anything.
    return isinstance(value, expected_type)


# ---------------------------------------------------------------------------
# BoundedConcurrency
# ---------------------------------------------------------------------------


class BoundedConcurrency:
    """Semaphore-based concurrency limiter for parallel worker dispatches.

    Use as an async context manager to acquire and release the semaphore::

        bc = BoundedConcurrency(max_concurrent=3)
        async with bc:
            await do_work()

    The semaphore is released when the block exits, whether normally or via
    an exception, preventing deadlocks from failed or timed-out workers.

    Args:
        max_concurrent: Maximum number of workers that may execute
            simultaneously.  Must be >= 1.  Defaults to 5.
    """

    def __init__(self, max_concurrent: int = 5) -> None:
        if max_concurrent < 1:
            raise ValueError(
                f"max_concurrent must be >= 1, got {max_concurrent}"
            )
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def __aenter__(self) -> BoundedConcurrency:
        """Acquire the semaphore."""
        await self._semaphore.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Release the semaphore, even if an exception was raised."""
        self._semaphore.release()
