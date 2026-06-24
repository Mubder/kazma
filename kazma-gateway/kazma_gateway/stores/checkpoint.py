"""LangGraph SQLite checkpointer factory.

Produces an AsyncSqliteSaver from langgraph-checkpoint-sqlite.
The checkpointer stores graph state (messages, tool results, supervisor
routing) so agent conversations survive server restarts.

Usage:
    checkpointer = await create_checkpointer()
    graph = builder.compile(checkpointer=checkpointer)
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)


async def create_checkpointer(
    path: str = "kazma-data/checkpoints.db",
) -> AsyncSqliteSaver:
    """Create and initialize an AsyncSqliteSaver checkpointer.

    Opens an aiosqlite connection with WAL journal mode for concurrent
    reads during graph execution. Creates the parent directory and
    database file if they don't exist.

    Args:
        path: Path to the SQLite checkpoint database.
              Defaults to kazma-data/checkpoints.db in the current directory.

    Returns:
        Initialized AsyncSqliteSaver ready for graph.compile(checkpointer=...).
    """
    db_path = Path(path).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(db_path))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")

    saver = AsyncSqliteSaver(conn)
    await saver.setup()

    logger.info("[Checkpoint] SQLite checkpointer initialized at %s", db_path)
    return saver
