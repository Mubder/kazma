"""Postgres schema setup that neither deadlocks nor waits behind a busy table.

2026-10-13. Every Kazma store that runs on Postgres set up its tables when it
was constructed -- the core shared tables at every pool start, the swarm task
store, the document jobs, catalog and intake stores at every service start --
by sending ``CREATE TABLE/INDEX IF NOT EXISTS`` and ``ALTER TABLE ... ADD
COLUMN IF NOT EXISTS``. Postgres takes the ``ALTER``'s ACCESS EXCLUSIVE lock
BEFORE it looks for the column, so a column that has existed for months still
cost an exclusive lock on every start:

* two processes starting at once (replicas, or several writers of one
  install) deadlocked on each other's locks (a 4-writer restore drill, the
  same day: ``DeadlockDetected`` in the intake store's constructor);
* a start while ``pg_dump`` held its read locks (the scheduled backup, the
  document backup's snapshot) waited for the whole dump, and every query on
  that table queued behind the waiting ``ALTER``: the core schema's 5 s
  statement timeout turned that into a failed pool start.

``ensure_schema`` runs a store's statements inside the caller's transaction,
after one transaction-scoped advisory lock that every Kazma schema setup takes
(so concurrent starts take turns instead of deadlocking), and sends each
``CREATE TABLE``, ``CREATE INDEX`` and ``ADD COLUMN`` only when the catalog
says the table, index or column is missing. A start against a current schema
takes no table lock at all. Any other ``ALTER TABLE`` is refused: it would
take the exclusive lock on every start. Other statements (an
``INSERT ... ON CONFLICT DO NOTHING`` seed row) run as given.

``tests/test_pg_schema_setup.py`` holds every Postgres ``ADD COLUMN`` in
product code to this module and runs the stores on a real Postgres.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

#: One key for every Kazma schema setup: a total order, so no two can deadlock.
SCHEMA_LOCK_KEY = "kazma:schema-setup"

_CREATE_TABLE = re.compile(r"^\s*CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][\w.]*)", re.I)
_CREATE_INDEX = re.compile(
    r"^\s*CREATE\s+(?:UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_][\w.]*)\s+ON\s+([A-Za-z_][\w.]*)", re.I
)
_ADD_COLUMN = re.compile(
    r"^\s*ALTER\s+TABLE\s+([A-Za-z_][\w.]*)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_]\w*)\s", re.I
)
_ALTER = re.compile(r"^\s*ALTER\s+TABLE\b", re.I)


def split_statements(ddl: str) -> list[str]:
    """Statements of a DDL script: ``--`` comment lines dropped, split at ``;``.

    The scripts are Kazma's own; none holds a ``;`` inside a literal."""
    lines = [line for line in ddl.splitlines() if not line.strip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


def _exists(cur: Any, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    return bool(_first(cur.fetchone()))


def _has_column(cur: Any, table: str, column: str) -> bool:
    cur.execute(
        "SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attname = %s "
        "AND attnum > 0 AND NOT attisdropped",
        (table, column),
    )
    return cur.fetchone() is not None


def _first(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()))
    return row[0]


def ensure_schema(cur: Any, statements: str | Iterable[str]) -> list[str]:
    """Bring a store's schema up to date inside the caller's transaction.

    Returns the statements it sent (empty when the schema was current). The
    caller commits; the advisory lock is released with its transaction.
    """

    items = split_statements(statements) if isinstance(statements, str) else [s.strip() for s in statements]
    cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (SCHEMA_LOCK_KEY,))
    sent: list[str] = []
    for statement in items:
        if not statement:
            continue
        table = _CREATE_TABLE.match(statement)
        index = _CREATE_INDEX.match(statement)
        column = _ADD_COLUMN.match(statement)
        if table:
            if _exists(cur, table.group(1)):
                continue
        elif index:
            if _exists(cur, index.group(1)):
                continue
        elif column:
            if _has_column(cur, column.group(1), column.group(2)):
                continue
        elif _ALTER.match(statement):
            raise ValueError(
                "ensure_schema takes ALTER TABLE ... ADD COLUMN IF NOT EXISTS only: any other "
                "ALTER would take an exclusive lock on every start"
            )
        cur.execute(statement)
        sent.append(statement)
    return sent
