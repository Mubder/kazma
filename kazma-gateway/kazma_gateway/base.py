"""Abstract base class for platform adapters.

Every messaging platform adapter must subclass BaseAdapter and implement
the listen() and send() methods.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from kazma_gateway.schemas import UniversalMessage

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Abstract base for all platform adapters.

    Adapters are responsible for:
      1. Listening to platform messages and normalizing them into
         UniversalMessage objects placed on the shared queue.
      2. Sending responses back to the platform via send().

    Lifecycle:
      - ``start()`` is called by GatewayManager to launch the listen loop.
      - ``stop()`` is called by GatewayManager on shutdown to cancel the task.
      - ``send()`` is called by the agent loop to deliver responses.

    Attributes:
        name: Platform identifier (e.g. "telegram", "discord").
        queue: Reference to the unified message bus (set by GatewayManager).
    """

    name: str = "unknown"

    def __init__(self) -> None:
        self.queue: asyncio.Queue[UniversalMessage] | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self, queue: asyncio.Queue[UniversalMessage]) -> None:
        """Start the adapter's listen loop.

        Called by GatewayManager — do not override unless you need
        custom startup logic (call super().start() first).

        Args:
            queue: The unified message bus to enqueue messages onto.
        """
        self.queue = queue
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("[%s] Adapter started", self.name)

    async def stop(self) -> None:
        """Stop the adapter gracefully.

        Cancels the listen task and waits for it to finish.
        """
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[%s] Adapter stopped", self.name)

    async def _run_loop(self) -> None:
        """Internal wrapper that runs listen() and handles cancellation."""
        assert self.queue is not None, "start() must be called before listen"
        try:
            await self.listen(self.queue)
        except asyncio.CancelledError:
            logger.debug("[%s] Listen loop cancelled", self.name)
        except Exception:
            logger.exception("[%s] Listen loop crashed", self.name)

    @abstractmethod
    async def listen(self, queue: asyncio.Queue[UniversalMessage]) -> None:
        """Listen for incoming platform messages and enqueue them.

        This method should run indefinitely (or until cancelled).
        Each incoming message must be normalized to UniversalMessage
        and placed on the queue via ``await queue.put(msg)``.

        Args:
            queue: The unified message bus.
        """
        ...

    @abstractmethod
    async def send(self, target_id: str, content: str) -> bool:
        """Send a message back to the platform.

        Args:
            target_id: Platform-prefixed target (e.g. "telegram:12345").
            content: The message text to send.

        Returns:
            True if the message was sent successfully, False otherwise.
        """
        ...
