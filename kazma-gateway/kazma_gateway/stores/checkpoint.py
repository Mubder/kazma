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
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Thread-safe wrapper around AsyncSqliteSaver.

    Prevents race conditions during concurrent writes to the same
    thread_id by acquiring a per-thread asyncio.Lock before save.

    Args:
        saver: The underlying AsyncSqliteSaver instance.
    """

    def __init__(self, saver: AsyncSqliteSaver) -> None:
        self._saver = saver
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, thread_id: str) -> asyncio.Lock:
        """Get or create a lock for a specific thread_id."""
        if thread_id not in self._locks:
            self._locks[thread_id] = asyncio.Lock()
        return self._locks[thread_id]

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
