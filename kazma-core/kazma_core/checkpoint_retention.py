"""Checkpoint retention — bound the growth of the LangGraph checkpoint DB.

Audit M-G1: LangGraph persists a checkpoint on every superstep, each row a
full SupervisorState blob (messages + up to 200 tool results of 100K chars
each). Nothing ever deleted old rows — a mission-mode turn produces 1000+
supersteps — so ``checkpoints.db`` grew without bound until the operator
manually deleted chats.

Retention policy (SQLite backend):

* Per thread, keep the newest ``keep_per_thread`` checkpoints (default 200)
  — /undo, /replay and resume only need recent history.
* For threads whose newest checkpoint is older than ``inactive_days``
  (default 30), keep only their newest ``inactive_keep`` (default 10)
  checkpoints — the thread stays resumable, its ancient history does not.

Postgres deployments are skipped (ops-owned; noted once per process).

Kill-switch: ``KAZMA_CHECKPOINT_RETENTION_DAYS=0`` disables the loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "prune_checkpoint_db",
    "start_checkpoint_retention_loop",
    "stop_checkpoint_retention_loop",
]

DEFAULT_DB = "kazma-data/checkpoints.db"
_DEFAULT_KEEP_PER_THREAD = 200
_DEFAULT_INACTIVE_DAYS = 30
_DEFAULT_INACTIVE_KEEP = 10

_loop_task: asyncio.Task | None = None
_pg_warned = False


def _env_int(name: str, default: int) -> int:
    try:
        raw = (os.environ.get(name) or "").strip()
        return int(raw) if raw else default
    except ValueError:
        return default


def prune_checkpoint_db(
    db_path: str = DEFAULT_DB,
    *,
    keep_per_thread: int = _DEFAULT_KEEP_PER_THREAD,
    inactive_days: int = _DEFAULT_INACTIVE_DAYS,
    inactive_keep: int = _DEFAULT_INACTIVE_KEEP,
) -> dict[str, int]:
    """Prune old checkpoint rows. Returns ``{"rows": n, "threads": t}``.

    Policy: per thread, keep the newest ``keep_per_thread`` checkpoints
    (default 200) — /undo, /replay and resume only need recent history.
    Threads whose newest checkpoint is older than ``inactive_days`` keep
    only ``inactive_keep`` (they are resumable but dormant).

    Synchronous by design — callers wrap in ``asyncio.to_thread``. Never
    raises: any failure logs and returns zeros (retention must never take
    the chat path down).
    """
    if not Path(db_path).is_file():
        return {"rows": 0, "threads": 0}

    import sqlite3
    from datetime import datetime, timedelta, timezone

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=5000")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "checkpoints" not in tables:
            return {"rows": 0, "threads": 0}

        # Inactivity cutoff against the metadata ts LangGraph stamps on
        # every checkpoint. Threads with no readable ts keep the full cap.
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, inactive_days))).isoformat()

        rows = conn.execute(
            "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id"
        ).fetchall()
        deleted = 0
        threads_touched = 0
        for thread_id, count in rows:
            newest_ts_row = conn.execute(
                "SELECT json_extract(metadata, '$.ts') FROM checkpoints "
                "WHERE thread_id = ? ORDER BY checkpoint_id DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            newest_ts = str(newest_ts_row[0]) if newest_ts_row and newest_ts_row[0] else ""
            if newest_ts and newest_ts < cutoff:
                keep = max(1, inactive_keep)
            else:
                keep = max(1, keep_per_thread)
            if count <= keep:
                continue
            doomed = conn.execute(
                "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? "
                "ORDER BY checkpoint_id DESC LIMIT -1 OFFSET ?",
                (thread_id, keep),
            ).fetchall()
            if not doomed:
                continue
            ids = [r[0] for r in doomed]
            marks = ",".join("?" * len(ids))
            threads_touched += 1
            if "checkpoint_writes" in tables:
                cur = conn.execute(
                    f"DELETE FROM checkpoint_writes WHERE thread_id = ? "
                    f"AND checkpoint_id IN ({marks})",
                    (thread_id, *ids),
                )
                deleted += max(cur.rowcount or 0, 0)
            cur = conn.execute(
                f"DELETE FROM checkpoints WHERE thread_id = ? "
                f"AND checkpoint_id IN ({marks})",
                (thread_id, *ids),
            )
            deleted += max(cur.rowcount or 0, 0)
            # Blobs are keyed by (thread_id, channel, version) where version
            # IS the checkpoint id — remove versions belonging to doomed ids.
            if "checkpoint_blobs" in tables:
                cur = conn.execute(
                    f"DELETE FROM checkpoint_blobs WHERE thread_id = ? "
                    f"AND version IN ({marks})",
                    (thread_id, *ids),
                )
                deleted += max(cur.rowcount or 0, 0)
        if deleted:
            conn.commit()
            logger.info(
                "[checkpoint-retention] pruned %d rows across %d thread(s) in %s",
                deleted, threads_touched, db_path,
            )
        return {"rows": deleted, "threads": threads_touched}
    except Exception:
        logger.warning("[checkpoint-retention] prune failed", exc_info=True)
        return {"rows": 0, "threads": 0}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def start_checkpoint_retention_loop(db_path: str = DEFAULT_DB) -> None:
    """Daily retention sweep (audit M-G1). Idempotent; env kill-switch off."""
    global _loop_task
    if _env_int("KAZMA_CHECKPOINT_RETENTION_DAYS", _DEFAULT_INACTIVE_DAYS) <= 0:
        return
    if _loop_task is not None:
        return

    async def _loop() -> None:
        # First sweep 10 min after boot (avoid competing with startup IO),
        # then daily.
        await asyncio.sleep(600)
        while True:
            try:
                from kazma_core.db.backend import is_postgres

                if is_postgres():
                    global _pg_warned
                    if not _pg_warned:
                        _pg_warned = True
                        logger.info(
                            "[checkpoint-retention] Postgres backend — retention "
                            "skipped (ops-owned)"
                        )
                else:
                    await asyncio.to_thread(prune_checkpoint_db, db_path)
            except Exception:
                logger.debug("[checkpoint-retention] sweep failed", exc_info=True)
            await asyncio.sleep(86400)

    from kazma_core.background import spawn_background

    _loop_task = spawn_background(_loop(), name="checkpoint-retention")


def stop_checkpoint_retention_loop() -> None:
    """Stop the retention sweep (idempotent)."""
    global _loop_task
    if _loop_task is not None:
        _loop_task.cancel()
        _loop_task = None
