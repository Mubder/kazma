"""Adding a column to an existing SQLite table -- one way (AUD-027).

``CREATE TABLE IF NOT EXISTS`` never changes a table that already exists, so
a store that gains a column adds it itself when it opens, and SQLite has no
``ADD COLUMN IF NOT EXISTS``. Kazma had three hand-written answers: read
``PRAGMA table_info`` first (the TaskStore convention, AGENTS.md §6), try the
``ALTER`` and swallow every error, or try it and match the error text. The
swallowing kind hid every other failure: a locked or read-only database left
the column missing, and each later query failed with "no such column", far
from the cause and with nothing logged at it.

One rule now, for every SQLite store: read the table's columns, add only the
missing ones, and let a real failure raise to the store that is opening. The
one error that is not a failure is another process adding the same column
between the read and the ``ALTER`` ("duplicate column name"): the column is
there. ``tests/test_sqlite_column_migrations.py`` holds every SQLite
``ADD COLUMN`` in product code to this module.

Neither function commits: the caller owns its transaction (a connection in
Python's default mode runs the ``ALTER`` inside whatever it has open).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _checked(name: str) -> str:
    # Table and column names come from code, never from input; refuse anything
    # else rather than quote it into DDL.
    if not _IDENT.match(name):
        raise ValueError(f"not a plain SQL identifier: {name!r}")
    return name


def _is_duplicate_column(exc: sqlite3.OperationalError) -> bool:
    return "duplicate column name" in str(exc).lower()


def add_missing_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: Iterable[tuple[str, str]],
) -> list[str]:
    """Add each ``(name, definition)`` the table lacks; return the names added."""
    table = _checked(table)
    wanted = [(_checked(name), definition) for name, definition in columns]
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    added: list[str] = []
    for name, definition in wanted:
        if name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        except sqlite3.OperationalError as exc:
            if not _is_duplicate_column(exc):
                raise
            continue  # another process added it first
        added.append(name)
    if added:
        logger.info("[sqlite] added column(s) %s to %s", ", ".join(added), table)
    return added


async def add_missing_columns_async(
    db: Any,
    table: str,
    columns: Iterable[tuple[str, str]],
) -> list[str]:
    """:func:`add_missing_columns` for an ``aiosqlite`` connection."""
    table = _checked(table)
    wanted = [(_checked(name), definition) for name, definition in columns]
    rows = await db.execute_fetchall(f"PRAGMA table_info({table})")
    existing = {str(row[1]) for row in rows}
    added: list[str] = []
    for name, definition in wanted:
        if name in existing:
            continue
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        except sqlite3.OperationalError as exc:
            if not _is_duplicate_column(exc):
                raise
            continue  # another process added it first
        added.append(name)
    if added:
        logger.info("[sqlite] added column(s) %s to %s", ", ".join(added), table)
    return added
