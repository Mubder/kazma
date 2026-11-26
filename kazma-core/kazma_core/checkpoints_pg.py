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

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from kazma_core.spaces.row_work import install_statements

logger = logging.getLogger(__name__)

__all__ = ["close_postgres_checkpointer", "open_postgres_checkpointer"]


def _conninfo(dsn: str) -> str:
    # libpq takes "postgresql://"; some operators write the short scheme.
    return "postgresql://" + dsn[len("postgres://"):] if dsn.startswith("postgres://") else dsn


#: The counted pool class, by the psycopg pool class it is made from.
_REPORTED: dict[type, type] = {}


def _reported_pool_class() -> type:
    """psycopg's ``AsyncConnectionPool`` whose every checkout is counted by
    the door it goes through (``db.space_doors``, Spaces S4-01 step 1: report
    mode, nothing refused). LangGraph's saver and the code that takes
    ``saver.conn`` itself (the gateway's thread listing, the approvals'
    listing, retention) all check out through ``connection()``. Made on
    first use, once for each pool class (psycopg_pool is an extra).

    Its checkout also asks whose statements these are
    (``row_security.checkpoint_door``, S4-01 step 4). Spaces not enforced and
    the lock not armed: today's path, nothing more sent. Enforced: a space's
    checkout opens its transaction in the space (``BEGIN`` and the setting in
    one message, awaited), so the rows it writes are stamped with it, armed
    or not (review M7), and ends it with ``COMMIT`` or ``ROLLBACK``. Armed, a
    space's checkout goes to the space half: a pool signed in as the space
    role, opened on the loop at its first use (review H4), every connection
    role-checked and given back reset (step 3's guards)."""
    from psycopg_pool import AsyncConnectionPool  # type: ignore

    from kazma_core.db import row_security, space_doors

    made = _REPORTED.get(AsyncConnectionPool)
    if made is not None:
        return made

    class ReportedCheckpointPool(AsyncConnectionPool):  # type: ignore[misc,type-arg,valid-type]
        _kazma_space_half: Any = None
        _kazma_space_lock: Any = None

        @asynccontextmanager
        async def connection(self, timeout: float | None = None) -> AsyncIterator[Any]:
            space_doors.checkout("checkpoints")
            space = row_security.checkpoint_door()
            if not space:
                async with super().connection(timeout=timeout) as conn:
                    yield conn
                return
            armed = row_security.armed()
            source = (await self._space_half()).connection(timeout=timeout) if armed \
                else super().connection(timeout=timeout)
            async with source as conn:
                await row_security.begin_space_async(conn, space, role=armed)
                ok = False
                try:
                    yield conn
                    ok = True
                finally:
                    if not conn.closed:
                        await row_security.end_space_transaction_async(conn, ok)

        async def _space_half(self) -> Any:
            """The pool that signs in as the space role (the address's own
            options kept, the role last), as large as this one, opened on the
            loop where the saver runs."""
            if self._kazma_space_half is None:
                if self._kazma_space_lock is None:
                    self._kazma_space_lock = asyncio.Lock()
                async with self._kazma_space_lock:
                    if self._kazma_space_half is None:
                        from psycopg.rows import dict_row  # type: ignore

                        from kazma_core.db.postgres_pool import pool_connection_kwargs

                        space_conninfo = row_security.space_conninfo(self.conninfo)
                        half = AsyncConnectionPool(
                            conninfo=space_conninfo,
                            min_size=0,
                            max_size=self.max_size,
                            kwargs=pool_connection_kwargs(
                                space_conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row,
                            ),
                            configure=row_security.check_space_role_async,
                            reset=row_security.end_space_async,
                            check=AsyncConnectionPool.check_connection,
                            open=False,
                        )
                        await half.open()
                        self._kazma_space_half = half
            return self._kazma_space_half

        async def close(self, timeout: float = 5.0) -> None:
            half, self._kazma_space_half = self._kazma_space_half, None
            if half is not None:
                await half.close(timeout=timeout)
            await super().close(timeout=timeout)

    _REPORTED[AsyncConnectionPool] = ReportedCheckpointPool
    return ReportedCheckpointPool


#: Kazma's saver class, by the LangGraph saver class it is made from.
_SAVERS: dict[type, type] = {}


