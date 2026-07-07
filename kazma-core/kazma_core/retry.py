"""Retry utilities with exponential backoff for LLM calls and tool executions.

Uses tenacity for configurable retry logic with friendly error mapping.

Usage:
    from kazma_core.retry import retry_llm_call, retry_tool_call, RETRYABLE_EXCEPTIONS

    # Decorator for LLM calls
    @retry_llm_call
    async def call_llm(...): ...

    # Decorator for tool executions
    @retry_tool_call
    async def execute_tool(...): ...
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

# ── Configuration defaults ───────────────────────────────────────────

MAX_ATTEMPTS = 3
MIN_WAIT = 2  # seconds
MAX_WAIT = 10  # seconds

RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
)

# Extended set for httpx (imported lazily to avoid hard dep)
_HTTPX_RETRYABLE: tuple[type[Exception], ...] = ()


def _get_retryable() -> tuple[type[Exception], ...]:
    """Get retryable exceptions including httpx if available."""
    global _HTTPX_RETRYABLE
    if not _HTTPX_RETRYABLE:
        try:
            import httpx

            _HTTPX_RETRYABLE = (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            )
        except ImportError:
            _HTTPX_RETRYABLE = ()
    return RETRYABLE_EXCEPTIONS + _HTTPX_RETRYABLE


# ── Config override from kazma.yaml ─────────────────────────────────


def load_retry_config() -> dict[str, Any]:
    """Load retry configuration from kazma.yaml if available."""
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        return {
            "max_attempts": store.get("retry.max_attempts", MAX_ATTEMPTS),
            "min_wait": store.get("retry.min_wait", MIN_WAIT),
            "max_wait": store.get("retry.max_wait", MAX_WAIT),
        }
    except Exception as _e:
        logger.debug("retry config load failed, using defaults: %s", _e)
        return {
            "max_attempts": MAX_ATTEMPTS,
            "min_wait": MIN_WAIT,
            "max_wait": MAX_WAIT,
        }


def _log_retry(retry_state: RetryCallState) -> None:
    """Log each retry attempt."""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    attempt = retry_state.attempt_number
    cfg = load_retry_config()
    max_att = cfg["max_attempts"]
    logger.warning(
        "Retry attempt %d/%d failed: %s",
        attempt,
        max_att,
        exc,
    )


# ── Decorators ───────────────────────────────────────────────────────


def retry_llm_call(fn: Any) -> Any:
    """Decorator: retry LLM calls with exponential backoff on network errors.

    Does NOT retry on 4xx (bad request, auth) — only on network/5xx errors.
    """
    cfg = load_retry_config()
    retryable = _get_retryable()

    return retry(
        stop=stop_after_attempt(cfg["max_attempts"]),
        wait=wait_exponential(
            multiplier=1,
            min=cfg["min_wait"],
            max=cfg["max_wait"],
        ),
        retry=retry_if_exception_type(retryable),
        before_sleep=_log_retry,
        reraise=True,
    )(fn)


def retry_tool_call(fn: Any) -> Any:
    """Decorator: retry tool executions with exponential backoff on network errors.

    Does NOT retry on tool logic errors (ValueError, TypeError, etc.).
    """
    cfg = load_retry_config()
    retryable = _get_retryable()

    return retry(
        stop=stop_after_attempt(cfg["max_attempts"]),
        wait=wait_exponential(
            multiplier=1,
            min=cfg["min_wait"],
            max=cfg["max_wait"],
        ),
        retry=retry_if_exception_type(retryable),
        before_sleep=_log_retry,
        reraise=True,
    )(fn)


# ── Friendly error mapping ───────────────────────────────────────────


def _extract_http_status_code(exc: Exception) -> int | None:
    """Extract HTTP status code from an exception or its cause chain."""
    current: BaseException | None = exc
    visited: set[int] = set()

    while current is not None and id(current) not in visited:
        visited.add(id(current))

        response = getattr(current, "response", None)
        status_code = getattr(response, "status_code", None)
        if isinstance(status_code, int):
            return status_code

        message = str(current)
        if "401" in message:
            return 401
        if "403" in message:
            return 403

        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

    return None


def friendly_llm_error(exc: Exception) -> str:
    """Map LLM call failures to user-friendly messages after retries exhausted."""
    status_code = _extract_http_status_code(exc)
    if status_code in (401, 403):
        return (
            "The model request was rejected due to an invalid or missing API key. "
            "Go to Settings > Models/Providers and update your credentials."
        )
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
        return "The model service is unavailable. Please try again in a moment."
    exc_name = type(exc).__name__
    if "ConnectError" in exc_name or "TimeoutException" in exc_name:
        return "The model service is unavailable. Please try again in a moment."
    if "RemoteProtocolError" in exc_name:
        return "The model service returned an unexpected response. Please try again."
    return f"An error occurred while contacting the model: {exc}"


def friendly_tool_error(exc: Exception) -> str:
    """Map tool execution failures to user-friendly messages."""
    if isinstance(exc, ConnectionError):
        return "Error: Could not connect to the service. Check your internet connection."
    if isinstance(exc, TimeoutError):
        return "Error: The request timed out. Please try again."
    return f"Error: {exc}"
