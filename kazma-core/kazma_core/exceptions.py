"""Common exception hierarchy for Kazma.

Provides structured error types with user-safe messages for API responses.
All exceptions should inherit from KazmaError for consistent handling.
"""

from __future__ import annotations

from typing import Any


class KazmaError(Exception):
    """Base exception for all Kazma errors.
    
    Attributes:
        message: Technical error message for logs.
        user_message: Safe message for end-user display.
        details: Optional structured details for debugging.
    """
    
    def __init__(
        self,
        message: str,
        user_message: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.user_message = user_message or "An error occurred. Please try again."
        self.details = details or {}
    
    def __str__(self) -> str:
        return self.message


class ConfigError(KazmaError):
    """Configuration-related errors (missing keys, invalid values, migration failures)."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Configuration error. Please check settings.",
            details,
        )


class SwarmError(KazmaError):
    """Swarm orchestration errors (dispatch failures, worker errors, pattern errors)."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Swarm task failed. Please try again.",
            details,
        )


class PlatformError(KazmaError):
    """Platform adapter errors (Telegram, Discord, Slack API failures)."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Platform communication error. Please try again.",
            details,
        )


class ValidationError(KazmaError):
    """Input validation errors (invalid chat_id, malformed task, etc.)."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Invalid input. Please check your request.",
            details,
        )


class TimeoutError(KazmaError):
    """Operation timeout errors (swarm dispatch, API calls, etc.)."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Request timed out. Please try again.",
            details,
        )


class HITLError(KazmaError):
    """Human-in-the-Loop approval errors (denied, expired, invalid)."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Action requires approval. Please wait for authorization.",
            details,
        )


class CircuitBreakerOpenError(KazmaError):
    """Circuit breaker is open - too many failures."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Service temporarily unavailable. Please try again later.",
            details,
        )


class WorkerNotFoundError(KazmaError):
    """Requested worker not registered in swarm."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Requested worker not available.",
            details,
        )


class NoCapableWorkersError(KazmaError):
    """No workers matched the task requirements."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "No suitable workers available for this task.",
            details,
        )


class SafetyGateError(KazmaError):
    """Safety gate blocked a dangerous operation."""
    
    def __init__(self, message: str, user_message: str | None = None, details: dict | None = None):
        super().__init__(
            message,
            user_message or "Operation blocked by safety policy.",
            details,
        )


# ─── Error Sanitization ─────────────────────────────────────────────────

def sanitize_error(exc: Exception) -> str:
    """Convert any exception to a user-safe message.
    
    Never exposes internal details, stack traces, or sensitive data.
    """
    if isinstance(exc, KazmaError):
        return exc.user_message
    
    msg = str(exc).lower()
    
    # Common patterns - map to safe messages
    if "timeout" in msg:
        return "⚠️ Request timed out. Please try again."
    if "unauthorized" in msg or "401" in msg:
        return "⚠️ Authentication failed. Check bot configuration."
    if "forbidden" in msg or "403" in msg:
        return "⚠️ Access denied. Check permissions."
    if "not found" in msg or "404" in msg:
        return "⚠️ Resource not found."
    if "rate limit" in msg or "429" in msg:
        return "⚠️ Too many requests. Please wait and try again."
    if "connection" in msg or "network" in msg:
        return "⚠️ Network error. Please check connection."
    if "database" in msg or "sqlite" in msg:
        return "⚠️ Storage error. Please try again."
    
    # Generic fallback - no internal details
    return "⚠️ An error occurred. Please try again later."


def to_kazma_error(exc: Exception) -> KazmaError:
    """Wrap any exception in a KazmaError if not already one."""
    if isinstance(exc, KazmaError):
        return exc
    return KazmaError(str(exc))