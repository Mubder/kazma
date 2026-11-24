"""LangGraph SQLite checkpointer factory with per-thread locking.

Produces an AsyncSqliteSaver from langgraph-checkpoint-sqlite,
wrapped in a CheckpointManager that prevents race conditions
during concurrent state writes to the same thread.

Usage:
    manager = await create_checkpoint_manager()
    graph = builder.compile(checkpointer=manager)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

import aiosqlite
from langchain_core.runnables import RunnableConfig
from kazma_core.checkpoint_serde import kazma_checkpoint_serde
from kazma_core.config_store import apply_sqlite_pragmas_async
from kazma_core.spaces import threads as _threads
from kazma_core.spaces.scope import OWNER_SPACE_ID, current_scope, spaces_enforced
from kazma_core.tenant_context import get_current_tenant_id
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    uuid6,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

#: Each thread's newest root checkpoint and how many it keeps, newest first.
#: LangGraph ids are uuid6, which sort by time: ordering by id is ordering by
#: last activity.
_NEWEST_PER_THREAD_PG = """
    SELECT thread_id, checkpoint_id, steps FROM (
        SELECT thread_id, checkpoint_id,
               ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY checkpoint_id DESC) AS rn,
               COUNT(*) OVER (PARTITION BY thread_id) AS steps
        FROM checkpoints
        WHERE checkpoint_ns = ''
    ) newest
    WHERE rn = 1
    ORDER BY checkpoint_id DESC
    LIMIT %s
