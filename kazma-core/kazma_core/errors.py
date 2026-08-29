"""Client-safe error reporting (audit O2).

165 API handlers returned ``str(exc)`` straight to the caller, which leaks
absolute filesystem paths, SQL fragments, driver internals, and occasionally
connection strings. It is also unhelpful to clients: a message string is not
something you can branch on.

:func:`safe_error` replaces that with a stable, machine-readable code plus a
correlation id, and logs the real exception server-side under the same id, so
an operator can join a user report to a traceback without the user ever seeing
one.

Usage::

    from kazma_core.errors import safe_error

    try:
        ...
    except Exception as exc:
        logger.exception("[memory] belief query failed")
        return {"beliefs": [], "error": safe_error(exc)}

Set ``KAZMA_VERBOSE_ERRORS=1`` in development to append the real message.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import uuid
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "ErrorInfo",
    "safe_error",
    "validation_error",
    "error_payload",
    "verbose_errors",
]

#: Exception type → stable error code. Anything unlisted becomes
#: ``internal_error``; the code is part of the API contract, the message is not.
_CODES: tuple[tuple[type[BaseException], str], ...] = (
    (PermissionError, "permission_denied"),
    (FileNotFoundError, "not_found"),
    (IsADirectoryError, "invalid_path"),
    (NotADirectoryError, "invalid_path"),
    (TimeoutError, "timeout"),
    (sqlite3.OperationalError, "database_unavailable"),
    (sqlite3.IntegrityError, "constraint_violation"),
    (sqlite3.DatabaseError, "database_error"),
    (ValueError, "invalid_input"),
    (TypeError, "invalid_input"),
    (KeyError, "invalid_input"),
    (LookupError, "not_found"),
    (ConnectionError, "upstream_unavailable"),
    (OSError, "io_error"),
)

#: Fragments that must never reach a client even in verbose mode.
_REDACT = re.compile(
    r"""(
        [A-Za-z]:[\\/][^\s'"]*        # Windows absolute paths
      | /(?:home|Users|root|etc|var|opt)/[^\s'"]*   # POSIX absolute paths
      | (?:password|secret|token|api[_-]?key)\s*[=:]\s*\S+
      | \b(?:sk|xox[baprs]|ghp|gho|ghu|ghs|AIza)[-_][A-Za-z0-9_-]{8,}
    )""",
    re.IGNORECASE | re.VERBOSE,
)

_MAX_VERBOSE_CHARS = 300


def verbose_errors() -> bool:
    """True when the operator opted into echoing real messages (dev only)."""
    return (os.environ.get("KAZMA_VERBOSE_ERRORS") or "").strip().lower() in (
        "1", "true", "on", "yes",
    )


def _code_for(exc: BaseException) -> str:
    for exc_type, code in _CODES:
        if isinstance(exc, exc_type):
            return code
    return "internal_error"


class ErrorInfo(str):
    """A client-safe error string that also carries its structured parts.

    Subclasses :class:`str` so it can drop straight into existing
    ``{"error": ...}`` payloads without changing any response shape, while
    callers that want the parts can still read ``.code`` and ``.ref``.
    """

    code: str
    ref: str

    def __new__(cls, text: str, *, code: str, ref: str) -> ErrorInfo:
        obj = super().__new__(cls, text)
        obj.code = code
        obj.ref = ref
        return obj


def safe_error(exc: BaseException, *, log: bool = True) -> ErrorInfo:
    """Return a client-safe description of *exc* and log the real one.

    Args:
        exc: The caught exception.
        log: Log the full exception under the correlation id. Pass ``False``
            only when the caller already logged it with ``logger.exception``.

    Returns:
        ``"<code> (ref: <8-hex>)"`` — safe to return to any client.
    """
    code = _code_for(exc)
    ref = uuid.uuid4().hex[:8]
    if log:
        logger.error(
            "[error] ref=%s code=%s %s: %s",
            ref, code, type(exc).__name__, exc,
            exc_info=exc,
        )
    text = f"{code} (ref: {ref})"
    if verbose_errors():
        detail = _REDACT.sub("<redacted>", str(exc))[:_MAX_VERBOSE_CHARS]
        if detail:
            text = f"{text}: {detail}"
    return ErrorInfo(text, code=code, ref=ref)


def validation_error(exc: BaseException, *, max_chars: int = _MAX_VERBOSE_CHARS) -> str:
    """Return a caller-facing message for a **4xx validation** failure.

    The counterpart to :func:`safe_error`. A ``ValueError`` raised by our own
    input validation ("chat_id must be an integer") is the answer the caller
    asked for — replacing it with an opaque code makes the API unusable. Only
    *unexpected* exceptions get the code-plus-ref treatment.

    The message is still passed through the redactor, so a validation error
    that happens to embed a path or a credential does not leak it.

    Use this for ``except ValueError`` / explicit 400/409/422 paths; use
    :func:`safe_error` for ``except Exception`` / 5xx paths.
    """
    text = _REDACT.sub("<redacted>", str(exc)).strip()
    if not text:
        return "invalid request"
    return text[:max_chars]


def error_payload(exc: BaseException, **extra: Any) -> dict[str, Any]:
    """Structured error body: ``{"ok": False, "error": …, "code": …, "ref": …}``."""
    info = safe_error(exc)
    return {"ok": False, "error": str(info), "code": info.code, "ref": info.ref, **extra}
