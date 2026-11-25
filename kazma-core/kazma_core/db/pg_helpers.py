"""Shared helpers for Postgres dual-backend stores."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def use_postgres() -> bool:
    try:
        from kazma_core.db.backend import is_postgres

        return is_postgres()
    except Exception:
        return False


def store_errors() -> tuple[type[BaseException], ...]:
    """What reading or writing a Kazma store can legitimately raise: the
    SQLite and Postgres drivers, the file system, a pool that is not there
    (``RuntimeError``), a value that will not decode. Catch this rather than
    ``Exception``, so a programming error still surfaces."""
    import sqlite3

    errs: list[type[BaseException]] = [OSError, RuntimeError, ValueError, TypeError, sqlite3.Error]
    try:
        import psycopg

        errs.append(psycopg.Error)
    except ImportError:
        pass
    return tuple(errs)


#: "The database refused this" (a policy, a table this role may not touch).
_INSUFFICIENT_PRIVILEGE = "42501"


def is_space_refusal(exc: BaseException | None) -> bool:
    """Whether *exc*, or what it was raised from, is a refusal and not an
    outage: no space was bound to the work (``NoSpaceBound``), or Postgres
    would not let this role do it. The store is there and said no, so nothing
    may be read as missing or empty and no other store takes its place.

    Always False while Spaces is not enforced: an install without it reads its
    errors as before. ``store_errors()`` holds both kinds, so code that
    catches it to fall back asks here first (S4-01, H2)."""
    from kazma_core.spaces.scope import NoSpaceBound, spaces_enforced

    if not spaces_enforced():
        return False
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, NoSpaceBound) or getattr(exc, "sqlstate", None) == _INSUFFICIENT_PRIVILEGE:
            return True
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return False


def no_other_store_on_refusal(exc: BaseException, what: str) -> None:
    """Raise *exc* again when it is a refusal (:func:`is_space_refusal`):
    the first thing a handler that would use another store does. *what* is
    the store, for the log. A fallback made then would last as long as the
    process: documents would list empty, uploads and chat state would be kept
    in two places."""
    if not is_space_refusal(exc):
        return
    logger.error("%s: Postgres refused this (%s: %s). No other store is used in its place.",
                 what, type(exc).__name__, exc)
    raise exc


def get_pool() -> Any:
    from kazma_core.db.postgres_pool import get_postgres_pool

    pool = get_postgres_pool()
    if pool is None:
        raise RuntimeError("Postgres pool unavailable (set KAZMA_DATABASE_URL)")
    return pool


_NUL = "\x00"
_REPLACEMENT = "�"


def strip_nul(val: Any) -> Any:
    """``val`` with every U+0000 in its strings (keys too) replaced by U+FFFD.

    Postgres cannot store NUL in ``text``, and rejects the NUL escape in
    ``json``/``jsonb`` ("unsupported Unicode escape sequence"). Tool output can
    carry it -- binary blobs read as text -- and one NUL in a chat session made
    every later save of that session fail (2026-09-23: "A reply was produced
    but NOT saved to the transcript"). SQLite stores it fine, so the same
    value behaved differently per backend unless it is cleaned here.
    """
    if isinstance(val, str):
        return val.replace(_NUL, _REPLACEMENT) if _NUL in val else val
    if isinstance(val, dict):
        return {strip_nul(k): strip_nul(v) for k, v in val.items()}
    if isinstance(val, (list, tuple)):
        return [strip_nul(v) for v in val]
    return val


def json_dumps(val: Any) -> str:
    """JSON for a Postgres json/jsonb parameter -- never containing NUL."""
    return json.dumps(strip_nul(val), ensure_ascii=False, default=lambda o: strip_nul(str(o)))
