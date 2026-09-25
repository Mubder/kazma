"""Short-lived SQLite connections that commit AND close.

``with sqlite3.connect(path) as conn:`` commits (or rolls back) on exit but
does NOT close the connection, and a ``Connection`` sits in a reference cycle
with its statement cache, so the file stays open until a garbage-collection
pass. On Windows an open file cannot be renamed or deleted: every
``kazma migrate import`` died at its first file swap for exactly this reason
(2026-09-25), and the stores below held one handle per call until the GC
came round.

Stores keep their ``with self._connect() as conn:`` call sites and have
``_connect`` return :func:`committed_and_closed`. ``tests/test_store_registry.py``
fails when a function returns a raw connection that a ``with`` block uses.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["committed_and_closed"]


@contextmanager
def committed_and_closed(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Yield *conn*; commit on success, roll back on error, close either way."""
    try:
        with conn:
            yield conn
    finally:
        conn.close()
