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
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from kazma_core.swarm.engine import get_swarm_engine

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# SSE frame helper
# ══════════════════════════════════════════════════════════════════════════


def _sse_frame(event: str, data: str | dict[str, Any] | list[Any]) -> str:
    """Format a single SSE frame.

    Args:
        event: The event type name.
        data: Payload -- dict/list is JSON-serialized, str is used as-is.

    Returns:
        Formatted SSE string: ``event: <type>\\ndata: <json>\\n\\n``
    """
    if isinstance(data, (dict, list)):
        data = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


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
        # No lock needed: all methods are sync and run in asyncio's
        # single-threaded event loop, so there are no interleaving points.

    def emit(self, task_id: str, event: str, data: dict[str, Any]) -> None:
        """Emit an event for a task.

        Stores the event in history and delivers it to all active
        subscribers for the given task_id.
        """
        entry: dict[str, Any] = {"event": event, "data": data}

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
        except RuntimeError:
            pass

    def _cleanup_terminal_history(self) -> None:
        """Remove history for terminal tasks that have no subscribers."""
        terminal_events = frozenset({"task_completed", "task_failed"})
        tasks_to_remove: list[str] = []
        for task_id, history in self._history.items():
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
    """Monkey-patch a SwarmEngine to emit SSE events through the bus.

    Patches the engine's ``dispatch``, ``_dispatch_worker``, and
    ``_finalize_task`` methods so that task lifecycle events
    (task_started, worker_started, worker_completed, task_completed)
    are emitted automatically.

    Safe to call multiple times; subsequent calls are no-ops. If the
    engine is already wired with a different bus, the new bus takes over.

    All dispatch paths (basic dispatch, pipeline, fan_out, broadcast,
    consult, conditional) funnel through ``_dispatch_worker``, so
    patching that single method ensures worker events are emitted for
    every orchestration pattern.
    """
    if getattr(engine, "_sse_wired", False) and getattr(engine, "_sse_bus", None) is bus:
        return

    engine._sse_wired = False
    engine._sse_bus = bus

    original_dispatch = engine.dispatch
    original_finalize = engine._finalize_task
    original_dispatch_worker = engine._dispatch_worker

    async def _patched_dispatch(task: Any) -> Any:
        """Emit task_started, track active task id, then delegate."""
        workers = list(task.workers) if task.workers else []
        bus.emit(task.id, "task_started", {"task_id": task.id, "workers": workers})
        # Store task_id so _dispatch_worker can resolve it.
        engine._active_task_id = task.id
        engine._sse_step_counter = 0
        try:
            return await original_dispatch(task)
        except Exception as exc:
            logger.exception("[SSE] dispatch failed for task '%s'", task.id)
            bus.emit(
                task.id,
                "task_failed",
                {
                    "task_id": task.id,
                    "error": str(exc)[:500],
                },
            )
            raise
        finally:
            engine._active_task_id = ""
            engine._sse_step_counter = 0

    async def _patched_dispatch_worker(
        worker: Any,
        prompt: str,
        context: Any,
        *,
        timeout: float | None = None,
        validation_schema: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> Any:
        """Emit worker_started and worker_completed around worker dispatch."""
        task_id = getattr(engine, "_active_task_id", "") or ""
        step = getattr(engine, "_sse_step_counter", 0)
        engine._sse_step_counter = step + 1

        worker_name = worker.name if hasattr(worker, "name") else str(worker)

        bus.emit(
            task_id,
            "worker_started",
            {"worker": worker_name, "step": step + 1},
        )

        results = await original_dispatch_worker(
            worker,
            prompt,
            context,
            timeout=timeout,
            validation_schema=validation_schema,
            trace_id=trace_id,
        )

        if results:
            last_result = results[-1]
            output_preview = ""
            if hasattr(last_result, "output") and last_result.output:
                output_preview = str(last_result.output)[:200]
            bus.emit(
                task_id,
                "worker_completed",
                {
                    "worker": worker_name,
                    "status": getattr(last_result, "status", "unknown"),
                    "output_preview": output_preview,
                },
            )

            # Emit handoff events if any.
            if hasattr(last_result, "handoffs") and last_result.handoffs:
                for handoff in last_result.handoffs:
                    bus.emit(
                        task_id,
                        "handoff",
                        {
                            "from": getattr(handoff, "from_worker", ""),
                            "to": getattr(handoff, "to_worker", ""),
                        },
                    )

        return results

    def _patched_finalize(
        task: Any,
        worker_results: Any,
        status: str,
        duration_seconds: float,
        **kwargs: Any,
    ) -> Any:
        """Finalize the task, then emit task_completed (or checkpoint)."""
        result = original_finalize(
            task, worker_results, status, duration_seconds, **kwargs
        )
        if result is not None:
            if result.status == "paused":
                checkpoint_data = result.metadata.get("checkpoint", {})
                bus.emit(
                    result.task_id,
                    "checkpoint",
                    {
                        "step": checkpoint_data.get("step", 0),
                        "needs_approval": True,
                        "output_preview": checkpoint_data.get("output_preview", ""),
                    },
                )
            else:
                bus.emit(
                    result.task_id,
                    "task_completed",
                    {"task_id": result.task_id, "result": result.to_dict()},
                )
        return result

    # Apply patches.
    engine.dispatch = _patched_dispatch
    engine._finalize_task = _patched_finalize
    engine._dispatch_worker = _patched_dispatch_worker
    engine._sse_wired = True
