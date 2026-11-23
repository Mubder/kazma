"""Every action under the person's name (Spaces S2-09).

Each request that changes something (``POST``, ``PUT``, ``PATCH``,
``DELETE``) is one row of ``activity.db``: when, the space it acted in, the
account that made it, the route (its method and template, ``POST
/api/documents/{document_id}/move``), the ids in its path and the answer's
status. Never a body, a query value or a word of what was sent: what is in a
space stays in its own stores.

The rows are written by ``kazma_ui.activity.ActivityRecorder`` (one
middleware: a route added later is recorded the day it is added) from what
the tenant middleware leaves in the request (the space it bound and its
asker). Only where Spaces is enforced: an install that does not enforce it
records nothing here (one person; the memory operator trail and the logs
are as before).

A space's members read its rows (:func:`recent`); nobody reads another
space's. The rows of a space go with its erase (the ``space`` column) and
are in its export. Kept 90 days, at most :data:`MAX_ROWS_PER_SPACE` a space,
pruned as they are written.

Blocking: async callers use ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ActivityRow", "record", "recent"]

logger = logging.getLogger(__name__)

#: Rows older than this go when the next row of their space is written.
KEEP_SECONDS = 90 * 24 * 3600
#: The most rows a space keeps; the oldest go first.
MAX_ROWS_PER_SPACE = 20_000
#: Every this many rows written in this process, the old ones are pruned.
_PRUNE_EVERY = 200
_IDS_MAX = 20
_ID_CHARS = 120

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL,
    space TEXT NOT NULL,
    account TEXT NOT NULL,
    action TEXT NOT NULL,
    ids TEXT NOT NULL DEFAULT '',
    status INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_space_at ON activity(space, at);
"""


@dataclass(frozen=True)
class ActivityRow:
    """One action: when, who, what (a route) and how it answered."""

    id: int
    at: float
    space: str
    account: str
    action: str
    ids: tuple[str, ...]
    status: int


def _activity_db() -> str:
    """The path of the activity store, in the data folder."""
    from kazma_core.paths import data_dir

    return str(Path(data_dir()) / "activity.db")


_schema_lock = threading.Lock()
_schema_ready: set[str] = set()
_written = 0
_written_lock = threading.Lock()


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """A short-lived connection: opened, used and closed by one call."""
    from kazma_core.config_store import apply_sqlite_pragmas

    path = _activity_db()
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    try:
        apply_sqlite_pragmas(conn)
        if path not in _schema_ready:
            with _schema_lock:
                if path not in _schema_ready:
                    conn.executescript(_SCHEMA)
                    _schema_ready.add(path)
        yield conn
    finally:
        conn.close()


def record(space: str, account: str, action: str, ids: list[str] | tuple[str, ...], status: int) -> None:
    """Write one action of *account* in *space*. A space or account that is
    not one (an empty one: a request no person made) writes nothing."""
    from kazma_core.spaces.scope import valid_account_id, valid_space_id

    if not valid_space_id(space) or not valid_account_id(account):
        return
    shown = "\x1f".join(str(i)[:_ID_CHARS] for i in list(ids)[:_IDS_MAX])
    global _written
    with _connection() as conn:
        conn.execute(
            "INSERT INTO activity (at, space, account, action, ids, status) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), space, account, str(action)[:300], shown, int(status)),
        )
        with _written_lock:
            _written += 1
            due = _written % _PRUNE_EVERY == 0
        if due:
            _prune(conn, space)


def _prune(conn: sqlite3.Connection, space: str) -> None:
    """The space's rows past their age or past its most go."""
    conn.execute("DELETE FROM activity WHERE at < ?", (time.time() - KEEP_SECONDS,))
    conn.execute(
        "DELETE FROM activity WHERE space = ? AND id <= ("
        "SELECT id FROM activity WHERE space = ? ORDER BY id DESC LIMIT 1 OFFSET ?)",
        (space, space, MAX_ROWS_PER_SPACE),
    )


def recent(space: str, *, limit: int = 100, before: int | None = None) -> list[ActivityRow]:
    """The newest actions of *space*, newest first, at most *limit*; with
    *before*, those older than that row (the next page)."""
    limit = max(1, min(int(limit), 500))
    query = "SELECT id, at, space, account, action, ids, status FROM activity WHERE space = ?"
    args: list[object] = [space]
    if before is not None:
        query += " AND id < ?"
        args.append(int(before))
    query += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _connection() as conn:
        rows = conn.execute(query, args).fetchall()
    return [ActivityRow(id=r[0], at=r[1], space=r[2], account=r[3], action=r[4],
                        ids=tuple(i for i in (r[5] or "").split("\x1f") if i), status=r[6]) for r in rows]
