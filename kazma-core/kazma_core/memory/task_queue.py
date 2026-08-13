"""Durable task queue for V2 memory consolidation.

Replaces the volatile ``loop.create_task`` dispatch for heavy
consolidation work (belief extraction, entity merge, macro sleep) with
a SQLite-backed queue that survives crashes. Pending rows are reclaimed
on worker restart; failed tasks retry up to ``max_attempts`` then dead-letter.

Task types:
  - ``micro_consolidation`` — post-turn belief extraction + mutation
  - ``entity_merge``        — resolve a pending entity merge candidate
  - ``macro_sleep``         — decay/demote/compact idle-cycle sweep

The worker is a singleton with a bounded concurrency pool. It is
started lazily on first enqueue (or explicitly via :func:`start_worker`).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "enqueue_task",
    "get_worker",
    "reset_worker",
    "start_worker",
    "stop_worker",
]

# Reclaim threshold: a task stuck in 'processing' longer than this (seconds)
# is considered crashed and becomes eligible for re-claim.
_STUCK_THRESHOLD_SEC = 300.0  # 5 minutes

# Handler registry: task_type → async callable(payload) -> bool (success)
TaskHandler = Callable[[dict[str, Any]], Awaitable[bool]]
_HANDLERS: dict[str, TaskHandler] = {}


def register_handler(task_type: str, handler: TaskHandler) -> None:
    """Register an async handler for a task type."""
    _HANDLERS[task_type] = handler


# ── Enqueue ───────────────────────────────────────────────────────────────


def enqueue_task(
    task_type: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
) -> str | None:
    """Enqueue a durable task. Returns the task id, or None on failure.

    Best-effort: never raises — a queue failure logs and returns None so
    the caller (post-turn hook) is never blocked.
    """
    try:
        from kazma_core.config_store import apply_sqlite_pragmas
        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
        try:
            apply_sqlite_pragmas(conn)
            ensure_ops_schema(conn)
            tid = "t_" + uuid.uuid4().hex[:20]
            now = time.time()
            conn.execute(
                """INSERT INTO memory_task_queue
                   (id, task_type, payload_json, status, max_attempts, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?, ?)""",
                (tid, task_type, json.dumps(payload, ensure_ascii=False), max_attempts, now, now),
            )
            conn.commit()
            # Nudge the worker to wake up and poll
            try:
                get_worker()._wake.set()
            except Exception:
                pass
            return tid
        finally:
            conn.close()
    except Exception:
        logger.debug("[task_queue] enqueue failed for %s", task_type, exc_info=True)
        return None


# ── Worker ────────────────────────────────────────────────────────────────


class _MemoryWorker:
    """Async durable-task worker draining ``memory_ops.db``.

    Runs a bounded concurrency pool. On each tick it claims a batch of
    pending (or stuck-processing) tasks and dispatches them to registered
    handlers. Crashes are safe: unclaimed tasks remain pending.
    """

    def __init__(self, *, max_concurrency: int = 2, poll_interval: float = 2.0) -> None:
        self.max_concurrency = max_concurrency
        self.poll_interval = poll_interval
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running = False
        self._lock = threading.Lock()
        # Strong references to dispatched handler tasks. Without these, CPython
        # may garbage-collect a task mid-handler (the asyncio docs warn to keep
        # a reference), silently dropping a micro_consolidation/entity_merge
        # run (audit finding).
        self._inflight: set[asyncio.Task] = set()

    def start(self) -> None:
        """Start the background poll loop (idempotent)."""
        with self._lock:
            if self._running:
                return
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.debug("[task_queue] no running loop — worker start deferred")
                return
            self._running = True
            self._wake = asyncio.Event()
            self._task = loop.create_task(self._run())
            logger.info("[task_queue] worker started (concurrency=%d)", self.max_concurrency)

    async def stop(self) -> None:
        """Stop the worker and await in-flight tasks."""
        with self._lock:
            self._running = False
            self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        # Drain handler tasks that were dispatched but not yet finished, so a
        # handler mid-execution (e.g. an LLM belief extraction holding a SQLite
        # transaction) is awaited/cancelled rather than abandoned on loop close.
        if self._inflight:
            await asyncio.gather(*self._inflight, return_exceptions=True)
            self._inflight.clear()

    async def _run(self) -> None:
        """Main poll loop: claim → dispatch → ack/nack, repeat."""
        sem = asyncio.Semaphore(self.max_concurrency)
        while self._running:
            try:
                claimed = self._claim_batch()
                for task in claimed:
                    await sem.acquire()
                    t = asyncio.create_task(self._process(sem, task))
                    self._inflight.add(t)
                    t.add_done_callback(self._inflight.discard)
            except Exception:
                logger.debug("[task_queue] poll cycle failed", exc_info=True)
            # Wait for the next poll interval or an explicit wake
            try:
                self._wake.clear()
                await asyncio.wait_for(self._wake.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass

    def _claim_batch(self, batch_size: int = 8) -> list[dict[str, Any]]:
        """Atomically claim up to ``batch_size`` pending/stuck tasks."""
        try:
            from kazma_core.config_store import apply_sqlite_pragmas
            from kazma_core.paths import memory_ops_db

            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                apply_sqlite_pragmas(conn)
                now = time.time()
                stuck_cutoff = now - _STUCK_THRESHOLD_SEC
                # Claim pending tasks + reclaim stuck 'processing' ones
                rows = conn.execute(
                    """SELECT * FROM memory_task_queue
                       WHERE status = 'pending'
                          OR (status = 'processing' AND updated_at < ?)
                       ORDER BY created_at ASC LIMIT ?""",
                    (stuck_cutoff, batch_size),
                ).fetchall()
                claimed_ids = [r["id"] for r in rows]
                if not claimed_ids:
                    return []
                placeholders = ",".join("?" * len(claimed_ids))
                conn.execute(
                    f"""UPDATE memory_task_queue
                        SET status = 'processing', updated_at = ?
                        WHERE id IN ({placeholders})""",
                    [now, *claimed_ids],
                )
                conn.commit()
                return [dict(r) for r in rows]
            finally:
                conn.close()
        except Exception:
            logger.debug("[task_queue] claim failed", exc_info=True)
            return []

    async def _process(self, sem: asyncio.Semaphore, task: dict[str, Any]) -> None:
        """Dispatch one task to its handler; ack on success, retry/nack on failure."""
        try:
            task_type = task["task_type"]
            handler = _HANDLERS.get(task_type)
            if handler is None:
                logger.warning("[task_queue] no handler for type %s — failing", task_type)
                self._ack(task, success=False, error=f"no handler for {task_type}")
                return
            try:
                payload = json.loads(task["payload_json"] or "{}")
            except Exception:
                payload = {}
            success = await handler(payload)
            self._ack(task, success=bool(success), error=None if success else "handler returned False")
        except Exception as exc:
            self._ack(task, success=False, error=f"{type(exc).__name__}: {exc}")
        finally:
            sem.release()

    def _ack(self, task: dict[str, Any], *, success: bool, error: str | None) -> None:
        """Mark a task completed (success) or retry/dead-letter (failure)."""
        try:
            from kazma_core.config_store import apply_sqlite_pragmas
            from kazma_core.paths import memory_ops_db

            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            try:
                apply_sqlite_pragmas(conn)
                now = time.time()
                if success:
                    conn.execute(
                        "UPDATE memory_task_queue SET status='completed', updated_at=? WHERE id=?",
                        (now, task["id"]),
                    )
                else:
                    attempts = int(task.get("attempts", 0)) + 1
                    max_attempts = int(task.get("max_attempts", 3))
                    if attempts >= max_attempts:
                        conn.execute(
                            "UPDATE memory_task_queue SET status='failed', attempts=?, updated_at=?, error_log=? WHERE id=?",
                            (attempts, now, (error or "")[:1000], task["id"]),
                        )
                    else:
                        conn.execute(
                            "UPDATE memory_task_queue SET status='pending', attempts=?, updated_at=?, error_log=? WHERE id=?",
                            (attempts, now, (error or "")[:1000], task["id"]),
                        )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.debug("[task_queue] ack failed", exc_info=True)


# ── Singleton ─────────────────────────────────────────────────────────────

_worker: _MemoryWorker | None = None
_worker_lock = threading.Lock()


def get_worker() -> _MemoryWorker:
    """Return the process-wide memory worker singleton."""
    global _worker
    if _worker is not None:
        return _worker
    with _worker_lock:
        if _worker is None:
            _worker = _MemoryWorker()
        return _worker


def reset_worker() -> None:
    """Drop the singleton (tests)."""
    global _worker
    with _worker_lock:
        _worker = None


def start_worker() -> None:
    """Start the singleton worker (idempotent)."""
    get_worker().start()


async def stop_worker() -> None:
    """Stop the singleton worker."""
    global _worker
    with _worker_lock:
        w = _worker
    if w is not None:
        await w.stop()
