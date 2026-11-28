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
    "is_arabic_credential_label",
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

_REDACTED = "<redacted>"
#: Windows and POSIX paths. The trailing run is possessive, so a long path
#: cannot be re-scanned. ``(?![\\/])`` keeps the ``s://`` of a URL.
_REDACT_PATHS = re.compile(
    r"""(?ix)
    \b[A-Za-z]:[\\/](?![\\/])[^\s'"]*+
    | /(?:home|Users|root|etc|var|opt)/[^\s'"]*+
    """,
    re.IGNORECASE | re.VERBOSE,
)
#: How a value is told from an ordinary word. One rule, used by
#: :func:`redact_secrets` and by :func:`hide_prose_secrets` (the log writer
#: reads it through ``mask_urls_in_text``). A colon, an equals sign, or a
#: fullwidth colon (U+FF1A) masks the next token, and that value may sit on
#: the following line (``\s`` after the separator). ``is`` / ``هي`` / ``هو``,
#: and a label with no separator, mask the next word when it is
#: secret-shaped: an underscore, a hyphen whose piece is at most three
#: characters (``abc-def``) or that has three or more pieces
#: (``long-value-here``; a two-piece compound such as ``rotation-policy``
#: or ``reset-link`` stays), both a letter and a digit, or four or more
#: digits. A bare English ``password`` / ``secret`` / ``api_key`` leaves a
#: four-digit year (``The secret 2024 plan``). English ``PIN`` is
#: case-sensitive and masks only after a separator or a run of four or more
#: digits, so ``workspace pin skipped`` and ``turn-model pin=…`` stay. The
#: Arabic PIN labels (``الرقم السري``, ``رمز المرور``) mask the next word
#: whatever it is. The gap between a label's words, and the gap of a bare
#: or copula form, is any whitespace but a line break (``[^\S\r\n]``). A
#: JSON member whose key is a credential label is this same rule, in either
#: language. ASCII ``token:`` stays in :data:`_REDACT_SECRETS` only:
#: :func:`redact_secrets` masks ``token: expired`` and the phrase masker
#: keeps that unquoted form.
#:
#: Every pattern below is a string literal on ``re.compile``: the regex
#: sweep only sees those, and a concatenation is invisible to it. Each
#: repeat that used to re-scan (``*://``, a JSON key, a no-colon word) is
#: possessive or one forward lookahead.
_REDACT_SECRETS = re.compile(
    r"""(?ix)
    token \s*+ [=:] \s*+ \S++
    | (?<![A-Za-z0-9+.\-]) [A-Za-z] [A-Za-z0-9+.\-]{0,31}+ :// [^/\s:@'"]++ : [^@\s/'"]++ @
    """,
    re.IGNORECASE | re.VERBOSE,
)
#: Key=value, key: value, the JSON member, and the no-separator phrases, in
#: English. Arabic phrases are :data:`_ARABIC_SECRET`; Arabic JSON keys are
#: :data:`_ARABIC_JSON_SECRET`. ASCII ``token:`` is not here.
_PROSE_SECRETS = re.compile(
    r"""(?ix)
    "(?> (?: (?! password | passwd | secret | token(?!s) | api[_-]?key | private[_-]?key | authorization ) [^"\s] )*+ )
      (?: password | passwd | secret | token(?!s) | api[_-]?key | private[_-]?key | authorization )
      [^"\s]*+
      " \s*+ [:\uFF1A] \s*+ " [^"]*+ "
    | (?: password | passwd | secret | api[_-]?key )
      (?: [^\S\r\n]*+ [=:\uFF1A] \s*+ (?!\*++(?!\S))\S++
        | [^\S\r\n]++ is [^\S\r\n]++ (?:[^\s_]*+_[^\s]*+|(?=[^\s]*\d)(?=[^\s]*[^\W\d_])[^\s]++|(?=[^\s]*-)(?:[^-\s]{1,3}-[^\s]*+|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}-[^\s]*+|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}+(?!\S)|[^\s-]++(?:-[^\s-]++){2,}+)|\d{4,}+(?!\d))
        | [^\S\r\n]++ (?! (?:19|20) [0-9]{2}+ (?!\S) ) (?:[^\s_]*+_[^\s]*+|(?=[^\s]*\d)(?=[^\s]*[^\W\d_])[^\s]++|(?=[^\s]*-)(?:[^-\s]{1,3}-[^\s]*+|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}-[^\s]*+|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}+(?!\S)|[^\s-]++(?:-[^\s-]++){2,}+)|\d{4,}+(?!\d))
      )
    | (?<!\w) (?-i:PIN) (?!\w)
      (?: [^\S\r\n]*+ [=:\uFF1A] \s*+ (?!\*++(?!\S))\S++
        | [^\S\r\n]++ \d{4,}+ (?!\d)
      )
    | token [^\S\r\n]*+ \uFF1A \s*+ \S++
    """,
    re.IGNORECASE | re.VERBOSE,
)
#: Arabic names of a credential, including the joined spelling and "رمز المرور".
#: Marks (shadda, other tashkeel, tatweel) may sit on any letter. The same
#: value rule as English. ``الرقم السري`` and ``رمز المرور`` mask any next
#: word. The words of a label are joined by ``[^\S\r\n]``, not only a space.
_ARABIC_CREDENTIAL_LABELS = (
    "كلمة السر",
    "كلمة المرور",
    "الرمز السري",
    "مفتاح سري",
    "الرقم السري",
    "كلمةالمرور",
    "رمز المرور",
)
_ARABIC_MARKS = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_ARABIC_SECRET = re.compile(
    r"(?:(?:ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+ق[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+س[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+ي[\u064B-\u065F\u0670\u0640]*+|ر[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ز[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+و[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+)(?:[^\S\r\n]*+[=:\uFF1A]\s*+(?!\*++(?!\S))\S++|[^\S\r\n]++(?:ه[\u064B-\u065F\u0670\u0640]*+ي[\u064B-\u065F\u0670\u0640]*+|ه[\u064B-\u065F\u0670\u0640]*+و[\u064B-\u065F\u0670\u0640]*+)[^\S\r\n]++\S++|[^\S\r\n]++\S++)|(?:ك[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ة[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+س[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+|ك[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ة[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+و[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+|ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ز[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+س[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+ي[\u064B-\u065F\u0670\u0640]*+|م[\u064B-\u065F\u0670\u0640]*+ف[\u064B-\u065F\u0670\u0640]*+ت[\u064B-\u065F\u0670\u0640]*+ا[\u064B-\u065F\u0670\u0640]*+ح[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++س[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+ي[\u064B-\u065F\u0670\u0640]*+|ك[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ة[\u064B-\u065F\u0670\u0640]*+ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+و[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+)(?:[^\S\r\n]*+[=:\uFF1A]\s*+(?!\*++(?!\S))\S++|[^\S\r\n]++(?:ه[\u064B-\u065F\u0670\u0640]*+ي[\u064B-\u065F\u0670\u0640]*+|ه[\u064B-\u065F\u0670\u0640]*+و[\u064B-\u065F\u0670\u0640]*+)[^\S\r\n]++(?:[^\s_]*+_[^\s]*+|(?=[^\s]*\d)(?=[^\s]*[^\W\d_])[^\s]++|(?=[^\s]*-)(?:[^-\s]{1,3}-[^\s]*+|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}-[^\s]*+|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}+(?!\S)|[^\s-]++(?:-[^\s-]++){2,}+)|\d{4,}+(?!\d))|[^\S\r\n]++(?:[^\s_]*+_[^\s]*+|(?=[^\s]*\d)(?=[^\s]*[^\W\d_])[^\s]++|(?=[^\s]*-)(?:[^-\s]{1,3}-[^\s]*+|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}-[^\s]*+|[^\s-]*+(?:-[^\s-]{4,}+)*+-[^\s-]{1,3}+(?!\S)|[^\s-]++(?:-[^\s-]++){2,}+)|\d{4,}+(?!\d))))"
)
_ARABIC_JSON_SECRET = re.compile(
    r'"(?:ك[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ة[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+س[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+|ك[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ة[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+و[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+|ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ز[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+س[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+ي[\u064B-\u065F\u0670\u0640]*+|م[\u064B-\u065F\u0670\u0640]*+ف[\u064B-\u065F\u0670\u0640]*+ت[\u064B-\u065F\u0670\u0640]*+ا[\u064B-\u065F\u0670\u0640]*+ح[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++س[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+ي[\u064B-\u065F\u0670\u0640]*+|ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+ق[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+س[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+ي[\u064B-\u065F\u0670\u0640]*+|ك[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ة[\u064B-\u065F\u0670\u0640]*+ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+و[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+|ر[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ز[\u064B-\u065F\u0670\u0640]*+[^\S\r\n]++ا[\u064B-\u065F\u0670\u0640]*+ل[\u064B-\u065F\u0670\u0640]*+م[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+و[\u064B-\u065F\u0670\u0640]*+ر[\u064B-\u065F\u0670\u0640]*+)"\s*+[:\uFF1A]\s*+"[^"]*+"'
)


