"""Kazma Gateway — Unified, polling-based message gateway.

Headless architecture: NO public IP, NO HTTPS tunnels, NO webhooks.
All platform adapters poll their respective APIs and enqueue normalized
messages onto a shared asyncio.Queue. The Brain (agent loop) only ever
sees IncomingMessage objects — platform-specific code never leaks.

Architecture:
    ┌─────────────┐     ┌─────────────┐
    │  Telegram    │     │  Discord    │  ... future adapters
    │  (manual     │     │  (future)   │
    │   polling)   │     │             │
    └──────┬───────┘     └──────┬──────┘
           │  listen()          │  listen()
           ▼                    ▼
    ┌──────────────────────────────────┐
    │  asyncio.Queue(maxsize=100)      │  ← Unified Message Bus
    │  (bounded, backpressure-safe)    │
    └──────────────┬───────────────────┘
                   │
                   ▼
    ┌──────────────────────────────────┐
    │  GatewayManager                   │
    │  - consumes queue                 │
    │  - dispatches to handler          │
    │  - asyncio.Event shutdown signal  │
    │  - graceful drain on stop()       │
    └──────────────┬───────────────────┘
                   │
                   ▼
    ┌──────────────────────────────────┐
    │  Agent Handler (Brain)            │
    │  - receives IncomingMessage       │
    │  - replies via send()             │
    └──────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BaseAdapter",
    "GatewayManager",
    "IncomingMessage",
    "MessageHandler",
    "MessageMetrics",
    "OutboundMessage",
    "RateLimiter",
    "SessionStore",
]


# ══════════════════════════════════════════════════════════════════════════
# IncomingMessage — the ONLY message type the Brain ever sees
# ══════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class IncomingMessage:
    """Normalized inbound message from any platform.

    The Brain never touches platform-specific fields. Raw platform IDs
    (chat_id, channel_id, guild_id, etc.) live inside context_metadata
    so the adapter's send() can use them later.

    Attributes:
        platform:       Source platform ("telegram", "discord", ...).
        sender_id:      Stable sender identifier (e.g. "telegram:12345").
        text:           The message body.
        context_metadata: Opaque dict carrying raw platform IDs and any
                          platform-specific data the adapter needs for
                          routing replies. The Brain passes this back
                          verbatim in send().
        timestamp:      Unix time when the message was received.
        correlation_id: UUID4 tracing ID injected at ingress.
    """

    platform: str
    sender_id: str
    text: str
    context_metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    correlation_id: str = field(default_factory=lambda: f"cid-{uuid.uuid4().hex[:12]}")

    def reply_target(self) -> str:
        """Build a platform-prefixed reply target from context_metadata."""
        return self.sender_id


# ══════════════════════════════════════════════════════════════════════════
# OutboundMessage — what send() delivers back to a platform
# ══════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class OutboundMessage:
    """A message destined for a specific platform chat/user.

    Attributes:
        target_id:  Platform-prefixed target (e.g. "telegram:12345").
        text:       The message body.
        context_metadata: The same dict from the IncomingMessage — the
                          adapter uses this to extract raw platform IDs.
    """

    target_id: str
    text: str
    context_metadata: dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════════════
# RateLimiter — token-bucket rate limiting for outbound messages
# ══════════════════════════════════════════════════════════════════════════


class RateLimiter:
    """Token-bucket rate limiter for outbound messages.

    Args:
        max_per_second: Maximum messages per second (default 30 for Telegram).
    """

    def __init__(self, max_per_second: int = 30) -> None:
        self._max = max_per_second
        self._tokens = float(max_per_second)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._max, self._tokens + elapsed * self._max)
            self._last_refill = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self._max
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1


# ══════════════════════════════════════════════════════════════════════════
# MessageMetrics — throughput and error tracking
# ══════════════════════════════════════════════════════════════════════════


class MessageMetrics:
    """Thread-safe message throughput and error counter."""

    def __init__(self) -> None:
        self.inbound_total: int = 0
        self.outbound_total: int = 0
        self.errors_total: int = 0
        self._lock = asyncio.Lock()

    async def record_inbound(self) -> None:
        async with self._lock:
            self.inbound_total += 1

    async def record_outbound(self) -> None:
        async with self._lock:
            self.outbound_total += 1

    async def record_error(self) -> None:
        async with self._lock:
            self.errors_total += 1

    def snapshot(self) -> dict[str, int]:
        """Return current metrics."""
        return {
            "inbound_total": self.inbound_total,
            "outbound_total": self.outbound_total,
            "errors_total": self.errors_total,
        }


# ══════════════════════════════════════════════════════════════════════════
# SessionStore — persistent side-cache for platform context
# ══════════════════════════════════════════════════════════════════════════


class SessionStore(ABC):
    """Abstract store for mapping thread_id → platform context_metadata.

    Replaces the in-memory _session_map dict with a persistent backend.
    Platform IDs (chat_id, user_id, etc.) live here — NEVER in graph state.

    Subclasses MUST implement get(), put(), and delete(). Implementations
    SHOULD override evict_older_than() to provide TTL-based eviction so that
    session entries survive an agent reply (enabling crash-recovery routing)
    instead of being deleted immediately.
    """

    @abstractmethod
    async def get(self, thread_id: str) -> dict[str, Any]:
        """Retrieve stored context_metadata for a thread_id.

        Returns empty dict if not found.
        """
        ...

    @abstractmethod
    async def put(self, thread_id: str, context: dict[str, Any]) -> None:
        """Store context_metadata for a thread_id (upsert)."""
        ...

    @abstractmethod
    async def delete(self, thread_id: str) -> None:
        """Remove stored context for a thread_id. No-op if not found."""
        ...

    async def evict_older_than(self, seconds: float) -> int:
        """Evict session entries older than ``seconds`` since their last update.

        Returns the number of entries evicted. The base implementation is a
        no-op returning 0; backends with a timestamp (e.g. SQLiteSessionStore)
        override this to enable TTL/LRU eviction. This replaces the old
        behavior of deleting the session entry after every agent reply, which
        broke crash-recovery routing.

        Args:
            seconds: TTL in seconds. Entries whose ``updated_at`` is older
                     than ``now - seconds`` are removed.

        Returns:
            Number of entries evicted.
        """
        return 0


# ══════════════════════════════════════════════════════════════════════════
# BaseAdapter — the contract every platform adapter must fulfill
# ══════════════════════════════════════════════════════════════════════════


class BaseAdapter(ABC):
    """Abstract base for all platform adapters.

    Lifecycle (managed by GatewayManager):
        1. GatewayManager calls adapter.start(queue, shutdown_event)
        2. Adapter spawns its listen() coroutine as a background task
        3. On shutdown, the asyncio.Event is set — adapter must exit
        4. GatewayManager awaits the task to confirm clean exit

    Subclasses MUST implement:
        - listen(queue, shutdown_event): poll loop that enqueues IncomingMessage
        - send(outbound): deliver an OutboundMessage to the platform

    Jitter contract:
        Every listen() implementation MUST include a randomized 1-3s delay
        between poll cycles to prevent rate-limiting and API hammering.
        Use ``await self._jitter_sleep(shutdown_event)`` for this.
    """

    name: str = "unknown"

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._start_time: float = 0.0

    async def start(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Launch the adapter's listen loop as a background task."""
        import time as _time

        self._running = True
        self._start_time = _time.time()
        self._task = asyncio.create_task(
            self.listen(queue, shutdown_event),
            name=f"adapter-{self.name}",
        )

        def _on_task_done(task: asyncio.Task[None]) -> None:
            """Reset _running and log if the listen task crashes unexpectedly.

            This catches the case where listen() raises an unhandled exception
            (e.g. a malformed update or network error that escapes the loop's
            own exception handling). Without this callback _running would stay
            True forever and get_status() would report "connected" even though
            polling has stopped.
            """
            if task.cancelled():
                return
            exc = task.exception()
            if exc:
                self._running = False
                logger.error(
                    "[%s] Adapter listen task crashed: %s",
                    self.name,
                    exc,
                )

        self._task.add_done_callback(_on_task_done)
        logger.info("[%s] Adapter started", self.name)

    async def stop(self) -> None:
        """Wait for the adapter task to finish after shutdown signal."""
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        self._running = False
        logger.info("[%s] Adapter stopped", self.name)

    @property
    def uptime(self) -> float:
        """Seconds since the adapter was started. 0 if not started."""
        if self._start_time and self._running:
            import time as _time

            return _time.time() - self._start_time
        return 0.0

    @property
    def is_running(self) -> bool:
        """Whether the adapter is currently running (public accessor)."""
        return self._running

    @staticmethod
    async def jitter_sleep(shutdown_event: asyncio.Event) -> bool:
        """Randomized 1-3 second delay between poll cycles.

        This is MANDATORY in every listen() loop to prevent rate-limiting.
        Returns True if shutdown was signalled during the sleep
        (caller should exit), False otherwise.

        Args:
            shutdown_event: The gateway's shutdown signal.

        Returns:
            True if caller should exit (shutdown signalled).
        """
        delay = random.uniform(1.0, 3.0)
        try:
            # Use wait_for so we wake up immediately on shutdown
            await asyncio.wait_for(
                shutdown_event.wait(),
                timeout=delay,
            )
            return True  # shutdown signalled
        except TimeoutError:
            return False  # normal jitter expiry

    @abstractmethod
    async def listen(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Poll the platform and enqueue IncomingMessage objects.

        MUST check shutdown_event.is_set() and exit cleanly when True.
        MUST call ``await self.jitter_sleep(shutdown_event)`` between
        poll cycles to introduce 1-3s randomized delay.

        Args:
            queue:          The unified message bus.
            shutdown_event: Set by GatewayManager on SIGTERM/CTRL+C.
        """
        ...

    @abstractmethod
    async def send(self, outbound: OutboundMessage) -> bool:
        """Deliver an outbound message to the platform.

        The adapter extracts raw platform IDs from outbound.context_metadata
        (which was carried verbatim from the original IncomingMessage).

        Args:
            outbound: The OutboundMessage to deliver.

        Returns:
            True if delivered successfully.
        """
        ...


# ══════════════════════════════════════════════════════════════════════════
# GatewayManager — the orchestrator
# ══════════════════════════════════════════════════════════════════════════

# Type alias for the handler the Brain registers
MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class GatewayManager:
    """Orchestrates all adapters and the unified message bus.

    Responsibilities:
        - Owns the bounded asyncio.Queue (maxsize=100).
        - Starts/stops all registered adapters.
        - Consumes the queue and dispatches to the registered handler.
        - Signals shutdown via asyncio.Event (no zombie tasks).
        - Drains remaining messages on shutdown before exiting.

    Usage:
        manager = GatewayManager()
        manager.add_adapter(TelegramAdapter(token="..."))
        manager.on_message(my_brain_handler)

        # Option 1: FastAPI lifespan
        app = FastAPI(lifespan=manager.lifespan)

        # Option 2: Manual
        await manager.start()
        ...
        await manager.stop()
    """

    def __init__(self, max_queue_size: int = 100) -> None:
        self.adapters: list[BaseAdapter] = []
        self.queue: asyncio.Queue[IncomingMessage] = asyncio.Queue(
            maxsize=max_queue_size,
        )
        self._shutdown = asyncio.Event()
        self._handler: MessageHandler | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._started = False
        # Metrics
        self.metrics = MessageMetrics()
        # Rate feedback (optional, set via set_rate_feedback)
        self._rate_feedback: Any = None
        # Suggestions (optional, set via set_suggester)
        self._suggester: Any = None
        # Persistence references (set by app.py at startup)
        self._session_store: Any = None
        self._checkpointer: Any = None
        self._session_store_path: str = ""
        self._checkpointer_path: str = ""

    def set_persistence(
        self,
        session_store: Any = None,
        checkpointer: Any = None,
        session_store_path: str = "",
        checkpointer_path: str = "",
    ) -> None:
        """Register persistence backends for status reporting."""
        self._session_store = session_store
        self._checkpointer = checkpointer
        self._session_store_path = session_store_path
        self._checkpointer_path = checkpointer_path

    def add_adapter(self, adapter: BaseAdapter) -> None:
        """Register a platform adapter."""
        self.adapters.append(adapter)
        logger.info("Registered adapter: %s", adapter.name)

    def set_rate_feedback(self, rate_feedback: Any) -> None:
        """Register a RateFeedbackManager for inbound rate limiting."""
        self._rate_feedback = rate_feedback
        logger.info("Rate feedback manager registered")

    def set_suggester(self, suggester: Any) -> None:
        """Register a PostTaskSuggester for next-step suggestions."""
        self._suggester = suggester
        logger.info("Suggestion manager registered")

    def on_message(self, handler: MessageHandler) -> None:
        """Register the Brain's message handler."""
        self._handler = handler

    async def start(self) -> None:
        """Start all adapters and the consumer loop."""
        if self._started:
            logger.warning("GatewayManager already started")
            return

        self._shutdown.clear()

        logger.info(
            "Starting gateway with %d adapter(s): [%s]",
            len(self.adapters),
            ", ".join(a.name for a in self.adapters),
        )

        # Start each adapter — they get the queue + shutdown signal
        for adapter in self.adapters:
            await adapter.start(self.queue, self._shutdown)

        # Start the consumer that dispatches to the Brain (always start,
        # even if no handler is registered — messages are dropped with a
        # warning instead of piling up and being silently lost)
        self._consumer_task = asyncio.create_task(
            self._consume(),
            name="gateway-consumer",
        )

        self._started = True
        logger.info(
            "Gateway started — bus active (maxsize=%d)",
            self.queue.maxsize,
        )

    async def stop(self) -> None:
        """Signal shutdown and wait for all adapters to exit cleanly.

        Shutdown sequence:
            1. Set the asyncio.Event — adapters see this and exit their loops.
            2. Wait for all adapter tasks to finish (with 5s timeout).
            3. Drain remaining messages from the queue (best-effort).
            4. Cancel the consumer task.
        """
        if not self._started:
            return

        logger.info("Gateway shutting down...")

        # 1. Signal all adapters to stop
        self._shutdown.set()

        # 2. Wait for all adapters to exit
        for adapter in self.adapters:
            await adapter.stop()

        # 3. Drain remaining messages (best-effort, don't block)
        drained = 0
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                drained += 1
            except asyncio.QueueEmpty:
                break
        if drained:
            logger.info("Drained %d undelivered messages from queue", drained)

        # 4. Stop the consumer
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

        self._started = False
        logger.info("Gateway stopped cleanly")

    async def send(self, outbound: OutboundMessage) -> bool:
        """Route an outbound message to the correct adapter.

        Parses the platform from outbound.target_id prefix
        (e.g. "telegram:12345" → platform "telegram").

        Args:
            outbound: The OutboundMessage to deliver.

        Returns:
            True if delivered.
        """
        if ":" not in outbound.target_id:
            logger.error(
                "target_id must be platform:id format: %s",
                outbound.target_id,
            )
            return False

        platform = outbound.target_id.split(":", 1)[0]

        for adapter in self.adapters:
            if adapter.name == platform:
                try:
                    ok = await adapter.send(outbound)
                except TimeoutError:
                    logger.warning(
                        "[Gateway] Send timed out for %s (platform=%s)",
                        outbound.target_id,
                        platform,
                    )
                    await self.metrics.record_error()
                    return False
                except ConnectionError:
                    logger.warning(
                        "[Gateway] Connection issue for %s (platform=%s) — will retry",
                        outbound.target_id,
                        platform,
                    )
                    await self.metrics.record_error()
                    return False
                if ok:
                    await self.metrics.record_outbound()
                else:
                    await self.metrics.record_error()
                return ok

        logger.error("No adapter for platform '%s'", platform)
        await self.metrics.record_error()
        return False

    async def _consume(self) -> None:
        """Dequeue messages and dispatch to the registered handler."""
        logger.info("Message consumer started")
        while not self._shutdown.is_set():
            try:
                msg = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=1.0,
                )
                await self.metrics.record_inbound()

                # ── Rate feedback check ────────────────────────────
                if self._rate_feedback is not None:
                    if self._rate_feedback.is_limited(msg.sender_id):
                        if self._rate_feedback.should_send_feedback(msg.sender_id):
                            feedback_text = self._rate_feedback.get_feedback_message(msg.sender_id)
                            # should_send_feedback already updates last_feedback timestamp
                            # Send feedback via the appropriate adapter
                            try:
                                feedback_msg = OutboundMessage(
                                    target_id=msg.reply_target(),
                                    text=feedback_text,
                                )
                                await self.send(feedback_msg)
                            except Exception:
                                logger.debug("[Gateway] Failed to send rate limit feedback")
                        continue  # Skip dispatching to handler

                if self._handler:
                    try:
                        await self._handler(msg)
                    except Exception:
                        logger.exception(
                            "Handler error for message from %s",
                            msg.sender_id,
                        )

                    # ── Post-task suggestions ────────────────────────
                    # Send next-step hints after the handler completes.
                    # detect_tool_intent analyzes the user's message for
                    # patterns that suggest a tool could help.
                    if self._suggester is not None and msg.text:
                        try:
                            from kazma_gateway.suggestions import detect_tool_intent

                            hints = detect_tool_intent(msg.text)
                            if hints:
                                hint_text = "\n".join(f"💡 {h}" for h in hints[:2])
                                try:
                                    await self.send(OutboundMessage(
                                        target_id=msg.reply_target(),
                                        text=hint_text,
                                    ))
                                except Exception:
                                    logger.debug("[Gateway] Failed to send suggestion hints")
                        except Exception:
                            logger.debug("[Gateway] Suggestion detection failed")
                else:
                    logger.warning(
                        "[Gateway] Message from %s dropped — no handler registered",
                        msg.sender_id,
                    )
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                break
        logger.info("Message consumer stopped")

    @asynccontextmanager
    async def lifespan(self, app: Any) -> AsyncIterator[None]:
        """FastAPI lifespan context manager.

        Usage:
            app = FastAPI(lifespan=gateway.lifespan)
        """
        await self.start()
        yield
        await self.stop()

    @property
    def stats(self) -> dict[str, Any]:
        """Gateway statistics for monitoring."""
        return {
            "started": self._started,
            "shutdown_signalled": self._shutdown.is_set(),
            "adapters": [{"name": a.name, "running": a._running} for a in self.adapters],
            "queue_depth": self.queue.qsize(),
            "queue_maxsize": self.queue.maxsize,
            "handler_registered": self._handler is not None,
        }

    async def get_status(self) -> dict[str, Any]:
        """Full status for the Gateway Monitor panel.

        Returns:
            {
                "adapters": [{"platform", "status", "uptime_seconds"}],
                "persistence": {"session_store": {...}, "checkpointer": {...}, "active_threads": N},
                "threads": [{"thread_id", "platform", "display_name", "status", "last_active_seconds"}],
            }
        """
        import time as _time

        now = _time.time()

        # Adapter status
        adapter_status = []
        for a in self.adapters:
            adapter_status.append(
                {
                    "platform": a.name,
                    "status": "connected" if a._running else "offline",
                    "uptime_seconds": round(a.uptime, 1),
                }
            )

        # Persistence info
        persistence: dict[str, Any] = {
            "session_store": {
                "type": "sqlite",
                "path": self._session_store_path or "(not configured)",
            },
            "checkpointer": {
                "type": "sqlite",
                "path": self._checkpointer_path or "(not configured)",
            },
            "active_threads": 0,
        }

        # Thread info from session store
        threads: list[dict[str, Any]] = []
        if self._session_store is not None and hasattr(self._session_store, "list_active"):
            try:
                active_sessions = await self._session_store.list_active()
                persistence["active_threads"] = len(active_sessions)
                for session in active_sessions:
                    threads.append(
                        {
                            "thread_id": session.get("thread_id", ""),
                            "platform": session.get("platform", "unknown"),
                            "display_name": session.get("display_name", "unknown"),
                            "status": "active",
                            "last_active_seconds": round(now - session.get("updated_at", now), 1),
                        }
                    )
            except Exception:
                logger.debug("[Gateway] Could not list active sessions")

        return {
            "adapters": adapter_status,
            "persistence": persistence,
            "threads": threads,
            "metrics": self.metrics.snapshot(),
        }
