"""Turn Delivery V2 — per-thread event journal + multi-socket broker.

The delivery half of ``docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md``.

Every chat turn event (token, tool, status, approval, terminal) from BOTH
transports (WS ``ws_chat.py`` / SSE ``sse_chat.py``) flows through exactly
one choke point — :meth:`TurnBroker.emit` — which:

1. Appends the frame to a bounded per-thread :class:`TurnJournal`, assigning
   a **monotonic per-thread sequence number** (``seq``) stamped on the frame.
2. Fans the stamped frame out to every socket bound to that thread and to
   every live subscriber queue. A dead/slow recipient is skipped — fan-out
   never raises for one bad socket and never blocks the emitting turn.

Reconnecting clients present their cursor (``last_seq``); the server replays
journal entries strictly after it. This is the Discord-gateway /
SSE-``Last-Event-ID`` pattern already proven in-repo by the swarm SSE bus
(``swarm_sse.SSEEventBus``). Correctness never depends on replay depth: when
a cursor predates retention, callers get ``gap=True`` and fall back to an
unconditional snapshot resync (status + messages) on the client.

Design invariants:

- **seq is canonical order.** Assigned under a lock at append; replay returns
  frames byte-identical to what was broadcast live.
- **Multi-slot sockets.** N tabs may bind the same thread concurrently
  (fixes the single-slot ``bind_live_socket`` silent-loser defect).
- **Process-local memory only.** The journal bridges a disconnected *user*,
  not a server restart; durable truth remains SessionStore + checkpointer.
  Bounded by per-thread cap, thread TTL, and a global thread ceiling.
- **One loop ordering.** Production runs a single event loop; per-thread
  asyncio locks serialize emit so live frames reach sockets in seq order.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections import deque
from typing import Any
from kazma_core.background import spawn_background

logger = logging.getLogger(__name__)

__all__ = [
    "TurnJournal",
    "TurnBroker",
    "get_turn_broker",
    "reset_turn_broker",
    "REPLAY_SKIP_TYPES",
    "is_replayable",
]

# ── Retention defaults ────────────────────────────────────────────────────

#: Max journal entries retained per thread (tokens dominate volume; a long
#: multi-tool turn produces a few hundred events).
DEFAULT_MAX_EVENTS_PER_THREAD = 2000

#: Threads idle longer than this are pruned lazily on the next append.
DEFAULT_TTL_SECONDS = 3600.0

#: Global thread ceiling — LRU eviction by last activity beyond this.
DEFAULT_MAX_THREADS = 500

#: Per-subscriber live queue bound (SSE path). Full ⇒ drop + warn; the
#: client's cursor/resync recovers, mirroring the swarm bus semantics.
SUBSCRIBER_QUEUE_MAX = 1000


#: Frame types excluded from RESUME REPLAY (both transports). Command
#: confirmations (/yolo, /long, /reset, /compact, steer acks) are persisted
#: in the session transcript and re-shown by loadSession; replaying them
#: into a reconnecting client's open turn was the 2026-08-16 duplicated-
#: MISSION-ON incident class. LIVE fan-out is unaffected — journaling them
#: is exactly what gives second tabs real-time parity.
#: ``error`` frames are transient turn-local diagnostics: replaying them
#: re-triggered the client's onError → attach → replayed-error loop (the
#: 2026-08-26 retry storm). The ``done`` that follows an error carries the
#: durable outcome a reconnecting client needs.
REPLAY_SKIP_TYPES = frozenset({"capacity", "steer", "error"})


def is_replayable(frame: dict[str, Any]) -> bool:
    """False for frames that must never be served on cursor replay."""
    if frame.get("type") in REPLAY_SKIP_TYPES:
        return False
    data = frame.get("data") or {}
    return not (isinstance(data, dict) and data.get("capacity"))


class TurnJournal:
    """Bounded per-thread event log assigning monotonic sequence numbers."""

    def __init__(
        self,
        *,
        max_events_per_thread: int = DEFAULT_MAX_EVENTS_PER_THREAD,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_threads: int = DEFAULT_MAX_THREADS,
    ) -> None:
        self._max_events = max(1, int(max_events_per_thread))
        self._ttl_seconds = float(ttl_seconds)
        self._max_threads = max(1, int(max_threads))
        # thread_id → deque[(seq, frame)]; frames carry their own "seq" key.
        self._events: dict[str, deque[dict[str, Any]]] = {}
        # thread_id → next seq to assign (starts at 1; 0 == "nothing yet").
        self._next_seq: dict[str, int] = {}
        # thread_id → monotonic time of last append (TTL/LRU clock).
        self._last_activity: dict[str, float] = {}
        # Registry integrity across loops/threadpools (mirrors active_turns).
        self._lock = threading.RLock()

    # ── Write path ────────────────────────────────────────────────

    def append(self, thread_id: str, frame: dict[str, Any]) -> dict[str, Any]:
        """Assign the next seq for *thread_id*, stamp it on a copy of *frame*,
        store, and return the stamped frame (``frame["seq"]`` is canonical).
        The input dict is never mutated — the returned copy is exactly what
        was stored and must be what gets broadcast live."""
        if not thread_id:
            raise ValueError("thread_id is required")
        with self._lock:
            self._prune_locked()
            now = time.monotonic()
            seq = self._next_seq.get(thread_id, 0) + 1
            self._next_seq[thread_id] = seq
            stamped = dict(frame)
            stamped["seq"] = seq
            bucket = self._events.get(thread_id)
            if bucket is None:
                bucket = deque()
                self._events[thread_id] = bucket
                # Global ceiling: evict the least-recently-active other thread.
                while len(self._events) > self._max_threads:
                    victim = min(
                        (tid for tid in self._events if tid != thread_id),
                        key=lambda tid: self._last_activity.get(tid, 0.0),
                        default=None,
                    )
                    if victim is None:
                        break
                    self._drop_thread_locked(victim)
            bucket.append(stamped)
            while len(bucket) > self._max_events:
                bucket.popleft()
            self._last_activity[thread_id] = now
            return stamped

    def head_seq(self, thread_id: str) -> int:
        """Highest assigned seq (0 when nothing was ever journaled)."""
        with self._lock:
            return self._next_seq.get(thread_id or "", 0)

    # ── Read path ─────────────────────────────────────────────────

    def replay(self, thread_id: str, after_seq: int) -> tuple[list[dict[str, Any]], bool]:
        """Return ``(frames, gap)`` for a client whose cursor is *after_seq*.

        ``frames`` are the retained entries strictly after ``after_seq``, in
        seq order. ``gap=True`` means the cursor predates retention (or the
        journal started mid-stream relative to it): the slice would be
        incomplete, so the caller must snapshot-resync instead of replaying.
        Unknown/empty threads yield ``([], False)`` — nothing was missed here.
        """
        with self._lock:
            after = int(after_seq or 0)
            bucket = self._events.get(thread_id or "")
            if not bucket:
                # Cursor into a vanished journal (process restart) — the
                # client must snapshot-resync from SessionStore, not assume
                # it is caught up on an empty in-memory log.
                return [], after > 0
            head = bucket[-1]["seq"]
            first = bucket[0]["seq"]
            if after > head:
                # Seq numbers restarted (new process) or the client is
                # holding a cursor from another generation.
                return [], True
            if after == head:
                return [], False
            if after < first - 1:
                return [], True
            return [frame for frame in bucket if frame["seq"] > after], False

    def stats(self) -> dict[str, Any]:
        """Occupancy snapshot (tests / metrics / ops dashboards)."""
        with self._lock:
            return {
                "threads": len(self._events),
                "total_events": sum(len(b) for b in self._events.values()),
                "max_events_per_thread": self._max_events,
                "ttl_seconds": self._ttl_seconds,
                "max_threads": self._max_threads,
            }

    # ── Pruning ───────────────────────────────────────────────────

    def _prune_locked(self) -> None:
        if not self._ttl_seconds or self._ttl_seconds <= 0:
            return
        now = time.monotonic()
        stale = [
            tid
            for tid, last in self._last_activity.items()
            if now - last > self._ttl_seconds
        ]
        for tid in stale:
            self._drop_thread_locked(tid)

    def _drop_thread_locked(self, thread_id: str) -> None:
        self._events.pop(thread_id, None)
        self._next_seq.pop(thread_id, None)
        self._last_activity.pop(thread_id, None)


class TurnBroker:
    """Journal + fan-out choke point for all chat turn events.

    Sockets are duck-typed (any object with an async ``send_json(dict)`` —
    Starlette WebSocket or test fakes). Subscriber queues serve future SSE
    attachment (plan P2). Fan-out failures are isolated per recipient.
    """

    def __init__(self, journal: TurnJournal | None = None) -> None:
        self._journal = journal or TurnJournal()
        # thread_id → {conn_id: socket}
        self._sockets: dict[str, dict[str, Any]] = {}
        # thread_id → [asyncio.Queue]
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        # Per-thread emit serialization (live frames reach sockets in seq
        # order even if two coroutines race; production emits sequentially
        # per pump anyway — this is defense, not the primary mechanism).
        self._emit_locks: dict[str, asyncio.Lock] = {}
        self._emit_locks_activity: dict[str, float] = {}
        self._max_emit_locks: int = 512
        self._lock = threading.RLock()

    # ── Socket registry ───────────────────────────────────────────

    def register_socket(self, thread_id: str, socket: Any) -> str:
        """Bind *socket* for live delivery on *thread_id*. Returns conn_id."""
        if not thread_id or socket is None:
            raise ValueError("thread_id and socket are required")
        conn_id = uuid.uuid4().hex
        with self._lock:
            self._sockets.setdefault(thread_id, {})[conn_id] = socket
        return conn_id

    def unregister_socket(self, thread_id: str, conn_id: str) -> None:
        with self._lock:
            conns = self._sockets.get(thread_id)
            if conns is not None:
                conns.pop(conn_id, None)
                if not conns:
                    self._sockets.pop(thread_id, None)

    def socket_count(self, thread_id: str) -> int:
        with self._lock:
            return len(self._sockets.get(thread_id or "", {}))

    # ── Emit ──────────────────────────────────────────────────────

    async def emit(self, thread_id: str, event: Any) -> dict[str, Any]:
        """Journal then fan-out one turn event. Returns the STAMPED frame
        (the exact dict stored in the journal — ``frame["seq"]`` is its
        canonical sequence number).

        Accepts a wire dict or anything exposing ``to_dict()`` (e.g.
        ``TelemetryEvent``). Raises only on programmer errors (empty thread
        id); recipient failures are swallowed per-socket.
        """
        if not thread_id:
            raise ValueError("thread_id is required")
        frame = event.to_dict() if hasattr(event, "to_dict") else dict(event)
        lock = self._emit_lock_for(thread_id)
        async with lock:
            stamped = self._journal.append(thread_id, frame)
            with self._lock:
                sockets = list(self._sockets.get(thread_id, {}).values())
                queues = list(self._subscribers.get(thread_id, []))
            delivered = await self._fan_out_sockets(sockets, stamped)
            dropped = 0
            for queue in queues:
                try:
                    queue.put_nowait(stamped)
                except asyncio.QueueFull:
                    dropped += 1
            if dropped:
                logger.warning(
                    "[Delivery] subscriber queue full for thread=%s — %d event(s) dropped",
                    thread_id[:12],
                    dropped,
                )
        try:
            from kazma_core.metrics import record_delivery_event

            record_delivery_event(events=1, replayed=0, dropped=dropped + (len(sockets) - delivered))
        except Exception:  # pragma: no cover — metrics must never break turns
            logger.debug("[Delivery] metric recording failed", exc_info=True)

        # Turn Delivery V2 P5: Web Push on terminal — the ONE choke point
        # both transports flow through, so WS and SSE turns both notify.
        # Fire-and-forget: never raises into the turn, never blocks it.
        # ``capacity`` done frames are slash-command acks (/yolo, /long,
        # /unrestricted): their content is the confirmation the sending
        # tab already painted — pushing it to every device is noise.
        if stamped.get("type") in ("turn_complete", "done"):
            data = stamped.get("data") or {}
            summary = str(data.get("content") or "").strip()
            if summary and not (isinstance(data, dict) and data.get("capacity")):
                spawn_background(self._push_terminal(summary), name="delivery-push-terminal")

        return stamped

    async def _push_terminal(self, summary: str) -> None:
        try:
            from kazma_ui.push import notify_push_turn_complete

            await notify_push_turn_complete(summary)
        except Exception:
            logger.debug("[Delivery] push notification failed", exc_info=True)

    async def _fan_out_sockets(self, sockets: list[Any], frame: dict[str, Any]) -> int:
        """Send *frame* to every socket; isolate failures. Returns delivered count."""
        delivered = 0
        for socket in sockets:
            try:
                await socket.send_json(frame)
                delivered += 1
            except Exception:
                logger.debug("[Delivery] socket send failed — skipping", exc_info=True)
        return delivered

    def _emit_lock_for(self, thread_id: str) -> asyncio.Lock:
        with self._lock:
            now = time.monotonic()
            lock = self._emit_locks.get(thread_id)
            if lock is None:
                if len(self._emit_locks) >= self._max_emit_locks:
                    candidates = [
                        tid for tid, lk in self._emit_locks.items()
                        if not lk.locked() and tid != thread_id
                    ]
                    if candidates:
                        victim = min(
                            candidates,
                            key=lambda tid: self._emit_locks_activity.get(tid, 0.0),
                        )
                        self._emit_locks.pop(victim, None)
                        self._emit_locks_activity.pop(victim, None)
                lock = asyncio.Lock()
                self._emit_locks[thread_id] = lock
            self._emit_locks_activity[thread_id] = now
            return lock

    # ── Resume / replay ───────────────────────────────────────────

    def head_seq(self, thread_id: str) -> int:
        """Highest journaled seq for *thread_id* (0 when nothing was ever
        journaled). Used by the NEW-prompt stream to subscribe at the current
        head instead of replaying the previous turn's backlog."""
        return self._journal.head_seq(thread_id)

    def resume(
        self, thread_id: str, last_seq: int
    ) -> tuple[list[dict[str, Any]], bool, int]:
        """Resolve a client cursor → ``(frames, gap, head_seq)``.

        ``gap=True`` ⇒ the client must snapshot-resync; ``frames`` is empty.
        Metrics-counted because this is THE recovery signal the plan tracks.
        """
        frames, gap = self._journal.replay(thread_id, last_seq)
        head = self._journal.head_seq(thread_id)
        try:
            from kazma_core.metrics import record_delivery_replay

            record_delivery_replay(replayed=len(frames), gap=gap)
        except Exception:  # pragma: no cover
            logger.debug("[Delivery] metric recording failed", exc_info=True)
        return frames, gap, head

    # ── Live subscriber queues (SSE attach, plan P2) ─────────────

    def subscribe(self, thread_id: str) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to live frames for *thread_id* (replay via :meth:`resume`)."""
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAX)
        with self._lock:
            self._subscribers.setdefault(thread_id or "", []).append(queue)
        return queue

    def unsubscribe(self, thread_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            subs = self._subscribers.get(thread_id or "")
            if subs and queue in subs:
                subs.remove(queue)
            if subs is not None and not subs:
                self._subscribers.pop(thread_id or "", None)

    # ── Introspection ─────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "journal": self._journal.stats(),
                "threads_with_sockets": len(self._sockets),
                "open_sockets": sum(len(c) for c in self._sockets.values()),
                "subscriber_queues": sum(len(s) for s in self._subscribers.values()),
            }


# ── Process-wide singleton (mirrors active_turns module-level registry) ──

_broker: TurnBroker | None = None
_broker_lock = threading.Lock()


def get_turn_broker() -> TurnBroker:
    """Return the process-wide broker, creating it on first use."""
    global _broker
    if _broker is None:
        with _broker_lock:
            if _broker is None:
                _broker = TurnBroker()
    return _broker


def reset_turn_broker() -> None:
    """Drop the singleton (tests only)."""
    global _broker
    with _broker_lock:
        _broker = None