def is_arabic_credential_label(text: str) -> bool:
    """True when *text* is an Arabic name of a credential. Marks are ignored."""
    bare = " ".join(_ARABIC_MARKS.sub("", str(text)).split())
    return bare in _ARABIC_CREDENTIAL_LABELS


def hide_prose_secrets(text: str, replacement: str) -> str:
    """Phrases a free-text masker reads, in either language.

    The one copy of the key=value, key: value, and JSON-member rule (English
    here, Arabic in :data:`_ARABIC_SECRET` and :data:`_ARABIC_JSON_SECRET`),
    so a log line that never reaches :func:`redact_secrets` still hides
    them. ASCII ``token:`` stays out, so ``token: expired`` remains;
    :func:`redact_secrets` masks that unquoted form itself. A fullwidth
    colon masks the next token, and a value after a separator may sit on
    the next line. ``is``, ``هي``, ``هو``, and a bare next word mask a
    secret-shaped word, or four or more digits, across any whitespace but
    a line break. English ``PIN`` is case-sensitive and masks only after a
    separator or four or more digits. The Arabic PIN labels mask the next
    word whatever it is.
    """
    stepped = _PROSE_SECRETS.sub(replacement, str(text))
    stepped = _ARABIC_SECRET.sub(replacement, stepped)
    return _ARABIC_JSON_SECRET.sub(replacement, stepped)