"""
_NEWEST_PER_THREAD_SQLITE = _NEWEST_PER_THREAD_PG.replace("%s", "?")

#: The same listing held to one space's threads, on a table that holds every
#: space's (Spaces S1-08). The filter is part of the statement, before the
#: limit: filtered afterwards, a space whose chats are older than the newest
#: ``limit`` threads of the table got a short or an empty list. ``{held}`` is
#: ``IN`` for a list of the space's own threads and ``NOT IN`` for a list of
#: every other space's (the owner's space: ``spaces.threads.space_threads``).
#: Postgres takes the ids as one array; SQLite as one JSON text.
_NEWEST_OF_THREADS_PG = _NEWEST_PER_THREAD_PG.replace(
    "WHERE checkpoint_ns = ''", "WHERE checkpoint_ns = '' AND {pg_held}"
)
_NEWEST_OF_THREADS_SQLITE = _NEWEST_PER_THREAD_SQLITE.replace(
    "WHERE checkpoint_ns = ''", "WHERE checkpoint_ns = '' AND {sqlite_held}"
)
_HELD = {
    True: {"pg_held": "thread_id = ANY(%s::text[])",
           "sqlite_held": "thread_id IN (SELECT value FROM json_each(?))"},
    False: {"pg_held": "NOT (thread_id = ANY(%s::text[]))",
            "sqlite_held": "thread_id NOT IN (SELECT value FROM json_each(?))"},
}

__all__ = [
    "CheckpointManager",
    "create_checkpointer",
    "create_checkpoint_manager",
]

# Maximum number of per-thread locks retained in memory.  When exceeded
# the least-recently-used lock is evicted (LRU via OrderedDict).
_MAX_THREAD_LOCKS = 10_000


class CheckpointManager(BaseCheckpointSaver):
    """Thread-safe wrapper around AsyncSqliteSaver.

    Prevents race conditions during concurrent writes to the same
    thread_id by acquiring a per-thread asyncio.Lock before save.

    The internal ``_locks`` dict is bounded by ``max_locks`` (default
    10 000).  When the limit is exceeded the least-recently-used lock
    entry is evicted using an :class:`~collections.OrderedDict`
    (``move_to_end`` on access, ``popitem(last=False)`` on overflow).

    Args:
        saver:     The underlying AsyncSqliteSaver instance.
        max_locks: Maximum number of per-thread locks to retain.
    """

    def __init__(self, saver: AsyncSqliteSaver, max_locks: int = _MAX_THREAD_LOCKS) -> None:
        super().__init__(serde=getattr(saver, "serde", None))
        self._saver = saver
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()
        self._max_locks = max_locks
        self._tenant_savers: dict[str, AsyncSqliteSaver] = {}
        self._saver_lock = asyncio.Lock()
        # Set when the saver came from the shared pool — close() releases
        # instead of closing (audit M-G5).
        self._shared_db_path: str | None = None

    async def _get_saver(self) -> AsyncSqliteSaver:
        """The saver of the space this work is bound to, as before Spaces:
        the owner's file for the owner's space and for work that bound
        nothing, else the space's own ``checkpoints_{space}.db``."""
        return await self._saver_of(get_current_tenant_id() or OWNER_SPACE_ID)

    def _shared_table(self) -> bool:
        """True on Postgres: one table holds every thread's checkpoints."""
        return "Postgres" in type(self._saver).__name__

    @staticmethod
    def _asked_space() -> str | None:
        """The space this work is bound to, or None when it bound nothing."""
        scope = current_scope()
        if scope is not None:
            return scope.space_id
        return (get_current_tenant_id() or "").strip() or None

    @staticmethod
    async def _home(thread_id: str) -> str:
        """The space a thread belongs to (``spaces.threads.thread_home``),
        from memory when the record is known, else read in a thread."""
        known = _threads.known_space(thread_id)
        if known is not None:
            return known
        return await asyncio.to_thread(_threads.thread_home, thread_id)

    async def _open(self, thread_id: str | None, *, write: bool = False) -> Any | None:
        """The saver *thread_id* is read or written in; None for a read this
        work may not make (the thread is another space's: it reads as a
        thread with no checkpoints). A refused write raises.

        An install that does not enforce Spaces takes the bound tenant's
        saver, exactly as before. One that does follows the THREAD (Spaces
        S1-08): its checkpoints live in its own space, whoever asks, and a
        request bound to another space is refused. On Postgres the table is
        shared and keyed by thread only, so the thread's record is what keeps
        two spaces apart; on SQLite each space also has its own file.
        """
        if not spaces_enforced():
            return await self._get_saver()
        asked = self._asked_space()
        if not thread_id:
            # A listing or a clear names no thread: the bound space's saver,
            # and the owner's for install work that bound nothing.
            return await self._saver_of(asked or OWNER_SPACE_ID)
        home = await self._home(str(thread_id))
        if asked is not None and asked != home:
            if write:
                raise _threads.ThreadInAnotherSpace(
                    "this chat belongs to another space: its state can only be changed there"
                )
            logger.warning("[Checkpoint] a read of thread %s was refused: it is another space's",
                           str(thread_id)[:12])
            return None
        return await self._saver_of(home)

    async def _saver_of(self, tenant_id: str) -> AsyncSqliteSaver:
        """The saver of one space: the shared table on Postgres (once Spaces
        is enforced), the owner's file, or the space's own file."""
        if tenant_id == OWNER_SPACE_ID or (self._shared_table() and spaces_enforced()):
            return self._saver

        # Sanitize the tenant id for use as a FILENAME (audit M-G3): raw
        # ids like "telegram:12345" contain ':' (illegal on Windows — every
        # checkpoint write for that tenant failed) and could carry '/'/'..'
        # path traversal. Hash-mangled, stable per tenant, and bounded.
        safe_tenant = re.sub(r"[^A-Za-z0-9_-]+", "_", tenant_id).strip("_") or "x"
        if len(safe_tenant) > 48:
            digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:16]
            safe_tenant = f"{safe_tenant[:32]}_{digest}"

        async with self._saver_lock:
            if tenant_id not in self._tenant_savers:
                # Under data_dir(), not the literal "kazma-data" relative to
                # the process CWD: started from another directory, a tenant's
                # checkpoints landed where neither backup nor migration looks
                # (the same bug already fixed for checkpoints.db below, and in
                # knowledge / bookmarks / documents; now a gate in
                # tests/test_store_registry.py).
                from kazma_core.paths import data_dir

                db_path = data_dir() / f"checkpoints_{safe_tenant}.db"
                db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(str(db_path))
                await apply_sqlite_pragmas_async(conn)

                # The one strict serializer (kazma_core/checkpoint_serde.py).
                saver = AsyncSqliteSaver(conn, serde=kazma_checkpoint_serde())
                await saver.setup()
                self._tenant_savers[tenant_id] = saver
                # Bound the saver cache (audit M-G3): one open SQLite
                # connection per tenant, never evicted, used to grow without
                # limit under per-user tenants. Keep the most recent 32.
                while len(self._tenant_savers) > 32:
                    oldest = next(iter(self._tenant_savers))
                    if oldest == tenant_id:
                        break
                    dropped = self._tenant_savers.pop(oldest, None)
                    if dropped is not None:
                        try:
                            await dropped.conn.close()
                        except Exception:
                            pass
                logger.info("[Checkpoint] Dynamic CheckpointManager created for tenant %s at %s", tenant_id, db_path)

            return self._tenant_savers[tenant_id]

    def _get_lock(self, thread_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific thread_id.

        Uses LRU ordering: existing entries are moved to the end
        (most-recently-used) and the oldest entry is evicted when the
        bound is exceeded. Locks that are currently held are never evicted.
        """
        lock = self._locks.get(thread_id)
        if lock is not None:
            # LRU: mark as most-recently-used.
            self._locks.move_to_end(thread_id)
            return lock
        lock = asyncio.Lock()
        self._locks[thread_id] = lock
        # Evict oldest non-held entries when the bound is exceeded.
        while len(self._locks) > self._max_locks:
            evicted = False
            for key in list(self._locks.keys()):
                if not self._locks[key].locked():
                    self._locks.pop(key)
                    evicted = True
                    break
            if not evicted:
                break  # All held — keep growing rather than breaking exclusion
        return lock

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        """Save a checkpoint with per-thread locking.

        Extracts thread_id from config["configurable"]["thread_id"]
        and acquires the corresponding lock before writing.
        """
        thread_id = config.get("configurable", {}).get("thread_id", "default")
        lock = self._get_lock(thread_id)

        async with lock:
            saver = await self._open(self._thread_of(config), write=True)
            return await saver.aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Save pending writes with per-thread locking."""
        thread_id = config.get("configurable", {}).get("thread_id", "default")
        lock = self._get_lock(thread_id)

        async with lock:
            saver = await self._open(self._thread_of(config), write=True)
            await saver.aput_writes(config, writes, task_id, task_path)

    @staticmethod
    def _thread_of(config: Any) -> str | None:
        """The thread a config names, or None (the lock's ``"default"`` is not one)."""
        thread_id = ((config or {}).get("configurable") or {}).get("thread_id")
        return str(thread_id) if thread_id else None

    async def aget(self, config: dict[str, Any]) -> Any:
        """Retrieve a checkpoint (read-only, no lock needed)."""
        saver = await self._open(self._thread_of(config))
        return await saver.aget(config) if saver is not None else None

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        """Retrieve a checkpoint tuple."""
        saver = await self._open(self._thread_of(config))
        return await saver.aget_tuple(config) if saver is not None else None

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> Any:
        """The caller's tenant's checkpoints, newest first: the saver's own
        listing. Without it the base class raised NotImplementedError, and the
        start-up pass that continues turns a restart cut off
        (``kazma_ui.turn_resume``) failed on every boot of the live server
        (2026-11-06), which hands it this manager, not the saver."""
        named = self._thread_of(config)
        saver = await self._open(named)
        if saver is None:
            return
        # A listing over every thread of a shared table shows a bound space
        # its own threads only. The space's threads are asked once.
        only = self._asked_space() if (named is None and self._filters_by_thread()) else None
        if only is None:
            async for item in saver.alist(config, filter=filter, before=before, limit=limit):
                yield item
            return
        mine = await self._threads_of(only)
        shown = 0
        # The saver's own limit counts every space's rows: it is applied here,
        # to the rows this space is shown.
        async for item in saver.alist(config, filter=filter, before=before):
            listed = self._thread_of(getattr(item, "config", None))
            if listed is None or not mine(listed):
                continue
            yield item
            shown += 1
            if limit is not None and shown >= limit:
                return

    @staticmethod
    async def _threads_of(space_id: str) -> Any:
        """A test "is this thread the space's?", from one read of the thread
        record store (``spaces.threads.space_threads``), off the loop."""
        own, ids = await asyncio.to_thread(_threads.space_threads, space_id)
        held = frozenset(ids)
        return (lambda thread_id: thread_id in held) if own else (lambda thread_id: thread_id not in held)

    def _filters_by_thread(self) -> bool:
        """True when one table holds several spaces' threads and Spaces is
        enforced: a listing or a clear must then go thread by thread."""
        return spaces_enforced() and self._shared_table()

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints for a thread."""
        lock = self._get_lock(thread_id)
        async with lock:
            saver = await self._open(str(thread_id) if thread_id else None, write=True)
            if hasattr(saver, "adelete_thread"):
                await saver.adelete_thread(thread_id)

    # ── A chat moved between spaces (Spaces S2-12) ─────────────────────────

    async def amove_state(self, thread_id: str, *, from_space: str, to_space: str,
                          clean: Callable[[dict[str, Any]], dict[str, Any]]) -> bool:
        """Put the thread's newest state, passed through *clean*, where
        *to_space* reads it: the chat's state moves
        (``kazma_ui.chat_move``, ``spaces.chat_moves.moved_state``). Only that
        one state, with no pending writes: the older ones hold what the
        source space recalled at each step, and nothing reads them but
        Time Travel, whose steps do not move. False when the thread has none.

        Each space's own file: the target gets the cleaned state, and the
        source's is left for :meth:`adelete_thread_in` after the switch. One
        table for every space (Postgres, the live install's): the cleaned
        state replaces the thread's, in place -- before the switch, so that
        nothing of the source space is there to read after it. The thread's
        record still names the source while this runs. Run again, it does
        the same."""
        tid = str(thread_id)
        source = await self._saver_of(from_space)
        target = await self._saver_of(to_space)
        config: Any = {"configurable": {"thread_id": tid, "checkpoint_ns": ""}}
        found = await source.aget_tuple(config)
        if found is None:
            return False
        checkpoint = dict(found.checkpoint)
        values = dict(checkpoint.get("channel_values") or {})
        cleaned = clean(values)
        versions = dict(checkpoint.get("channel_versions") or {})
        if source is not target:
            checkpoint["channel_values"] = cleaned
            await target.adelete_thread(tid)  # an earlier attempt's copy
            await target.aput(config, checkpoint, found.metadata, versions)
            return True
        # One table: a channel's value is stored by its version, so what
        # changed gets a new one, and what went loses its own.
        version = self._version_after(source, versions)
        changed = {k: version for k, v in cleaned.items() if k not in values or values[k] != v}
        kept = {k: v for k, v in versions.items() if k in cleaned or k not in values}
        checkpoint.update(id=str(uuid6(clock_seq=-2)), channel_values=cleaned,
                          channel_versions={**kept, **changed})
        await source.aput(config, checkpoint, found.metadata, changed)
        await self._keep_only(source, tid, checkpoint["id"])
        return True

    def _version_after(self, saver: Any, versions: ChannelVersions) -> Any:
        """The version after every one of *versions*, in the scheme they are
        in: a graph numbers its channels through the saver it was compiled
        with -- this manager (whole numbers) or a saver of its own (text) --
        and LangGraph compares them, so the two schemes never mix."""
        newest = max(versions.values(), default=None)
        return (saver if isinstance(newest, str) else self).get_next_version(newest, None)

    @staticmethod
    async def _keep_only(saver: Any, thread_id: str, checkpoint_id: str) -> None:
        """Delete every checkpoint and pending write of *thread_id* but the
        root checkpoint *checkpoint_id*, in one transaction, and (Postgres)
        every channel value it does not name."""
        if "Postgres" in type(saver).__name__ and hasattr(saver.conn, "connection"):
            async with saver.conn.connection() as conn, conn.transaction():
                await conn.execute("DELETE FROM checkpoint_writes WHERE thread_id = %s", (thread_id,))
                await conn.execute("DELETE FROM checkpoints WHERE thread_id = %s AND NOT "
                                   "(checkpoint_ns = '' AND checkpoint_id = %s)", (thread_id, checkpoint_id))
                await conn.execute(
                    "DELETE FROM checkpoint_blobs b WHERE b.thread_id = %s AND NOT EXISTS ("
                    "SELECT 1 FROM checkpoints c, jsonb_each_text(c.checkpoint -> 'channel_versions') e "
                    "WHERE c.thread_id = b.thread_id AND c.checkpoint_ns = b.checkpoint_ns "
                    "AND e.key = b.channel AND e.value = b.version)", (thread_id,))
            return
        async with saver.lock:
            await saver.conn.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
            await saver.conn.execute("DELETE FROM checkpoints WHERE thread_id = ? AND NOT "
                                     "(checkpoint_ns = '' AND checkpoint_id = ?)", (thread_id, checkpoint_id))
            await saver.conn.commit()

    async def adelete_thread_in(self, thread_id: str, *, space: str) -> None:
        """Delete *thread_id*'s checkpoints from *space*'s saver: the copy a
        move leaves behind (the source after the switch, the target when a
        move is put back). Never the saver the thread's record names: there
        the checkpoints are the chat's own."""
        saver = await self._saver_of(space)
        if saver is await self._saver_of(await self._home(str(thread_id))):
            return
        if hasattr(saver, "adelete_thread"):
            await saver.adelete_thread(str(thread_id))

    @staticmethod
    async def _thread_ids(saver: Any) -> list[str]:
        """Every thread that has a checkpoint in *saver*'s table."""
        query = "SELECT DISTINCT thread_id FROM checkpoints"
        conn = getattr(saver, "conn", None)
        if conn is None:
            return []
        if "Postgres" in type(saver).__name__:
            async with conn.connection() as held:  # type: ignore[union-attr]
                async with held.cursor() as cur:  # type: ignore[union-attr]
                    await cur.execute(query)
                    return [str(r["thread_id"] if isinstance(r, dict) else r[0])
                            for r in await cur.fetchall()]
        cursor = await conn.execute(query)
        return [str(row[0]) for row in await cursor.fetchall()]

    async def adelete_all_threads(self) -> int:
        """Delete every checkpoint thread. Returns how many threads were removed.

        Postgres and SQLite do not share a placeholder or a connection type.
        A raw ``DELETE … ?`` against the Postgres pool is what made the
        dashboard clear-all route return 500 on a Postgres install.
        """
        saver = await self._open(None, write=True)
        tables = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")
        if self._filters_by_thread():
            # One table, several spaces: only the bound space's threads go.
            from kazma_core.spaces.scope import require_space_id

            only = require_space_id()
            is_mine = await self._threads_of(only)
            mine = [t for t in await self._thread_ids(saver) if t and is_mine(t)]
            for thread_id in mine:
                await saver.adelete_thread(thread_id)
            return len(mine)
        if "Postgres" in type(saver).__name__:
            pool = getattr(saver, "conn", None)
            if pool is None:
                return 0
            async with pool.connection() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cur:  # type: ignore[union-attr]
                    await cur.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints")
                    row = await cur.fetchone()
                    if isinstance(row, dict):
                        count = int(next(iter(row.values()), 0) or 0)
                    else:
                        count = int(row[0] if row else 0)
                    for table in tables:
                        await cur.execute(f"DELETE FROM {table}")
            return count

        conn = saver.conn if hasattr(saver, "conn") else None
        if conn is None:
            return 0
        cursor = await conn.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints")
        row = await cursor.fetchone()
        count = int(row[0] if row else 0)
        for table in tables:
            try:
                await conn.execute(f"DELETE FROM {table}")
            except Exception:
                if table == "checkpoints":
                    raise
                logger.debug("[Checkpoint] skip missing table %s", table, exc_info=True)
        await conn.commit()
        return count

    async def setup(self) -> None:
        """Initialize the underlying saver."""
        await self._saver.setup()

    @property
    def conn(self) -> Any:
        """Expose the underlying connection."""
        tenant_id = get_current_tenant_id() or OWNER_SPACE_ID
        if tenant_id == OWNER_SPACE_ID:
            return self._saver.conn if hasattr(self._saver, "conn") else None
        saver = self._tenant_savers.get(tenant_id)
        return saver.conn if saver and hasattr(saver, "conn") else None

    async def close(self) -> None:
        """Close the underlying database connection.

        A SHARED saver (default checkpoints.db via checkpoints_shared) is
        RELEASED, not closed — the process-wide cache keeps serving it to
        other holders (audit M-G5); the connection closes only when the
        last holder releases. Owned/tenant savers close directly.
        """
        if getattr(self, "_shared_db_path", None):
            try:
                from kazma_core.checkpoints_shared import release_shared_checkpoints

                await release_shared_checkpoints(self._shared_db_path)
            except Exception:
                logger.debug("[Checkpoint] shared release failed", exc_info=True)
            self._shared_db_path = None
        elif hasattr(self._saver, "conn") and self._saver.conn:
            await self._saver.conn.close()
        for saver in self._tenant_savers.values():
            if hasattr(saver, "conn") and saver.conn:
                try:
                    await saver.conn.close()
                except Exception:
                    pass
        self._tenant_savers.clear()

    async def list_checkpoints(self, limit: int = 50) -> list[dict[str, Any]]:
        """List checkpointed threads, newest activity first.

        One row per thread: its newest checkpoint, how many checkpoints it
        keeps (``steps``), when the newest was saved (``last_activity``,
        ISO-8601, read from the uuid6 id -- the checkpoint's own ``ts`` when
        the id is not one), and ``message_count``: the messages inside the
        newest checkpoint, or ``None`` when the checkpoint does not carry
        them. The Postgres saver keeps messages in ``checkpoint_blobs``, so
        there it is always ``None``; the Dashboard takes the count from the
        chat instead. It used to report 0 for every thread (live
        2026-09-28: 50 rows of "0 messages, created -").

        Only the root graph's checkpoints count (``checkpoint_ns = ''``).
        """
        saver = await self._open(None)
        saver_type = type(saver).__name__

        # ── Postgres backend ──────────────────────────────────────────
        if "Postgres" in saver_type:
            if self._filters_by_thread() and (only := self._asked_space()) is not None:
                # One table, several spaces: the bound space's threads only,
                # chosen by the statement itself.
                own, ids = await asyncio.to_thread(_threads.space_threads, only)
                return await self._list_checkpoints_postgres(
                    saver, limit, query=_NEWEST_OF_THREADS_PG.format(**_HELD[own]), threads=ids,
                )
            return await self._list_checkpoints_postgres(saver, limit)

        # ── SQLite backend (default) ──────────────────────────────────
        conn = saver.conn if hasattr(saver, "conn") else None
        if conn is None:
            logger.warning(
                "[Checkpoint] list_checkpoints: saver has no conn (type=%s)",
                saver_type,
            )
            return []
        # One table, several spaces (never a SQLite install's own layout,
        # where each space has its file; kept so the rule is one rule).
        only = self._asked_space() if self._filters_by_thread() else None
        try:
            if only is not None:
                own, ids = await asyncio.to_thread(_threads.space_threads, only)
                cursor = await conn.execute(
                    _NEWEST_OF_THREADS_SQLITE.format(**_HELD[own]), (json.dumps(ids), limit)
                )
            else:
                cursor = await conn.execute(_NEWEST_PER_THREAD_SQLITE, (limit,))
            rows = await cursor.fetchall()
            results: list[dict[str, Any]] = []
            for thread_id, checkpoint_id, steps in rows:
                checkpoint: dict[str, Any] | None = None
                try:
                    blob_cursor = await conn.execute(
                        "SELECT type, checkpoint FROM checkpoints "
                        "WHERE thread_id = ? AND checkpoint_ns = '' AND checkpoint_id = ? LIMIT 1",
                        (thread_id, checkpoint_id),
                    )
                    blob_row = await blob_cursor.fetchone()
                    if blob_row and blob_row[1]:
                        checkpoint = self._decode_checkpoint(blob_row[1], blob_row[0], saver)
                except Exception as exc:
                    logger.debug("Checkpoint blob decode failed for thread %s: %s", thread_id, exc)
                results.append(self._thread_row(thread_id, checkpoint_id, steps, checkpoint))
            return results
        except Exception:
            logger.warning("[Checkpoint] list_checkpoints query failed", exc_info=True)
            return []

    async def _list_checkpoints_postgres(
        self, saver: Any, limit: int, *, query: str = _NEWEST_PER_THREAD_PG,
        threads: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Postgres variant of list_checkpoints using the AsyncConnectionPool.

        The ``AsyncPostgresSaver`` stores its pool in ``saver.conn`` (an
        ``AsyncConnectionPool``). The ``checkpoint`` column is JSONB, which
        psycopg hands back already parsed.
        """
        pool = saver.conn if hasattr(saver, "conn") else None
        if pool is None:
            return []
        try:
            async with pool.connection() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cur:  # type: ignore[union-attr]
                    await cur.execute(query, (limit,) if threads is None else (threads, limit))
                    rows = await cur.fetchall()

                results: list[dict[str, Any]] = []
                for row in rows:
                    # psycopg dict_row returns dict; a plain cursor a tuple.
                    if isinstance(row, dict):
                        thread_id, checkpoint_id, steps = row["thread_id"], row["checkpoint_id"], row["steps"]
                    else:
                        thread_id, checkpoint_id, steps = row[0], row[1], row[2]
                    checkpoint: dict[str, Any] | None = None
                    try:
                        async with conn.cursor() as bcur:  # type: ignore[union-attr]
                            await bcur.execute(
                                "SELECT checkpoint FROM checkpoints "
                                "WHERE thread_id = %s AND checkpoint_ns = '' AND checkpoint_id = %s LIMIT 1",
                                (thread_id, checkpoint_id),
                            )
                            blob_row = await bcur.fetchone()
                        blob = blob_row.get("checkpoint") if isinstance(blob_row, dict) else (blob_row[0] if blob_row else None)
                        if isinstance(blob, memoryview):
                            blob = bytes(blob)
                        if blob:
                            checkpoint = self._decode_checkpoint(blob)
                    except Exception as exc:
                        logger.debug("Checkpoint blob decode failed for thread %s: %s", thread_id, exc)
                    results.append(self._thread_row(thread_id, checkpoint_id, steps, checkpoint))
                return results
        except Exception:
            logger.warning("[Checkpoint] list_checkpoints (postgres) query failed", exc_info=True)
            return []

    @staticmethod
    def _thread_row(
        thread_id: str, checkpoint_id: Any, steps: Any, checkpoint: dict[str, Any] | None
    ) -> dict[str, Any]:
        """One ``list_checkpoints`` row from the newest checkpoint of a thread."""
        from datetime import UTC, datetime

        from kazma_core.checkpoint_retention import checkpoint_time

        last_activity = ""
        born = checkpoint_time(str(checkpoint_id))
        if born is not None:
            last_activity = datetime.fromtimestamp(born, UTC).isoformat()
        elif checkpoint and isinstance(checkpoint.get("ts"), str):
            last_activity = checkpoint["ts"]
        message_count: int | None = None
        if checkpoint:
            messages = (checkpoint.get("channel_values") or {}).get("messages")
            if isinstance(messages, list):
                message_count = len(messages)
        return {
            "thread_id": thread_id,
            "checkpoint_id": str(checkpoint_id),
            "steps": int(steps or 0),
            "last_activity": last_activity,
            "message_count": message_count,
        }

    @staticmethod
    def _decode_checkpoint(
        blob: Any, type_: str | None = None, saver: Any = None
    ) -> dict[str, Any] | None:
        """A stored checkpoint as a dict, or ``None`` when it cannot be read.

        Postgres JSONB arrives parsed. SQLite stores what the saver's own
        serializer wrote, tagged by the row's ``type`` column, so the saver's
        serde reads it back (``loads_typed``); LangGraph's ``ormsgpack`` is
        the fallback for a row with no type, JSON for very old files. It
        used to ``import msgpack`` -- a package neither Kazma nor LangGraph
        depends on, present on the dev machine through ``locust`` only -- so
        on CI every count was ``None`` (2026-09-28).
        """
        if isinstance(blob, dict):
            return blob
        serde = getattr(saver, "serde", None)
        if serde is not None and type_:
            try:
                data = serde.loads_typed((str(type_), blob))
                if isinstance(data, dict):
                    return data
            except (ValueError, TypeError, KeyError) as exc:
                logger.debug("Checkpoint serde decode failed (type=%s): %s", type_, exc)
        try:
            import ormsgpack

            data = ormsgpack.unpackb(blob)
        except Exception:
            try:
                import json

                data = json.loads(blob if isinstance(blob, str) else blob.decode("utf-8", errors="replace"))
            except Exception:
                return None
        return data if isinstance(data, dict) else None

    @property
    def active_locks(self) -> int:
        """Number of thread locks currently held."""
        return len(self._locks)


async def create_checkpoint_manager(
    path: str | None = None,
) -> CheckpointManager:
    """Create and initialize a CheckpointManager with per-thread locking.

    Backend:
      * Postgres when ``KAZMA_DATABASE_URL`` is set (requires
        ``langgraph-checkpoint-postgres`` + ``psycopg``).
      * SQLite otherwise (``path``).

    Returns:
        Initialized CheckpointManager ready for graph.compile(checkpointer=...).
    """
    # ── Postgres checkpointer (multi-replica) ──────────────────────
    try:
        from kazma_core.db.backend import get_database_url, is_postgres

        if is_postgres():
            try:
                from kazma_core.checkpoints_pg import open_postgres_checkpointer

                saver = await open_postgres_checkpointer(get_database_url() or "", max_size=10)
                manager = CheckpointManager(saver)  # type: ignore[arg-type]
                logger.info(
                    "[Checkpoint] CheckpointManager using AsyncPostgresSaver (multi-replica)"
                )
                return manager
            except ImportError as exc:
                logger.warning(
                    "[Checkpoint] Postgres URL set but langgraph-checkpoint-postgres "
                    "unavailable (%s) — falling back to SQLite. "
                    "pip install -e '.[postgres]'",
                    exc,
                )
            except Exception as exc:
                logger.exception(
                    "[Checkpoint] AsyncPostgresSaver failed (%s) — SQLite fallback",
                    exc,
                )
    except Exception:
        pass

    # ── SQLite checkpointer (default) ──────────────────────────────
    # Resolve the default here, not in the signature. It was the literal
    # "kazma-data/checkpoints.db", which is relative to the process CWD and
    # blind to KAZMA_DATA_DIR — a service with a different WorkingDirectory
    # silently opened, or created, a different checkpoints database.
    if path is None:
        from kazma_core.paths import checkpoints_db

        path = str(checkpoints_db())

    # Route through the process-wide shared saver (audit M-G5) so the
    # server graphs and KazmaAgent.run() never hold two independent
    # writers on the same checkpoints.db; CheckpointManager adds the
    # per-thread logical locks on top.
    from kazma_core.checkpoints_shared import (
        get_shared_sqlite_saver,
        retain_shared_checkpoints,
    )

    saver = await get_shared_sqlite_saver(str(path))
    # Lifetime retention (audit M-G5): the server's CheckpointManager holds
    # this saver for the process lifetime and never releases — transient
    # holders (KazmaAgent) can release without closing it out from under
    # the live graphs. close() releases this retention.
    retain_shared_checkpoints(str(path))

    manager = CheckpointManager(saver)
    manager._shared_db_path = str(path)
    logger.info("[Checkpoint] CheckpointManager initialized at %s (per-thread locking)", path)
    return manager


# Backward-compatible alias
async def create_checkpointer(
    path: str | None = None,
) -> CheckpointManager:
    """Alias for create_checkpoint_manager (backward compatibility).

    ``None`` passes through so the default is resolved in exactly one place —
    ``create_checkpoint_manager`` — rather than duplicated into this signature,
    where it was a CWD-relative literal.
    """
    return await create_checkpoint_manager(path)
