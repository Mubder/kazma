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
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
    """

    platform: str
    sender_id: str
    text: str
    context_metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

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

    async def start(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Launch the adapter's listen loop as a background task."""
        self._running = True
        self._task = asyncio.create_task(
            self.listen(queue, shutdown_event),
            name=f"adapter-{self.name}",
        )
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

    def add_adapter(self, adapter: BaseAdapter) -> None:
        """Register a platform adapter."""
        self.adapters.append(adapter)
        logger.info("Registered adapter: %s", adapter.name)

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

        # Start the consumer that dispatches to the Brain
        if self._handler:
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
                return await adapter.send(outbound)

        logger.error("No adapter for platform '%s'", platform)
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
                if self._handler:
                    try:
                        await self._handler(msg)
                    except Exception:
                        logger.exception(
                            "Handler error for message from %s",
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
