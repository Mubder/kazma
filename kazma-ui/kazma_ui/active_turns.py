"""Shared registry of in-flight agent turns across chat transports.

Both the SSE endpoint (``sse_chat.py``) and the WebSocket bus
(``routes/ws_chat.py``) execute the same LangGraph supervisor graph per
``thread_id``.  Before this module existed, each transport kept its own
private map, so a WebSocket turn was invisible to the SSE duplicate-turn
guard and to the session status endpoint — a refresh could start a SECOND
concurrent graph invocation on the same thread/checkpointer, or a new
prompt could be silently dropped.

This module is the single source of truth for "is a turn still running on
thread X?".  A turn is registered when the graph starts and unregistered
when the pump/stream task completes.  Tasks are held by strong reference
so CPython never garbage-collects a running task whose client disconnected.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

__all__ = [
    "DETACHED_TTL_S",
    "bind_live_socket",
    "cancel_turn",
    "clear_orphan_stamp",
    "get_active_turn",
    "get_live_socket",
    "get_orphan_stamp",
    "is_turn_running",
    "mark_turn_orphaned",
    "reap_stale_turn",
    "register_turn",
    "unbind_live_socket",
    "unregister_turn",
    "active_turns",
]

# A detached (orphaned) turn is cancelled after this long so its remaining
# LLM calls aren't billed for a client that is not coming back. Overridable
# via KAZMA_DETACHED_TTL_S. The clock is "time since the client left", NOT
# turn age — long legitimate runs are never punished.
DETACHED_TTL_S = int(os.getenv("KAZMA_DETACHED_TTL_S", "300"))

# thread_id → running asyncio.Task. Module-level so both transports share
# one process-wide view. Guarded by a lock because Starlette may touch the
# registry from multiple event loops / threadpool contexts in tests.
_turns: dict[str, Any] = {}
# thread_id → monotonic timestamp of the first detected client disconnect.
# Side dict so ``active_turns`` and every task-based accessor stay untouched.
_orphaned_at: dict[str, float] = {}
# thread_id → current live WebSocket (or any object with send_json +
# client_state). WS turns capture a *sender* that re-reads this map on every
# emit so a tab-switch reconnect rebinds delivery without restarting the
# graph. SSE does not use this map.
_live_sockets: dict[str, Any] = {}
_lock = threading.RLock()

logger = logging.getLogger(__name__)

# Back-compat alias: sse_chat historically exposed ``_active_turns``.
active_turns = _turns


def register_turn(thread_id: str, task: Any) -> None:
    """Register *task* as the in-flight turn for *thread_id*."""
    if not thread_id:
        return
    with _lock:
        _turns[thread_id] = task
        _orphaned_at.pop(thread_id, None)


def unregister_turn(thread_id: str, task: Any = None) -> None:
    """Remove the registered turn for *thread_id*.

    When *task* is given it is only removed if it is still the registered
    task — a done-callback from a superseded turn must not unregister its
    replacement.
    """
    if not thread_id:
        return
    with _lock:
        if task is None or _turns.get(thread_id) is task:
            _turns.pop(thread_id, None)
            _orphaned_at.pop(thread_id, None)


def bind_live_socket(thread_id: str, socket: Any) -> None:
    """Point live telemetry delivery for *thread_id* at *socket*.

    Called on every WS accept/reconnect. An in-flight turn that was bound to
    a dead socket (tab backgrounded / refresh) resumes emitting on the new
    connection — without this, ``turn_complete`` is swallowed by ``is_lost``
    on the old socket and the UI never updates until a full page reload.
    Also clears any orphan stamp: the client is present again.
    """
    if not thread_id or socket is None:
        return
    with _lock:
        _live_sockets[thread_id] = socket
        _orphaned_at.pop(thread_id, None)


def unbind_live_socket(thread_id: str, socket: Any = None) -> None:
    """Drop the live socket for *thread_id* if it still matches *socket*.

    When *socket* is omitted the entry is always cleared. Matching prevents a
    superseded connection's disconnect from wiping a newer reconnect's bind.
    """
    if not thread_id:
        return
    with _lock:
        if socket is None or _live_sockets.get(thread_id) is socket:
            _live_sockets.pop(thread_id, None)


def get_live_socket(thread_id: str) -> Any | None:
    """Return the current live delivery socket for *thread_id*, or None."""
    if not thread_id:
        return None
    with _lock:
        return _live_sockets.get(thread_id)


def clear_orphan_stamp(thread_id: str) -> None:
    """Clear the orphan TTL clock for *thread_id* (client reattached)."""
    if not thread_id:
        return
    with _lock:
        _orphaned_at.pop(thread_id, None)


def mark_turn_orphaned(thread_id: str) -> None:
    """Record the moment the client detached from *thread_id*'s turn.

    Idempotent — the first disconnect wins (a refresh is a single event;
    every subsequent drop is ignored). A turn that already finished is not
    stamped. ``reap_stale_turn`` uses this timestamp to cancel turns that
    were abandoned for longer than ``DETACHED_TTL_S``.
    """
    if not thread_id:
        return
    with _lock:
        task = _turns.get(thread_id)
        if task is None:
            return
        try:
            if task.done():
                return
        except Exception:
            logger.debug("[active-turns] task.done() check failed for thread=%s", thread_id[:12], exc_info=True)
        if thread_id not in _orphaned_at:
            _orphaned_at[thread_id] = time.monotonic()


def reap_stale_turn(thread_id: str, ttl_s: float = DETACHED_TTL_S) -> Any | None:
    """Return and unregister the turn task if its client has been gone for
    longer than *ttl_s*, else ``None``.

    Atomic under the registry lock — two racing callers cannot reap the
    same turn. The caller MUST ``task.cancel()`` and await the task to
    completion before starting any replacement run so the two runs never
    interleave on the checkpointer.
    """
    if not thread_id:
        return None
    with _lock:
        task = _turns.get(thread_id)
        stamp = _orphaned_at.get(thread_id)
        if task is None or stamp is None:
            return None
        if time.monotonic() - stamp < ttl_s:
            return None
        _turns.pop(thread_id, None)
        _orphaned_at.pop(thread_id, None)
        return task


def get_orphan_stamp(thread_id: str) -> float | None:
    """Return the monotonic timestamp when *thread_id*'s turn was orphaned,
    or ``None`` if the turn is not running / not orphaned."""
    if not thread_id:
        return None
    with _lock:
        task = _turns.get(thread_id)
        if task is None:
            return None
        try:
            if task.done():
                return None
        except Exception:
            logger.debug("[active-turns] task.done() check failed for thread=%s", thread_id[:12], exc_info=True)
        return _orphaned_at.get(thread_id)


def cancel_turn(thread_id: str) -> Any | None:
    """Immediately cancel the running turn for *thread_id* (user Stop).

    Atomically unregisters and cancels the turn's task. Returns the task
    so the caller can await its full unwind before starting a replacement
    run. The done callback still fires and persists partial state from
    the checkpointer — a Stop never loses what the graph already wrote.
    """
    if not thread_id:
        return None
    with _lock:
        task = _turns.pop(thread_id, None)
        _orphaned_at.pop(thread_id, None)
    if task is not None:
        try:
            task.cancel()
        except Exception:
            logger.debug("[active-turns] task.cancel() failed for thread=%s", thread_id[:12], exc_info=True)
        logger.info("[active-turns] cancelled turn for thread=%s", thread_id[:12])
    return task


def get_active_turn(thread_id: str) -> Any | None:
    """Return the registered task for *thread_id*, or ``None``."""
    if not thread_id:
        return None
    with _lock:
        return _turns.get(thread_id)


def is_turn_running(thread_id: str) -> bool:
    """Return True if a live turn is registered for *thread_id*.

    A registered but already-finished task (done / cancelled) does NOT
    count — the done callback usually unregisters it, but this guard also
    protects against callback races.
    """
    task = get_active_turn(thread_id)
    if task is None:
        return False
    try:
        return not task.done()
    except Exception:
        logger.debug("[active-turns] task.done() check failed for thread=%s", thread_id[:12], exc_info=True)
        return True
