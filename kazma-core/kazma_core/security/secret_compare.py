"""One way to compare a secret Kazma holds with one it was given (2026-11-15).

:func:`hmac.compare_digest` takes two ``str`` only when both are ASCII. A
header or query value holding any other character raised ``TypeError``: the
Telegram webhook's secret header, WhatsApp's verify token (a query value) and
its signature header -- open routes -- answered 500 with a traceback, and a
JSON value with a lone surrogate failed ``.encode("utf-8")`` the same way.

:func:`same_secret` compares the UTF-8 bytes of both sides in constant time;
a value that is neither text nor bytes is never equal. Nothing else in product
code calls ``compare_digest`` (``tests/test_secret_compare.py`` finds every
call from the source; the document broker, standard library only and loaded
by the guard by path, is declared there).
"""

from __future__ import annotations

import hmac

__all__ = ["same_secret"]


def _as_bytes(value: object) -> bytes | None:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        # surrogatepass: a lone surrogate from a JSON escape is a character
        # like any other here, never an exception.
        return value.encode("utf-8", "surrogatepass")
    return None


def same_secret(given: object, expected: object) -> bool:
    """Whether *given* equals *expected*, compared in constant time as UTF-8
    bytes. Text with any characters, or bytes; anything else is not equal."""
    a, b = _as_bytes(given), _as_bytes(expected)
    if a is None or b is None:
        return False
    return hmac.compare_digest(a, b)