def _mask_labels(text: str, pattern: re.Pattern[str], replacement: str) -> str:
    """*pattern*, then the prose phrases and the Arabic labels."""
    return hide_prose_secrets(pattern.sub(replacement, str(text)), replacement)


def _mask_secret_text(text: str, pattern: re.Pattern[str], replacement: str) -> str:
    """Label forms, then token shapes (``security.token_shapes``)."""
    from kazma_core.security.token_shapes import mask_tokens

    return mask_tokens(_mask_labels(text, pattern, replacement), replacement)


class _Redactor:
    """``_REDACT.sub(...)``: paths, then context-shaped secrets, then every
    token shape (``security.token_shapes``)."""

    def sub(self, replacement: str, text: str) -> str:
        stripped = _REDACT_PATHS.sub(replacement, str(text))
        return _mask_secret_text(stripped, _REDACT_SECRETS, replacement)


_REDACT = _Redactor()


def redact_secrets(text: str) -> str:
    """*text* with every secret-shaped fragment ``<redacted>``, paths kept.

    For a message the operator reads to fix their own setup -- an MCP server
    that would not start ("No such file: C:\\...\\npx.cmd") -- where
    :func:`validation_error`'s path redaction would remove the answer. A key
    is still never shown: an HTTP failure quotes its URL, query and all; a
    token is found by its shape too (``security.token_shapes``).

    A credential label (``password``, ``secret``, ``api_key``, and the
    Arabic names in :data:`_ARABIC_CREDENTIAL_LABELS`, marks allowed) is
    masked with a colon, an equals sign, or a fullwidth colon whatever the
    next token is, including a value on the next line. ``is`` / ``هي`` /
    ``هو``, and the same label with no separator, mask the next word when
    it contains an underscore, a short hyphen piece, three or more hyphen
    pieces, both a letter and a digit, or four or more digits, across any
    whitespace but a line break. A bare year (``The secret 2024 plan``),
    a two-piece compound (``rotation-policy``), and ``the password is
    required`` stay. English ``PIN`` is case-sensitive and masks only after
    a separator or four or more digits; the Arabic PIN labels mask the next
    word whatever it is. A JSON member whose key is a credential label is
    masked here and by :func:`hide_prose_secrets`. ASCII ``token:`` is
    masked here and kept, unquoted, by :func:`hide_prose_secrets`.
    """
    from kazma_core.security.token_shapes import mask_tokens

    return mask_tokens(_mask_labels(text, _REDACT_SECRETS, _REDACTED), _REDACTED)

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
