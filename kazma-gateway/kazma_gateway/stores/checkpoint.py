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
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
)
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

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

    def _get_lock(self, thread_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific thread_id.

        Uses LRU ordering: existing entries are moved to the end
        (most-recently-used) and the oldest entry is evicted when the
        bound is exceeded.
        """
        lock = self._locks.get(thread_id)
        if lock is not None:
            # LRU: mark as most-recently-used.
            self._locks.move_to_end(thread_id)
            return lock
        lock = asyncio.Lock()
        self._locks[thread_id] = lock
        # Evict oldest entries when the bound is exceeded.
        while len(self._locks) > self._max_locks:
            self._locks.popitem(last=False)
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
            return await self._saver.aput(config, checkpoint, metadata, new_versions)

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
            await self._saver.aput_writes(config, writes, task_id, task_path)

    async def aget(self, config: dict[str, Any]) -> Any:
        """Retrieve a checkpoint (read-only, no lock needed)."""
        return await self._saver.aget(config)

    async def aget_tuple(self, config: dict[str, Any]) -> Any:
        """Retrieve a checkpoint tuple."""
        return await self._saver.aget_tuple(config)

    async def adelete_thread(self, thread_id: str) -> None:
        """Delete all checkpoints for a thread."""
        lock = self._get_lock(thread_id)
        async with lock:
            if hasattr(self._saver, "adelete_thread"):
                await self._saver.adelete_thread(thread_id)

    async def setup(self) -> None:
        """Initialize the underlying saver."""
        await self._saver.setup()

    @property
    def conn(self) -> Any:
        """Expose the underlying connection."""
        return self._saver.conn if hasattr(self._saver, "conn") else None

    async def close(self) -> None:
        """Close the underlying database connection."""
        if hasattr(self._saver, "conn") and self._saver.conn:
            await self._saver.conn.close()

    async def list_checkpoints(self, limit: int = 50) -> list[dict[str, Any]]:
        """List checkpointed threads with their latest checkpoint metadata.

        Queries the underlying checkpoint store for distinct thread_ids
        and returns summary info for each.

        Args:
            limit: Maximum number of threads to return.

        Returns:
            List of dicts with keys: thread_id, checkpoint_id, created_at,
            message_count, context_tokens.
        """
        conn = self.conn
        if conn is None:
            return []
        try:
            # Each checkpoint row stores metadata as JSON in the
            # ``checkpoint`` column.  We pick the latest checkpoint per
            # thread (highest checkpoint_id) so the dashboard shows the
            # most recent state.
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
                # Try to extract message count from the checkpoint blob
                msg_count = 0
                try:
                    blob_cursor = await conn.execute(
                        "SELECT checkpoint FROM checkpoints "
                        "WHERE thread_id = ? AND checkpoint_id = ? LIMIT 1",
                        (thread_id, checkpoint_id),
                    )
                    blob_row = await blob_cursor.fetchone()
                    if blob_row and blob_row[0]:
                        msg_count = self._try_decode_message_count(blob_row[0])
                except Exception:
                    pass
                results.append({
                    "thread_id": thread_id,
                    "checkpoint_id": str(checkpoint_id),
                    "created_at": "",
                    "message_count": msg_count,
                    "context_tokens": 0,
                })
            return results
        except Exception:
            logger.debug("[Checkpoint] list_checkpoints query failed", exc_info=True)
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

    @property
    def active_locks(self) -> int:
        """Number of thread locks currently held."""
        return len(self._locks)


async def create_checkpoint_manager(
    path: str = "kazma-data/checkpoints.db",
) -> CheckpointManager:
    """Create and initialize a CheckpointManager with per-thread locking.

    Opens an aiosqlite connection with WAL journal mode for concurrent
    reads during graph execution. Creates the parent directory and
    database file if they don't exist.

    Args:
        path: Path to the SQLite checkpoint database.

    Returns:
        Initialized CheckpointManager ready for graph.compile(checkpointer=...).
    """
    db_path = Path(path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")

    saver = AsyncSqliteSaver(conn)
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
