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
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.base import BaseCheckpointSaver
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

    async def aput(self, config: dict[str, Any], checkpoint: Any, metadata: Any, new_versions: Any) -> dict[str, Any]:
        """Save a checkpoint with per-thread locking.

        Extracts thread_id from config["configurable"]["thread_id"]
        and acquires the corresponding lock before writing.
        """
        thread_id = config.get("configurable", {}).get("thread_id", "default")
        lock = self._get_lock(thread_id)

        async with lock:
            return await self._saver.aput(config, checkpoint, metadata, new_versions)

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
