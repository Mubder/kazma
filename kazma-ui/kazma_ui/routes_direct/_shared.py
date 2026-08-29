"""Shared helpers for the routes_direct package (audit O5).

Holds the two pieces every memory route needs — the tenant predicate and the
SQLite connection factory — so tenant scoping has one enforcement point and
schema setup is not re-run on every request (audit O4).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

__all__ = ["_mem_tid", "_tenant_clause", "open_memory_db", "memory_db"]


def _mem_tid() -> str:
    """Active memory tenant for THIS request (audit M-05).

    Delegates to memory_api._memory_tenant_id: 'default' under single-user
    (no narrowing — legacy behavior), the verified request tenant when
    enforcement is on, or '__unscoped__' on failure (fail-closed: matches
    nothing rather than leaking another tenant's rows).
    """
    try:
        from kazma_ui.memory_api import _memory_tenant_id

        return _memory_tenant_id()
    except Exception:
        # Audit F-05: this used to return "default", which _tenant_clause maps
        # to an EMPTY predicate — so a failed tenant lookup widened the query
        # to every tenant instead of narrowing it to none. Match the sentinel
        # the delegate itself uses on failure.
        logger.error(
            "[tenant] resolution failed — scoping query to nothing", exc_info=True
        )
        return "__unscoped__"


def _tenant_clause(tid: str, col: str = "tenant_id") -> tuple[str, list]:
    """(sql, params) tenant predicate; empty for the shared default tenant.

    Any unrecognized tenant id is treated as unscoped and matches nothing —
    "no predicate" must be requested explicitly via the shared default and can
    never be produced by a fallback (audit F-05).
    """
    if tid in ("", "default"):
        return "", []
    return f" AND {col} = ?", [tid]


# ── Connection factory (audit O4) ────────────────────────────────────────
#
# Every memory handler used to open its own connection and call
# ``ensure_primary_schema`` — a full DDL script plus a dozen ALTER TABLE
# statements that each raise and are swallowed — on EVERY request. The DDL is
# idempotent, so it only needs to run once per process per database file;
# per-connection pragmas still run every time.

_schema_ready: set[str] = set()
_schema_lock = threading.Lock()


def open_memory_db(path: str | None = None) -> sqlite3.Connection:
    """Open the primary memory DB with pragmas, row factory, and schema ready.

    The caller owns the connection and must close it — prefer
    :func:`memory_db`, which closes for you.
    """
    from kazma_core.paths import primary_memory_db

    db_path = str(path or primary_memory_db())
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row

    if db_path in _schema_ready:
        # Pragmas are per-connection; the DDL is not.
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(conn)
        return conn

    with _schema_lock:
        if db_path not in _schema_ready:
            from kazma_core.memory.schema_v2 import ensure_primary_schema

            ensure_primary_schema(conn)
            _schema_ready.add(db_path)
        else:
            from kazma_core.config_store import apply_sqlite_pragmas

            apply_sqlite_pragmas(conn)
    return conn


@contextmanager
def memory_db(path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Context-managed :func:`open_memory_db` — always closes."""
    conn = open_memory_db(path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            logger.debug("[memory_db] close failed", exc_info=True)


def reset_schema_cache() -> None:
    """Forget which databases have had their schema ensured (tests)."""
    with _schema_lock:
        _schema_ready.clear()
