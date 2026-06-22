"""Kazma Checkpoint Manager — Durable state persistence in SQLite.

Uses langgraph-checkpoint-sqlite for WAL-mode SQLite with crash-safe writes.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from kazma_core.state import AgentState

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "kazma-data/checkpoints.db"


class CheckpointManager:
    """Manages agent state checkpoints in SQLite.

    Each checkpoint is a full snapshot of the AgentState, stored as JSON
    in a WAL-mode SQLite database. WAL mode ensures crash safety:
    a SIGKILL mid-write either completes the old checkpoint or rolls back,
    never corrupts.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = str(db_path or DEFAULT_DB_PATH)
        self._saver: AsyncSqliteSaver | None = None
        self._conn: aiosqlite.Connection | None = None

    async def _ensure_saver(self) -> AsyncSqliteSaver:
        """Lazily initialize the AsyncSqliteSaver with direct connection."""
        if self._saver is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self._db_path)
            await self._conn.execute("PRAGMA journal_mode=WAL")
            await self._conn.execute("PRAGMA synchronous=NORMAL")
            self._saver = AsyncSqliteSaver(self._conn)
            await self._saver.setup()
        return self._saver

    def _config(self, thread_id: str) -> dict[str, Any]:
        """Create a LangGraph config dict for the given thread."""
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    async def save(self, state: AgentState) -> str:
        """Save state to SQLite, return checkpoint ID (last_cp_id).

        Generates a new UUID checkpoint_id and stores the full state
        as JSON. Uses langgraph's checkpoint protocol under the hood.
        """
        saver = await self._ensure_saver()

        cp_id = state.get("last_cp_id") or str(uuid.uuid4())
        state["last_cp_id"] = cp_id

        thread_id = state.get("provenance", {}).get("thread_id", str(uuid.uuid4()))
        config = self._config(thread_id)

        # Serialize the full state as the checkpoint payload
        checkpoint: dict[str, Any] = {
            "v": 1,
            "id": cp_id,
            "ts": state.get("created_at", ""),
            "channel_values": {
                "agent_state": json.dumps(state, default=str),
            },
            "channel_versions": {},
            "versions_seen": {},
        }

        metadata: dict[str, Any] = {
            "checkpoint_id": cp_id,
            "created_at": state.get("created_at", ""),
            "context_tokens": state.get("context_tokens", 0),
            "message_count": len(state.get("messages", [])),
        }

        await saver.aput(config, checkpoint, metadata, {})
        logger.info("Checkpoint saved: %s (thread=%s)", cp_id, thread_id)
        return cp_id

    async def load(self, checkpoint_id: str) -> AgentState:
        """Load state from SQLite by checkpoint ID.

        Uses a direct SQL query with index lookup for O(1) performance.
        Falls back to full scan if the column-based lookup doesn't find it.
        """
        saver = await self._ensure_saver()
        conn = self._conn
        assert conn is not None

        # Direct SQL lookup — try checkpoint_id column first
        try:
            cursor = await conn.execute(
                "SELECT checkpoint, metadata FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            row = await cursor.fetchone()
            if row is not None:
                raw_blob = row[0]
                # Deserialize: could be JSON str, bytes (msgpack), or dict
                if isinstance(raw_blob, bytes):
                    try:
                        checkpoint_data = json.loads(raw_blob.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        checkpoint_data = None
                elif isinstance(raw_blob, str):
                    checkpoint_data = json.loads(raw_blob)
                elif isinstance(raw_blob, dict):
                    checkpoint_data = raw_blob
                else:
                    checkpoint_data = None

                if checkpoint_data is not None:
                    raw = checkpoint_data.get("channel_values", {}).get("agent_state")
                    if raw:
                        state: AgentState = json.loads(raw)
                        logger.info("Checkpoint loaded: %s", checkpoint_id)
                        return state
        except Exception:
            logger.debug("Direct SQL lookup failed, falling back to scan")

        # Fallback: scan all (LangGraph's alist handles deserialization)
        async for cp_tuple in saver.alist(None):
            if cp_tuple.checkpoint.get("id") == checkpoint_id:
                raw = cp_tuple.checkpoint.get("channel_values", {}).get("agent_state")
                if raw:
                    state = json.loads(raw)
                    logger.info("Checkpoint loaded via scan: %s", checkpoint_id)
                    return state

        raise FileNotFoundError(f"Checkpoint {checkpoint_id} not found")

    async def load_latest(self) -> AgentState | None:
        """Load the most recent checkpoint, or None if no checkpoints exist."""
        checkpoints = await self.list_checkpoints(limit=1)
        if not checkpoints:
            return None
        return await self.load(checkpoints[0]["id"])

    async def list_checkpoints(self, limit: int = 10) -> list[dict[str, Any]]:
        """List recent checkpoints with metadata, sorted by creation time (newest first).

        Returns a list of dicts with id, created_at, context_tokens, message_count.
        """
        saver = await self._ensure_saver()

        results: list[dict[str, Any]] = []

        # alist(None) returns all checkpoints, but ordered by checkpoint_id (UUID),
        # not chronologically. We need to collect all and sort by created_at.
        async for cp_tuple in saver.alist(None):
            cp_id = cp_tuple.checkpoint.get("id", "")
            meta = cp_tuple.metadata or {}
            results.append(
                {
                    "id": cp_id,
                    "created_at": meta.get("created_at", ""),
                    "context_tokens": meta.get("context_tokens", 0),
                    "message_count": meta.get("message_count", 0),
                }
            )

        # Sort by created_at descending (newest first)
        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        return results[:limit]

    async def prune(self, keep_last: int = 100) -> int:
        """Prune old checkpoints, return count removed.

        Keeps the most recent `keep_last` checkpoints and deletes the rest.
        """
        await self._ensure_saver()

        # Fetch ALL checkpoints (no limit) so we can correctly identify old ones
        all_checkpoints = await self.list_checkpoints(limit=999_999)
        total = len(all_checkpoints)

        if total <= keep_last:
            return 0

        # Delete oldest checkpoints beyond keep_last
        ids_to_delete = [cp["id"] for cp in all_checkpoints[keep_last:]]
        conn = self._conn
        assert conn is not None
        for cp_id in ids_to_delete:
            await conn.execute("DELETE FROM checkpoints WHERE checkpoint_id = ?", (cp_id,))
        await conn.commit()

        removed = len(ids_to_delete)
        logger.info("Pruned %d checkpoints, kept %d", removed, keep_last)
        return removed

    async def close(self) -> None:
        """Clean up resources."""
        self._saver = None
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
