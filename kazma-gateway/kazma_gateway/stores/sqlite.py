"""SQLite-backed session store for platform context persistence.

Stores thread_id → context_metadata mappings in a local SQLite database
via aiosqlite. Survives server restarts. Platform-specific IDs (chat_id,
user_id, etc.) live here — NEVER in graph state.

Usage:
    store = SQLiteSessionStore("kazma-data/sessions.db")
    await store.put("thread-123", {"chat_id": 999, "user_id": 555})
    ctx = await store.get("thread-123")  # {"chat_id": 999, ...}
    await store.delete("thread-123")
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import aiosqlite

from kazma_gateway.gateway import SessionStore

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id TEXT PRIMARY KEY,
    context   TEXT NOT NULL,
    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
);
"""

_GET_BY_THREAD = "SELECT context FROM sessions WHERE thread_id = ?"
_UPSERT = "INSERT OR REPLACE INTO sessions (thread_id, context, updated_at) VALUES (?, ?, strftime('%s', 'now'))"
_DELETE = "DELETE FROM sessions WHERE thread_id = ?"
_EVICT_OLDER_THAN = "DELETE FROM sessions WHERE updated_at < ?"


class SQLiteSessionStore(SessionStore):
    """Persistent session store backed by SQLite.

    Thread-safe via aiosqlite's async interface. Creates the database
    and table on first use.

    Args:
        db_path: Path to the SQLite database file. Created if it doesn't exist.
                 Use ":memory:" for testing.
    """

    def __init__(self, db_path: str = "kazma-data/sessions.db") -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _ensure_db(self) -> aiosqlite.Connection:
        """Open the database and create the table if needed."""
        if self._db is not None:
            return self._db

        # Ensure parent directory exists
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute(_CREATE_TABLE)
        await self._db.commit()
        logger.info("[SQLiteSessionStore] Opened %s", self._db_path)
        return self._db

    async def get(self, thread_id: str) -> dict[str, Any]:
        """Retrieve context_metadata for a thread_id.

        Returns empty dict if not found.
        """
        db = await self._ensure_db()
        async with db.execute(_GET_BY_THREAD, (thread_id,)) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return {}
            try:
                return json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                logger.warning("[SQLiteSessionStore] Corrupt context for %s", thread_id)
                return {}

    async def put(self, thread_id: str, context: dict[str, Any]) -> None:
        """Store context_metadata for a thread_id (upsert)."""
        db = await self._ensure_db()
        serialized = json.dumps(context, ensure_ascii=False)
        await db.execute(_UPSERT, (thread_id, serialized))
        await db.commit()

    async def delete(self, thread_id: str) -> None:
        """Remove stored context for a thread_id. No-op if not found."""
        db = await self._ensure_db()
        await db.execute(_DELETE, (thread_id,))
        await db.commit()

    async def evict_older_than(self, seconds: float) -> int:
        """Evict session entries whose ``updated_at`` is older than ``seconds``.

        Implements TTL-based eviction so that session entries persist across
        agent replies (for crash-recovery routing) while still bounding the
        store size over time.

        Args:
            seconds: TTL in seconds. Entries not updated within this window
                     are removed.

        Returns:
            Number of entries evicted.
        """
        db = await self._ensure_db()
        cutoff = int(time.time()) - int(seconds)
        cursor = await db.execute(_EVICT_OLDER_THAN, (cutoff,))
        await db.commit()
        return cursor.rowcount or 0

    async def list_active(self) -> list[dict[str, Any]]:
        """List all stored sessions with their metadata.

        Returns:
            List of dicts with thread_id, context fields, and updated_at.
        """
        db = await self._ensure_db()
        rows: list[dict[str, Any]] = []
        async with db.execute("SELECT thread_id, context, updated_at FROM sessions") as cursor:
            async for row in cursor:
                try:
                    ctx = json.loads(row[1])
                except (json.JSONDecodeError, TypeError):
                    ctx = {}
                rows.append(
                    {
                        "thread_id": row[0],
                        "updated_at": row[2],
                        "platform": ctx.get("platform", "unknown"),
                        "display_name": ctx.get("username", "unknown"),
                        **ctx,
                    }
                )
        return rows

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("[SQLiteSessionStore] Closed")