def _saver_class() -> type:
    """LangGraph's ``AsyncPostgresSaver`` with ``aprune`` keeping the latest
    refused in a space's checkout -- Spaces enforced, armed or not (S4-01
    review L1: it sets its transaction's isolation inside the checkout's
    ``BEGIN`` and fails with 25001). Kazma never calls it; the ``delete``
    strategy works. Spaces off and nothing armed, it prunes as before."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore

    from kazma_core.db import row_security

    made = _SAVERS.get(AsyncPostgresSaver)
    if made is not None:
        return made

    class KazmaPostgresSaver(AsyncPostgresSaver):  # type: ignore[misc,valid-type]
        async def aprune(self, thread_ids: Any, *, strategy: str = "keep_latest") -> None:
            if strategy == "keep_latest" and row_security.checkpoint_door():
                raise row_security._KeepLatestRefused(
                    "pruning to the latest checkpoint cannot run in a space's transaction: use the delete strategy")
            await super().aprune(thread_ids, strategy=strategy)

    _SAVERS[AsyncPostgresSaver] = KazmaPostgresSaver
    return KazmaPostgresSaver


async def open_postgres_checkpointer(dsn: str, *, max_size: int = 8) -> Any:
    """An ``AsyncPostgresSaver`` on its own pool, with its tables set up.

    The pool is ``saver.conn``; close it with
    :func:`close_postgres_checkpointer`. Autocommit because LangGraph's
    ``setup()`` may run ``CREATE INDEX CONCURRENTLY``, which cannot run in a
    transaction; ``prepare_threshold=0`` prepares each statement at its first
    use (``None`` would never prepare, which a pooler in front of Postgres that
    cannot keep prepared statements needs). Measured 2026-11-25 on one server:
    0 and ``None`` take the same time. Raises when Postgres or the package is
    not there -- the callers fall back to SQLite and say so, but never on a
    refusal (``pg_helpers.no_other_store_on_refusal``): that one goes up.
    """
    from psycopg.rows import dict_row  # type: ignore
    from psycopg_pool import AsyncConnectionPool  # type: ignore

    from kazma_core.checkpoint_serde import kazma_checkpoint_serde
    from kazma_core.db.postgres_pool import pool_connection_kwargs

    conninfo = _conninfo(dsn)
    pool = _reported_pool_class()(
        conninfo=conninfo,
        min_size=1,
        max_size=max_size,
        kwargs=pool_connection_kwargs(
            conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row,
        ),
        # A connection the server closed (a database restart) is replaced
        # at checkout instead of failing the turn that drew it.
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
    from kazma_core.db import availability

    began = availability.now()
    ready = False
    try:
        await pool.open()
        saver = _saver_class()(conn=pool, serde=kazma_checkpoint_serde())  # type: ignore[arg-type]
        with install_statements("the checkpoint tables, made for every space"):
            await saver.setup()
        await _rules_after_setup(conninfo)
        ready = True
        availability.note_success("checkpoints", since=began)
    finally:
        if not ready:
            # One process-wide "the database is away" (AGENTS.md section 44),
            # told what stopped the setup; the error itself goes on up.
            failure = sys.exc_info()[1]
            if isinstance(failure, Exception):
                availability.note_failure("checkpoints", failure)
            await pool.close()  # a failed setup must not leave its pool open
    return saver


async def _rules_after_setup(conninfo: str) -> None:
    """The second lock's rules on tables ``setup()`` just made (Spaces S4-01
    review H3): LangGraph makes them outside :func:`pg_schema.ensure_schema`.
    Nothing until the rules are in place in this process. A failure is said
    and the checkpointer still opens: the space role is refused those tables
    until the next start puts their rules in place."""
    from kazma_core.db import row_security

    if not row_security.in_place():
        return
    import psycopg

    try:
        await asyncio.to_thread(row_security.after_checkpointer_setup, conninfo)
    except psycopg.Error as exc:
        logger.warning("[checkpoints] the checkpoint tables' rules were not put in place (%s); the next start "
                       "tries again", type(exc).__name__)


async def close_postgres_checkpointer(saver: Any) -> bool:
    """Close the pool of a saver :func:`open_postgres_checkpointer` opened.
    True when a pool was closed; a saver without one is left alone."""
    pool = getattr(saver, "conn", None)
    close = getattr(pool, "close", None)
    if close is None or getattr(pool, "closed", False):
        return False
    await close()
    return True
