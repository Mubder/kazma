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


def get_pool() -> Any:
    from kazma_core.db.postgres_pool import get_postgres_pool

    pool = get_postgres_pool()
    if pool is None:
        raise RuntimeError("Postgres pool unavailable (set KAZMA_DATABASE_URL)")
    return pool


def json_loads(val: Any, default: Any = None) -> Any:
    if val is None:
        return default
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return default
    return default


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
