"""Canonical L3 ``memories`` / ``memories_fts`` schema helpers.

Both the sync degrade path (``FTS5Memory``) and the async adapter backend
(``SQLiteMemoryBackend``) must install **identical** FTS sync triggers.

Historical bug: some installs used FTS5 special-command delete triggers
(``INSERT INTO fts(fts, ...) VALUES('delete', ...)``) that raise
``SQL logic error`` on every UPDATE of ``memories`` — leaving timestamps
and embeddings impossible to write. We always reinstall the safe
``DELETE FROM fts WHERE memory_id = ...`` form.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = [
    "MEMORIES_TABLE_SQL",
    "MEMORIES_FTS_SQL",
    "ensure_memories_schema_async",
    "ensure_memories_schema_sync",
    "install_memories_fts_triggers_async",
    "install_memories_fts_triggers_sync",
]

logger = logging.getLogger(__name__)

MEMORIES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    content_arabic TEXT,
    metadata TEXT DEFAULT '{}',
    timestamp INTEGER DEFAULT 0,
    source TEXT DEFAULT '',
    relevance REAL DEFAULT 1.0,
    embedding BLOB,
    tenant_id TEXT
)
"""

MEMORIES_FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
USING fts5(memory_id, content, content_arabic)
"""

# Safe triggers — DELETE BY memory_id (string PK), never FTS5 'delete' command.
_TRIGGER_AI = """
CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(memory_id, content, content_arabic)
    VALUES (new.id, new.content, COALESCE(new.content_arabic, ''));
END
"""

_TRIGGER_AD = """
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    DELETE FROM memories_fts WHERE memory_id = old.id;
END
"""

_TRIGGER_AU = """
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    DELETE FROM memories_fts WHERE memory_id = old.id;
    INSERT INTO memories_fts(memory_id, content, content_arabic)
    VALUES (new.id, new.content, COALESCE(new.content_arabic, ''));
END
"""


def install_memories_fts_triggers_sync(conn: Any) -> None:
    """Drop and recreate FTS sync triggers (sync sqlite3 connection)."""
    for name in ("memories_ai", "memories_ad", "memories_au"):
        try:
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        except Exception:
            pass
    conn.execute(_TRIGGER_AI)
    conn.execute(_TRIGGER_AD)
    conn.execute(_TRIGGER_AU)


async def install_memories_fts_triggers_async(conn: Any) -> None:
    """Drop and recreate FTS sync triggers (aiosqlite connection)."""
    for name in ("memories_ai", "memories_ad", "memories_au"):
        try:
            await conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        except Exception:
            pass
    await conn.execute(_TRIGGER_AI)
    await conn.execute(_TRIGGER_AD)
    await conn.execute(_TRIGGER_AU)


def ensure_memories_schema_sync(conn: Any) -> None:
    """Create table + FTS + safe triggers on a sync connection."""
    conn.execute(MEMORIES_TABLE_SQL)
    for col_sql in (
        "ALTER TABLE memories ADD COLUMN tenant_id TEXT",
        "ALTER TABLE memories ADD COLUMN embedding BLOB",
        "ALTER TABLE memories ADD COLUMN content_arabic TEXT",
    ):
        try:
            conn.execute(col_sql)
        except Exception:
            pass
    conn.execute(MEMORIES_FTS_SQL)
    install_memories_fts_triggers_sync(conn)
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)"
        )
    except Exception:
        pass
    conn.commit()


async def ensure_memories_schema_async(conn: Any) -> None:
    """Create table + FTS + safe triggers on an aiosqlite connection."""
    await conn.execute(MEMORIES_TABLE_SQL)
    for col_sql in (
        "ALTER TABLE memories ADD COLUMN tenant_id TEXT",
        "ALTER TABLE memories ADD COLUMN embedding BLOB",
        "ALTER TABLE memories ADD COLUMN content_arabic TEXT",
    ):
        try:
            await conn.execute(col_sql)
        except Exception:
            pass
    await conn.execute(MEMORIES_FTS_SQL)
    await install_memories_fts_triggers_async(conn)
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp)"
        )
    except Exception:
        pass
    await conn.commit()
    logger.debug("[schema] memories / memories_fts triggers reinstalled (async)")
