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
    "redact_secrets",
    "safe_error",
    "described_error",
    "validation_error",
    "refusal_message",
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

_PATH_FRAGMENTS = r"""
        \b[A-Za-z]:[\\/](?![\\/])[^\s'"]*   # Windows absolute paths (not the "s://" of a URL)
      | /(?:home|Users|root|etc|var|opt)/[^\s'"]*   # POSIX absolute paths
"""
#: Secrets recognised by what surrounds them: a JSON member or ``name=value``
#: whose name is a credential's, URL userinfo. Credentials recognised by their
#: own shape (an Authorization header's, ``sk-``, ``xapp-``, ``ya29.``, a
#: JWT...) are ``security.token_shapes.TOKEN_SHAPES``, the table every masker
#: reads (2026-11-15: this list knew five of them, the maskers none).
_SECRET_FRAGMENTS = r"""
        "[^"\s]*(?:password|passwd|secret|token(?!s)|api[_-]?key|private[_-]?key|authorization)[^"\s]*"
          \s*:\s*"[^"]*"                                # a JSON member ("password": "x")
      | (?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*\S+
      | [a-z][a-z0-9+.-]*://[^/\s:@'"]+:[^@\s/'"]+@  # URL userinfo (a DSN's password)
"""
_REDACTED = "<redacted>"
#: Fragments that must never reach a client even in verbose mode.
_REDACT_PATTERN = re.compile("(" + _PATH_FRAGMENTS + "|" + _SECRET_FRAGMENTS + ")", re.IGNORECASE | re.VERBOSE)
#: The secrets alone, for a message whose path is the answer (below).
_REDACT_SECRETS = re.compile("(" + _SECRET_FRAGMENTS + ")", re.IGNORECASE | re.VERBOSE)


class _Redactor:
    """``_REDACT.sub(...)``: the paths and context-shaped secrets, then every
    token shape (``security.token_shapes``)."""

    def __init__(self, pattern: re.Pattern[str]) -> None:
        self._pattern = pattern

    def sub(self, replacement: str, text: str) -> str:
        from kazma_core.security.token_shapes import mask_tokens

        return mask_tokens(self._pattern.sub(replacement, str(text)), replacement)


_REDACT = _Redactor(_REDACT_PATTERN)


def redact_secrets(text: str) -> str:
    """*text* with every secret-shaped fragment ``<redacted>``, paths kept.

    For a message the operator reads to fix their own setup -- an MCP server
    that would not start ("No such file: C:\\...\\npx.cmd") -- where
    :func:`validation_error`'s path redaction would remove the answer. A key
    is still never shown: an HTTP failure quotes its URL, query and all; a
    token is found by its shape too (``security.token_shapes``).
    """
    from kazma_core.security.token_shapes import mask_tokens

    return mask_tokens(_REDACT_SECRETS.sub(_REDACTED, str(text)), _REDACTED)

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


def described_error(
    sentence: str, exc: BaseException, *, level: int = logging.ERROR,
) -> ErrorInfo:
    """*sentence* with *exc*'s stable code and a reference, for a page that
    shows the error to a person.

    "Could not resolve the clone URL (upstream_unavailable, ref: 1a2b3c4d)."
    The real exception is logged under the reference, as :func:`safe_error`
    logs it; its text -- a path, a SQL statement, a host name -- never reaches
    the caller. *level* is the log level: ``logging.WARNING`` for a probe a
    page polls, so an outage does not fill the log with tracebacks (they are
    logged at ``ERROR`` and above).
    """
    info = safe_error(exc, log=False)
    logger.log(
        level, "[error] ref=%s code=%s (%s) %s: %s",
        info.ref, info.code, sentence, type(exc).__name__, exc,
        exc_info=exc if level >= logging.ERROR else None,
    )
    text = f"{sentence.rstrip(' .')} ({info.code}, ref: {info.ref})."
    if verbose_errors():
        detail = _REDACT.sub("<redacted>", str(exc))[:_MAX_VERBOSE_CHARS]
        if detail:
            text = f"{text} {detail}"
    return ErrorInfo(text, code=info.code, ref=info.ref)


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


#: The catalog key a refusal with no sentence of its own answers (never
#: expected: every class a route answers this way carries one, see
#: :func:`refusal_message`); worded in English and Arabic.
REFUSED_KEY = "common.request_refused"


def _refused_sentence() -> str:
    """:data:`REFUSED_KEY` in the reader's language. A route asks this from a
    request, where the UI's catalog is loaded; anywhere else the key itself
    is the answer, a code a page can word."""
    try:
        from kazma_ui.i18n import current_language, t
    except ImportError:
        return REFUSED_KEY
    return t(REFUSED_KEY, current_language())


def refusal_message(exc: BaseException, *, max_chars: int = _MAX_VERBOSE_CHARS) -> str:
    """The sentence one of Kazma's own refusals carries, for the page.

    Read from the refusal's ``message`` field -- the sentence Kazma wrote
    when it refused -- and never from the exception's text (``str(exc)``),
    which a subclass, a wrapper or a cause can change. Redacted and cut as
    :func:`validation_error` is, so a sentence that names a path or holds a
    token shape does not carry it out.

    A route that answers a typed refusal (``PeopleRefused``, ``LinkRefused``,
    ``SpaceMoveRefused``...) uses this; ``tests/test_codeql_triage_2026_11_26.py``
    holds every class a route passes here to carrying the field. One without
    it answers the catalog's :data:`REFUSED_KEY` in the reader's language,
    said at WARNING.
    """
    message = getattr(exc, "message", None)
    if not isinstance(message, str):
        logger.warning("[errors] %s carries no message field: the page was told only that it was refused",
                       type(exc).__name__)
        return _refused_sentence()
    text = _REDACT.sub("<redacted>", message).strip()
    return text[:max_chars] if text else _refused_sentence()


def error_payload(exc: BaseException, **extra: Any) -> dict[str, Any]:
    """Structured error body: ``{"ok": False, "error": …, "code": …, "ref": …}``."""
    info = safe_error(exc)
    return {"ok": False, "error": str(info), "code": info.code, "ref": info.ref, **extra}
