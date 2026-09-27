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

A connection a store keeps open is in autocommit mode, or every write on it
runs inside ``with conn:`` (``tests/test_sqlite_kept_connections.py``). On an
autocommit ``aiosqlite`` connection, :func:`write_transaction` groups the
writes that must land together.
"""

from __future__ import annotations

import asyncio
import sqlite3
import weakref
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

__all__ = ["committed_and_closed", "write_transaction"]

#: One lock per connection: two groups on one connection wait for each other
#: instead of the second failing with "cannot start a transaction within a
#: transaction". Weak keys: a closed store's entry goes with its connection.
_GROUP_LOCKS: weakref.WeakKeyDictionary[object, asyncio.Lock] = weakref.WeakKeyDictionary()


@contextmanager
def committed_and_closed(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Yield *conn*; commit on success, roll back on error, close either way."""
    try:
        with conn:
            yield conn
    finally:
        conn.close()


@asynccontextmanager
async def write_transaction(conn: aiosqlite.Connection) -> AsyncIterator[aiosqlite.Connection]:
    """One write transaction on an autocommit ``aiosqlite`` connection.

    ``BEGIN IMMEDIATE`` takes the write lock up front (so the block never
    fails half way to get it), and the block ends in ``COMMIT``, or in
    ``ROLLBACK`` when it raises or is cancelled: the lock is never left held
    and a half-done group of writes never lands. Groups on one connection
    run one at a time.
    """
    async with _GROUP_LOCKS.setdefault(conn, asyncio.Lock()):
        committed = False
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            await conn.commit()
            committed = True
        finally:
            if not committed:
                await conn.rollback()
