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
import logging
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import aiosqlite
from langchain_core.runnables import RunnableConfig
from kazma_core.config_store import apply_sqlite_pragmas_async
from kazma_core.tenant_context import get_current_tenant_id
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.serde._msgpack import SAFE_MSGPACK_TYPES

logger = logging.getLogger(__name__)

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

    async def _get_saver(self) -> AsyncSqliteSaver:
        """Resolve the appropriate AsyncSqliteSaver for the current tenant.

        If the tenant is "default" (or None), we use the default self._saver.
        Otherwise, we dynamically load or create an AsyncSqliteSaver for
        the tenant's own database checkpoints_{tenant_id}.db.
        """
        tenant_id = get_current_tenant_id() or "default"
        if tenant_id == "default":
            return self._saver

        async with self._saver_lock:
            if tenant_id not in self._tenant_savers:
                db_path = Path("kazma-data") / f"checkpoints_{tenant_id}.db"
                db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = await aiosqlite.connect(str(db_path))
                await apply_sqlite_pragmas_async(conn)
                
                # Copy the serde from self._saver or use JsonPlusSerializer with custom settings
                serde = getattr(self._saver, "serde", None)
                if serde is None:
                    serde = JsonPlusSerializer(
                        allowed_msgpack_modules=list(SAFE_MSGPACK_TYPES) + [
                            ("kazma_core.agent.state", "NodeName"),
                        ]
                    )
                
                saver = AsyncSqliteSaver(conn, serde=serde)
                await saver.setup()
                self._tenant_savers[tenant_id] = saver
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
            saver = await self._get_saver()
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
            saver = await self._get_saver()
            await saver.aput_writes(config, writes, task_id, task_path)

    async def aget(self, config: dict[str, Any]) -> Any:
        """Retrieve a checkpoint (read-only, no lock needed)."""
        saver = await self._get_saver()
        return await saver.aget(config)

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        """Retrieve a checkpoint tuple."""
        saver = await self._get_saver()
        return await saver.aget_tuple(config)

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints for a thread."""
        lock = self._get_lock(thread_id)
        async with lock:
            saver = await self._get_saver()
            if hasattr(saver, "adelete_thread"):
                await saver.adelete_thread(thread_id)

    async def setup(self) -> None:
        """Initialize the underlying saver."""
        await self._saver.setup()

    @property
    def conn(self) -> Any:
        """Expose the underlying connection."""
        tenant_id = get_current_tenant_id() or "default"
        if tenant_id == "default":
            return self._saver.conn if hasattr(self._saver, "conn") else None
        saver = self._tenant_savers.get(tenant_id)
        return saver.conn if saver and hasattr(saver, "conn") else None

    async def close(self) -> None:
        """Close the underlying database connection."""
        if hasattr(self._saver, "conn") and self._saver.conn:
            await self._saver.conn.close()
        for saver in self._tenant_savers.values():
            if hasattr(saver, "conn") and saver.conn:
                try:
                    await saver.conn.close()
                except Exception:
                    pass
        self._tenant_savers.clear()

    async def list_checkpoints(self, limit: int = 50) -> list[dict[str, Any]]:
        """List checkpointed threads with their latest checkpoint metadata.

        Queries the underlying checkpoint store for distinct thread_ids
        and returns summary info for each. Supports both SQLite
        (AsyncSqliteSaver) and Postgres (AsyncPostgresSaver) backends.

        Args:
            limit: Maximum number of threads to return.

        Returns:
            List of dicts with keys: thread_id, checkpoint_id, created_at,
            message_count, context_tokens.
        """
        saver = await self._get_saver()
        saver_type = type(saver).__name__

        # ── Postgres backend ──────────────────────────────────────────
        if "Postgres" in saver_type:
            return await self._list_checkpoints_postgres(saver, limit)

        # ── SQLite backend (default) ──────────────────────────────────
        conn = saver.conn if hasattr(saver, "conn") else None
        if conn is None:
            logger.warning(
                "[Checkpoint] list_checkpoints: saver has no conn (type=%s)",
                saver_type,
            )
            return []
        try:
            cursor = await conn.execute(
                """
                SELECT
                    thread_id,
                    checkpoint_id,
                    COALESCE(type, '') AS type
                FROM (
                    SELECT
                        thread_id,
                        checkpoint_id,
                        type,
                        ROW_NUMBER() OVER (
                            PARTITION BY thread_id
                            ORDER BY checkpoint_id DESC
                        ) AS rn
                    FROM checkpoints
                )
                WHERE rn = 1
                ORDER BY checkpoint_id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            results: list[dict[str, Any]] = []
            for row in rows:
                thread_id = row[0]
                checkpoint_id = row[1]
                msg_count = 0
                created_at = ""
                try:
                    blob_cursor = await conn.execute(
                        "SELECT checkpoint, metadata FROM checkpoints "
                        "WHERE thread_id = ? AND checkpoint_id = ? LIMIT 1",
                        (thread_id, checkpoint_id),
                    )
                    blob_row = await blob_cursor.fetchone()
                    if blob_row and blob_row[0]:
                        msg_count = self._try_decode_message_count(blob_row[0])
                    if blob_row and blob_row[1]:
                        created_at = self._try_decode_created_at(blob_row[1])
                except Exception as exc:
                    logger.debug("Checkpoint blob decode failed for thread %s: %s", thread_id, exc)
                results.append({
                    "thread_id": thread_id,
                    "checkpoint_id": str(checkpoint_id),
                    "created_at": created_at,
                    "message_count": msg_count,
                    "context_tokens": 0,
                })
            return results
        except Exception:
            logger.warning("[Checkpoint] list_checkpoints query failed", exc_info=True)
            return []

    async def _list_checkpoints_postgres(
        self, saver: Any, limit: int
    ) -> list[dict[str, Any]]:
        """Postgres variant of list_checkpoints using the AsyncConnectionPool.

        The ``AsyncPostgresSaver`` stores its pool in ``saver.conn`` (an
        ``AsyncConnectionPool``). We acquire a connection from the pool,
        run the equivalent query with ``%s`` placeholders, and decode
        blobs the same way as the SQLite path.
        """
        pool = saver.conn if hasattr(saver, "conn") else None
        if pool is None:
            return []
        try:
            async with pool.connection() as conn:  # type: ignore[union-attr]
                async with conn.cursor() as cur:  # type: ignore[union-attr]
                    await cur.execute(
                        """
                        SELECT thread_id, checkpoint_id
                        FROM (
                            SELECT
                                thread_id,
                                checkpoint_id,
                                ROW_NUMBER() OVER (
                                    PARTITION BY thread_id
                                    ORDER BY checkpoint_id DESC
                                ) AS rn
                            FROM checkpoints
                        ) sub
                        WHERE rn = 1
                        ORDER BY checkpoint_id DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                    rows = await cur.fetchall()

                results: list[dict[str, Any]] = []
                for row in rows:
                    # psycopg dict_row returns dict; aiosqlite returns tuple.
                    # Support both for safety.
                    if isinstance(row, dict):
                        thread_id = row["thread_id"]
                        checkpoint_id = row["checkpoint_id"]
                    else:
                        thread_id = row[0]
                        checkpoint_id = row[1]
                    msg_count = 0
                    created_at = ""
                    try:
                        async with conn.cursor() as bcur:  # type: ignore[union-attr]
                            await bcur.execute(
                                "SELECT checkpoint, metadata FROM checkpoints "
                                "WHERE thread_id = %s AND checkpoint_id = %s LIMIT 1",
                                (thread_id, checkpoint_id),
                            )
                            blob_row = await bcur.fetchone()
                        _blob = blob_row.get("checkpoint") if isinstance(blob_row, dict) else (blob_row[0] if blob_row else None)
                        _meta = blob_row.get("metadata") if isinstance(blob_row, dict) else (blob_row[1] if blob_row else None)
                        if _blob:
                            if isinstance(_blob, memoryview):
                                _blob = bytes(_blob)
                            msg_count = self._try_decode_message_count(_blob)
                        if _meta:
                            if isinstance(_meta, memoryview):
                                _meta = bytes(_meta)
                            created_at = self._try_decode_created_at(_meta)
                    except Exception as exc:
                        logger.debug("Checkpoint blob decode failed for thread %s: %s", thread_id, exc)
                    results.append({
                        "thread_id": thread_id,
                        "checkpoint_id": str(checkpoint_id),
                        "created_at": created_at,
                        "message_count": msg_count,
                        "context_tokens": 0,
                    })
                return results
        except Exception:
            logger.warning("[Checkpoint] list_checkpoints (postgres) query failed", exc_info=True)
            return []

    @staticmethod
    def _try_decode_message_count(blob: bytes) -> int:
        """Decode message count from LangGraph checkpoint blob.

        Tries msgpack first (LangGraph's serde), then JSON as fallback.
        Returns 0 on any decode failure.
        """
        try:
            import msgpack
            cp_data = msgpack.unpackb(blob, raw=False)
        except Exception:
            try:
                import json
                cp_data = json.loads(blob if isinstance(blob, str) else blob.decode("utf-8", errors="replace"))
            except Exception:
                return 0
        msgs = cp_data.get("channel_values", {}).get("messages", [])
        return len(msgs) if isinstance(msgs, list) else 0

    @staticmethod
    def _try_decode_created_at(metadata_blob: bytes | str) -> str:
        """Best-effort extraction of a created-at timestamp from metadata.

        LangGraph stores ``metadata`` as JSON.  Standard savers do not
        record a timestamp, but custom metadata or tracing integrations
        may include ``created_at``/``ts``/``timestamp``.  Returns ``""``
        when no timestamp is found.
        """
        import json

        try:
            raw = metadata_blob if isinstance(metadata_blob, str) else metadata_blob.decode("utf-8", errors="replace")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return ""
            for key in ("created_at", "ts", "timestamp", "created"):
                val = data.get(key)
                if isinstance(val, str) and val:
                    return val
        except Exception:
            return ""
        return ""

    @property
    def active_locks(self) -> int:
        """Number of thread locks currently held."""
        return len(self._locks)


async def create_checkpoint_manager(
    path: str = "kazma-data/checkpoints.db",
) -> CheckpointManager:
    """Create and initialize a CheckpointManager with per-thread locking.

    Backend:
      * Postgres when ``KAZMA_DATABASE_URL`` is set (requires
        ``langgraph-checkpoint-postgres`` + ``psycopg``).
      * SQLite otherwise (``path``).

    Returns:
        Initialized CheckpointManager ready for graph.compile(checkpointer=...).
    """
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=list(SAFE_MSGPACK_TYPES) + [
            ("kazma_core.agent.state", "NodeName"),
        ]
    )

    # ── Postgres checkpointer (multi-replica) ──────────────────────
    try:
        from kazma_core.db.backend import get_database_url, is_postgres

        if is_postgres():
            dsn = get_database_url() or ""
            if dsn.startswith("postgres://"):
                dsn = "postgresql://" + dsn[len("postgres://") :]
            try:
                from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore
                from psycopg.rows import dict_row  # type: ignore
                from psycopg_pool import AsyncConnectionPool  # type: ignore

                # LangGraph setup() may run CREATE INDEX CONCURRENTLY, which
                # requires autocommit (not a transaction block). Official
                # from_conn_string() uses autocommit=True; the pool must match.
                pool = AsyncConnectionPool(
                    conninfo=dsn,
                    min_size=1,
                    max_size=10,
                    kwargs={
                        "autocommit": True,
                        "prepare_threshold": 0,
                        "row_factory": dict_row,
                    },
                    open=False,
                )
                await pool.open()
                saver = AsyncPostgresSaver(conn=pool, serde=serde)  # type: ignore[arg-type]

                if hasattr(saver, "setup"):
                    await saver.setup()
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
    db_path = Path(path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(db_path))
    await apply_sqlite_pragmas_async(conn)

    saver = AsyncSqliteSaver(conn, serde=serde)
    await saver.setup()

    manager = CheckpointManager(saver)
    logger.info("[Checkpoint] CheckpointManager initialized at %s (per-thread locking)", db_path)
    return manager


# Backward-compatible alias
async def create_checkpointer(
    path: str = "kazma-data/checkpoints.db",
) -> CheckpointManager:
    """Alias for create_checkpoint_manager (backward compatibility)."""
    return await create_checkpoint_manager(path)
