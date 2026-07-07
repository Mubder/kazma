"""SSE streaming endpoint for swarm task progress.

Provides GET /api/swarm/tasks/{id}/stream which emits Server-Sent Events as a
task progresses through its lifecycle.  The event bus collects per-task event
history so late subscribers receive catch-up events on reconnect.

Event contract:
  event: task_started    data: {"task_id": "...", "workers": [...]}
  event: worker_started  data: {"worker": "...", "step": N}
  event: worker_progress data: {"worker": "...", "tokens": N}
  event: worker_completed data: {"worker": "...", "status": "...", "output_preview": "..."}
  event: checkpoint      data: {"step": N, "needs_approval": true, "output_preview": "..."}
  event: handoff         data: {"from": "...", "to": "..."}
  event: task_completed  data: {"task_id": "...", "result": {...}}
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from kazma_core.swarm.engine import get_swarm_engine

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# SSE frame helper (imported from shared utility)
# ══════════════════════════════════════════════════════════════════════════

from kazma_ui.sse_utils import sse_frame as _sse_frame


# ══════════════════════════════════════════════════════════════════════════
# SSE Event Bus — per-task event pub/sub with history
# ══════════════════════════════════════════════════════════════════════════

_TERMINAL_STATUSES = frozenset({"completed", "failed", "timeout"})


class SSEEventBus:
    """Per-task event pub/sub bus with history for catch-up replays.

    The bus stores emitted events in a per-task history list so that late
    subscribers (reconnecting clients) can replay missed events. History
    for terminal tasks is pruned periodically to avoid unbounded growth.
    """

    def __init__(self, max_history_per_task: int = 1000) -> None:
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._max_history_per_task = max_history_per_task
        self._last_cleanup_time = 0.0
        self._last_update_time: dict[str, float] = {}
        # No lock needed: all methods are sync and run in asyncio's
        # single-threaded event loop, so there are no interleaving points.

    def emit(self, task_id: str, event: str, data: dict[str, Any]) -> None:
        """Emit an event for a task.

        Stores the event in history and delivers it to all active
        subscribers for the given task_id.
        """
        entry: dict[str, Any] = {"event": event, "data": data}
        self._last_update_time[task_id] = time.time()

        # Store in history with bounded size to avoid memory growth.
        if task_id not in self._history:
            self._history[task_id] = []
        history = self._history[task_id]
        history.append(entry)
        if len(history) > self._max_history_per_task:
            self._history[task_id] = history[-self._max_history_per_task :]

        # Deliver to active subscribers.
        for queue in self._subscribers.get(task_id, []):
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                logger.warning(
                    "[SSE] subscriber queue full for task '%s', dropping event '%s'",
                    task_id,
                    event,
                )

        # Periodic cleanup of old terminal history (roughly every 60s).
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                now = loop.time()
                if now - self._last_cleanup_time > 60:
                    self._cleanup_terminal_history()
                    self._last_cleanup_time = now
        except RuntimeError as exc:
            logger.debug("SSE event loop cleanup skipped: %s", exc)

    def _cleanup_terminal_history(self) -> None:
        """Remove history for terminal tasks that have no subscribers, or older than TTL."""
        terminal_events = frozenset({"task_completed", "task_failed"})
        tasks_to_remove: list[str] = []
        now = time.time()
        ttl_seconds = 3600.0  # 1 hour TTL limit to prevent unbounded memory growth

        for task_id, history in list(self._history.items()):
            # Unconditional TTL check
            last_update = self._last_update_time.get(task_id, 0.0)
            if last_update > 0.0 and (now - last_update > ttl_seconds):
                tasks_to_remove.append(task_id)
                continue

            # Existing subscribers guard
            if self._subscribers.get(task_id):
                continue

            if not history:
                tasks_to_remove.append(task_id)
                continue

            last_event = history[-1].get("event")
            if last_event in terminal_events:
                tasks_to_remove.append(task_id)

        for task_id in tasks_to_remove:
            self._history.pop(task_id, None)
            self._last_update_time.pop(task_id, None)

    def subscribe(self, task_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to live events for a task.

        Returns an asyncio.Queue that receives event dicts as they are
        emitted.  Use ``get_history`` to replay past events first.
        """
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        if task_id not in self._subscribers:
            self._subscribers[task_id] = []
        self._subscribers[task_id].append(queue)
        return queue

    def unsubscribe(self, task_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Remove a subscriber queue."""
        subs = self._subscribers.get(task_id, [])
        if queue in subs:
            subs.remove(queue)
        if not subs:
            self._subscribers.pop(task_id, None)

    def get_history(self, task_id: str) -> list[dict[str, Any]]:
        """Return historical events for a task (for catch-up replays)."""
        return list(self._history.get(task_id, []))


# ══════════════════════════════════════════════════════════════════════════
# SSE streaming endpoint
# ══════════════════════════════════════════════════════════════════════════


def create_sse_router(*, event_bus: SSEEventBus | None = None) -> APIRouter:
    """Create the SSE streaming router.

    Args:
        event_bus: The SSEEventBus instance.  Created internally if not
            provided.

    Returns:
        APIRouter with GET /api/swarm/tasks/{id}/stream registered.
    """
    bus = event_bus or SSEEventBus()
    router = APIRouter(tags=["swarm-sse"])

    @router.get("/api/swarm/tasks/{task_id}/stream")
    async def stream_task_events(task_id: str) -> StreamingResponse:
        """Stream SSE events for a swarm task.

        For terminal tasks (completed/failed/timeout), replays the full
        event history and closes the stream.  For active tasks, replays
        history then streams live events until a terminal event arrives.
        """
        engine = get_swarm_engine()
        if engine is None:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"status": "error", "message": "Swarm engine not available"},
                status_code=404,
            )  # type: ignore[return-value]

        # Resolve the task from the store or in-memory history.
        task = None
        store = getattr(engine, "task_store", None)
        if store is not None:
            task = store.get_task(task_id)
        if task is None:
            task = engine.get_task(task_id)
        if task is None:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' not found"},
                status_code=404,
            )  # type: ignore[return-value]

        task_status = task.status.value if hasattr(task.status, "value") else str(task.status)
        is_terminal = task_status in _TERMINAL_STATUSES

        return StreamingResponse(
            _stream_events(task_id, bus, is_terminal),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return router


async def _stream_events(
    task_id: str,
    bus: SSEEventBus,
    is_terminal: bool,
) -> AsyncGenerator[str, None]:
    """Generate SSE frames for a task.

    Replays historical events for catch-up, then streams live events
    for active tasks until a terminal event arrives.
    """
    # Subscribe FIRST to avoid missing events between history replay
    # and live subscription.
    queue = bus.subscribe(task_id)
    try:
        # Replay historical events (catch-up for reconnecting clients).
        for entry in bus.get_history(task_id):
            yield _sse_frame(entry["event"], entry["data"])

        # For terminal tasks, all events are already in history -- close.
        if is_terminal:
            return

        # Stream live events until task_completed or disconnect.
        while True:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                # No event in the last second -- check if task has become
                # terminal (events already delivered via the queue).
                engine = get_swarm_engine()
                if engine is not None:
                    task = None
                    store = getattr(engine, "task_store", None)
                    if store is not None:
                        task = store.get_task(task_id)
                    if task is None:
                        task = engine.get_task(task_id)
                    if task is not None:
                        status = task.status.value if hasattr(task.status, "value") else str(task.status)
                        if status in _TERMINAL_STATUSES:
                            # Drain any remaining events from the queue.
                            while not queue.empty():
                                remaining = queue.get_nowait()
                                yield _sse_frame(remaining["event"], remaining["data"])
                            return
                continue
            except asyncio.CancelledError:
                return

            yield _sse_frame(entry["event"], entry["data"])

            # Close the stream after any terminal event.
            if entry["event"] in ("task_completed", "task_failed"):
                return
    finally:
        bus.unsubscribe(task_id, queue)


# ══════════════════════════════════════════════════════════════════════════
# Engine integration — wire event emission into SwarmEngine
# ══════════════════════════════════════════════════════════════════════════


def wire_engine_events(engine: Any, bus: SSEEventBus) -> None:
    """Wire a SwarmEngine to emit SSE events via the provided bus.

    Uses the engine's public set_sse_bus API (no more monkey-patching of
    private methods like _finalize_task or _dispatch_worker).

    Safe to call multiple times; subsequent calls are no-ops if already
    wired with the same bus.
    """
    if getattr(engine, "_sse_wired", False) and getattr(engine, "_sse_bus", None) is bus:
        return

    if hasattr(engine, "set_sse_bus"):
        engine.set_sse_bus(bus)
    else:
        # Fallback for older engines (should not happen after refactor)
        engine._sse_bus = bus

    engine._sse_wired = True
    engine._sse_bus = bus  # keep for compatibility checks
