"""GatewayManager — Orchestrates all platform adapters and the unified message bus.

The GatewayManager owns the asyncio.Queue (Unified Message Bus) and manages
the lifecycle of all registered adapters. It starts them on ``start()``,
routes outgoing messages via ``send()``, and cleanly shuts down on ``stop()``.

Integration with FastAPI:
    Use the ``lifespan`` context manager to auto-start/stop with the server.

    app = FastAPI(lifespan=gateway.lifespan)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from kazma_gateway.base import BaseAdapter
from kazma_gateway.schemas import UniversalMessage

logger = logging.getLogger(__name__)

# Type alias for the message handler callback
MessageHandler = Callable[[UniversalMessage], Awaitable[None]]


class GatewayManager:
    """Orchestrates platform adapters and the unified message bus.

    Usage:
        manager = GatewayManager()
        manager.add_adapter(TelegramAdapter(token="..."))

        # Option 1: Manual lifecycle
        await manager.start()
        msg = await manager.next_message()
        await manager.stop()

        # Option 2: FastAPI lifespan
        app = FastAPI(lifespan=manager.lifespan)

        # Option 3: Register a handler (auto-processes messages)
        manager.on_message(my_handler)
        await manager.start()

    Attributes:
        adapters: List of registered adapters.
        queue: The unified message bus (asyncio.Queue of UniversalMessage).
    """

    def __init__(self, max_queue_size: int = 1000) -> None:
        self.adapters: list[BaseAdapter] = []
        self.queue: asyncio.Queue[UniversalMessage] = asyncio.Queue(maxsize=max_queue_size)
        self._handler: MessageHandler | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._started = False

    def add_adapter(self, adapter: BaseAdapter) -> None:
        """Register a platform adapter.

        Args:
            adapter: An instance of a BaseAdapter subclass.
        """
        self.adapters.append(adapter)
        logger.info("Registered adapter: %s", adapter.name)

    def on_message(self, handler: MessageHandler) -> None:
        """Register a message handler that processes all incoming messages.

        The handler is called for every UniversalMessage dequeued from
        the message bus. This is the primary integration point with the
        agent loop.

        Args:
            handler: An async callable that accepts a UniversalMessage.
        """
        self._handler = handler

    async def start(self) -> None:
        """Start all adapters and the message consumer (if handler set)."""
        if self._started:
            logger.warning("GatewayManager already started")
            return

        logger.info(
            "Starting gateway with %d adapter(s): %s",
            len(self.adapters),
            [a.name for a in self.adapters],
        )

        # Start all adapters — each gets a reference to the shared queue
        for adapter in self.adapters:
            await adapter.start(self.queue)

        # Start consumer task if a handler is registered
        if self._handler:
            self._consumer_task = asyncio.create_task(self._consume_messages())

        self._started = True
        logger.info("Gateway started — message bus active")

    async def stop(self) -> None:
        """Stop all adapters and the consumer task gracefully."""
        if not self._started:
            return

        logger.info("Stopping gateway...")

        # Cancel consumer
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

        # Stop all adapters
        for adapter in self.adapters:
            await adapter.stop()

        self._started = False
        logger.info("Gateway stopped")

    async def next_message(self) -> UniversalMessage:
        """Block until the next message arrives on the bus.

        Returns:
            The next UniversalMessage from the queue.

        Raises:
            asyncio.CancelledError: If the task is cancelled.
        """
        return await self.queue.get()

    async def send(self, target_id: str, content: str) -> bool:
        """Route an outgoing message to the correct adapter.

        The target_id must be prefixed with the platform name, e.g.
        "telegram:12345" or "discord:channel_id". The manager finds
        the matching adapter and delegates to its send() method.

        Args:
            target_id: Platform-prefixed target identifier.
            content: Message text to send.

        Returns:
            True if sent successfully, False otherwise.
        """
        if ":" not in target_id:
            logger.error("target_id must be platform-prefixed (e.g. 'telegram:12345'): %s", target_id)
            return False

        platform, _ = target_id.split(":", 1)

        for adapter in self.adapters:
            if adapter.name == platform:
                return await adapter.send(target_id, content)

        logger.error("No adapter found for platform '%s'", platform)
        return False

    async def _consume_messages(self) -> None:
        """Internal consumer loop — dequeues messages and calls the handler."""
        logger.info("Message consumer started")
        while True:
            try:
                msg = await self.queue.get()
                if self._handler:
                    await self._handler(msg)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Message handler error")

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
        """Return gateway statistics."""
        return {
            "started": self._started,
            "adapters": [{"name": a.name, "running": a._running} for a in self.adapters],
            "queue_size": self.queue.qsize(),
            "handler_registered": self._handler is not None,
        }
