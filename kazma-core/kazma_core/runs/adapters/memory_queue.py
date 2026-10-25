"""Memory queue tasks as runs.

Source of truth: the durable memory task queue (``memory_ops.db``,
``memory_task_queue``). Listed: the tasks being run, the ones waiting to run
again after a failure (their backoff, M12), and the dead letters. A task done
the first time is routine and not listed (post-turn memory runs every turn).

A ``processing`` task's owner is the memory worker; one untouched past the
reclaim threshold has lost it (the worker reclaims it, §15D). Dead letters
already raise the ``memory.dead_letter`` alert. Retry is the Memory page's
(``task_queue.retry_failed_task``).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any

from kazma_core.runs.adapters._common import short_title
from kazma_core.runs.contract import ActionResult, Run, RunState

__all__ = ["ADAPTER"]


def _ops_db() -> str:
    """Where the memory task queue lives."""
    from kazma_core.paths import memory_ops_db

    return memory_ops_db()


class _MemoryQueueAdapter:
    STATE_MAP: dict[str, str] = {
        "pending": RunState.QUEUED,
        "processing": RunState.RUNNING,
        "completed": RunState.DONE,
        "failed": RunState.FAILED,
    }

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        return await asyncio.to_thread(self._read, now, window_s)

    def _read(self, now: float, window_s: float) -> list[Run]:
        from kazma_core.memory.task_queue import _STUCK_THRESHOLD_SEC

        path = _ops_db()
        if not os.path.exists(path):
            return []
        conn = sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT id, task_type, status, attempts, max_attempts, created_at, updated_at, "
                "error_log, next_attempt_at FROM memory_task_queue "
                "WHERE status = 'processing' OR (status = 'pending' AND attempts > 0) "
                "OR (status = 'failed' AND updated_at >= ?) ORDER BY updated_at DESC LIMIT 200",
                (now - window_s,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc):
                return []
            raise
        finally:
            conn.close()
        return [self._run(row, now, _STUCK_THRESHOLD_SEC) for row in rows]

    def _run(self, row: Any, now: float, stuck_after: float) -> Run:
        native = str(row["status"])
        state = RunState(self.STATE_MAP.get(native, RunState.UNKNOWN))
        updated = float(row["updated_at"] or 0.0) or None
        owner = None
        if native == "processing":
            owner = updated is not None and now - updated < stuck_after
        return Run(
            run_id=f"memory_task:{row['id']}", kind="memory_task", native_id=row["id"],
            title=str(row["task_type"]), state=state, native_state=native,
            started_at=float(row["created_at"] or 0.0) or None, progress_at=updated,
            progress_note=f"attempt {int(row['attempts'] or 0)} of {int(row['max_attempts'] or 0)}",
            owner_alive=owner,
            deadline_at=float(row["next_attempt_at"]) if row["next_attempt_at"] else None,
            ended_at=updated if native == "failed" else None,
            version=str(updated or ""),
            actions=("retry",) if native == "failed" else (),
            link="/memory",
            detail=short_title(row["error_log"] or "", 200) if native == "failed" else "",
        )

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        if action != "retry":
            return ActionResult(False, "not_offered", f"Memory tasks do not take {action}.")
        from kazma_core.memory.task_queue import retry_failed_task

        if await asyncio.to_thread(retry_failed_task, native_id):
            return ActionResult(True, "retried", "The task runs again.")
        return ActionResult(False, "not_found", "The task is no longer waiting for a retry.")


ADAPTER = _MemoryQueueAdapter()
