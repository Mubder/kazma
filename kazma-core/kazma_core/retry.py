"""Retry configuration + friendly error mapping.

The live retry loops are owned by the callers (the supervisor's
``_call_llm_with_retry`` and the tool registry's backoff) — they read
``load_retry_config()`` for their budgets. There are deliberately NO
tenacity decorators here anymore: ``retry_llm_call`` / ``retry_tool_call``
were exported but never applied anywhere, and a future caller wrapping a
call the supervisor already retries would double-retry with the wrong
classification (removed in audit follow-up).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

__all__ = ["MAX_ATTEMPTS", "MAX_WAIT", "MIN_WAIT", "RETRYABLE_EXCEPTIONS", "friendly_llm_error", "friendly_tool_error", "load_retry_config"]

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


# ── Friendly error mapping ───────────────────────────────────────────

# Auth-status detection from exception TEXT must be pattern-anchored: a
# bare ``"401" in message`` matched request ids, byte counts, and model
# names that happen to contain the digits, misreporting unrelated failures
# as invalid-API-key.
_STATUS_TEXT_RE = re.compile(r"(?:HTTP|status[ _-]?code|Error)\D{0,4}(401|403)\b", re.IGNORECASE)


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

        match = _STATUS_TEXT_RE.search(str(current))
        if match:
            return int(match.group(1))

        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)

    return None


def friendly_llm_error(exc: Exception) -> str:
    """Map LLM call failures to user-friendly messages after retries exhausted.

    Prefixed with ``⚠️`` so a failure is never mistaken for a normal model
    reply. Honors the ``transient`` flag on :class:`LLMError` to give an
    actionable hint: transient failures invite a retry, permanent failures
    point at the underlying cause.
    """
    status_code = _extract_http_status_code(exc)
    if status_code in (401, 403):
        return (
            "⚠️ The model request was rejected due to an invalid or missing API key. "
            "Go to Settings > Models/Providers and update your credentials."
        )

    is_transient = bool(getattr(exc, "transient", False))
    # Inspect the cause chain too — some LLMErrors wrap a network exception.
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if not is_transient and cause is not None:
        is_transient = bool(getattr(cause, "transient", False)) or isinstance(
            cause, (ConnectionError, TimeoutError, asyncio.TimeoutError)
        )

    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)) or is_transient:
        return (
            "⚠️ I lost the connection to the model mid-turn (it was retried "
            "automatically but kept failing). Please send your message again — "
            "transient network/rate-limit blips usually clear on the next turn."
        )
    exc_name = type(exc).__name__
    if "ConnectError" in exc_name or "TimeoutException" in exc_name:
        return (
            "⚠️ The model service is unavailable. Please try again in a moment."
        )
    if "RemoteProtocolError" in exc_name or "ReadError" in exc_name:
        return (
            "⚠️ The connection to the model dropped mid-response. Please try again."
        )
    # Permanent content/schema error — surface a short reason so the user
    # can act (e.g. switch model, drop an attachment, fix config).
    msg = str(exc)
    # Trim very long provider error bodies for readability.
    if len(msg) > 240:
        msg = msg[:240] + "…"
    return f"⚠️ The model rejected the request: {msg}"


def friendly_tool_error(exc: Exception) -> str:
    """Map tool execution failures to user-friendly messages."""
    if isinstance(exc, ConnectionError):
        return "Error: Could not connect to the service. Check your internet connection."
    if isinstance(exc, TimeoutError):
        return "Error: The request timed out. Please try again."
    return f"Error: {exc}"
