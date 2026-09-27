"""The Postgres checkpointer: one way to open it, one way to close it.

``KazmaAgent`` (``agent_runner``) and the gateway's ``CheckpointManager`` each
built their own ``AsyncPostgresSaver`` with the same pool settings, and no test
ran either against a real Postgres. Consolidated 2026-09-27, when the
Postgres-coverage gate (``tests/test_postgres_coverage.py``) named them --
and the agent's copy turned out never to close its pool: it called
``aclose()``, which psycopg's ``AsyncConnectionPool`` does not have, so every
model switch (which rebuilds the graph) left a pool and its connection open
for the life of the process.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["close_postgres_checkpointer", "open_postgres_checkpointer"]


def _conninfo(dsn: str) -> str:
    # libpq takes "postgresql://"; some operators write the short scheme.
    return "postgresql://" + dsn[len("postgres://"):] if dsn.startswith("postgres://") else dsn


async def open_postgres_checkpointer(dsn: str, *, max_size: int = 8) -> Any:
    """An ``AsyncPostgresSaver`` on its own pool, with its tables set up.

    The pool is ``saver.conn``; close it with
    :func:`close_postgres_checkpointer`. Autocommit because LangGraph's
    ``setup()`` may run ``CREATE INDEX CONCURRENTLY``, which cannot run in a
    transaction; ``prepare_threshold=0`` because a pooler in front of Postgres
    cannot keep prepared statements. Raises when Postgres or the package is
    not there -- the callers fall back to SQLite and say so.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore
    from psycopg.rows import dict_row  # type: ignore
    from psycopg_pool import AsyncConnectionPool  # type: ignore

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde

    pool = AsyncConnectionPool(
        conninfo=_conninfo(dsn),
        min_size=1,
        max_size=max_size,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=False,
    )
    await pool.open()
    ready = False
    try:
        saver = AsyncPostgresSaver(conn=pool, serde=kazma_checkpoint_serde())  # type: ignore[arg-type]
        await saver.setup()
        ready = True
    finally:
        if not ready:  # a failed setup must not leave its pool open
            await pool.close()
    return saver


async def close_postgres_checkpointer(saver: Any) -> bool:
    """Close the pool of a saver :func:`open_postgres_checkpointer` opened.
    True when a pool was closed; a saver without one is left alone."""
    pool = getattr(saver, "conn", None)
    close = getattr(pool, "close", None)
    if close is None or getattr(pool, "closed", False):
        return False
    await close()
    return True
