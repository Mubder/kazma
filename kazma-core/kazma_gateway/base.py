"""BaseAdapter — Abstract interface for platform message adapters.

Every platform (Telegram, Discord, Slack, etc.) implements this interface.
The GatewayManager discovers and manages all active adapters.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Universal Message Format (UMF) ────────────────────────────────────


@dataclass
class Message:
    """Normalized message that all adapters produce and the agent consumes.

    The GatewayManager's asyncio.Queue transports these from adapters
    to the agent brain.  The agent never sees platform-specific payloads.
    """

    sender_id: str  # e.g. "telegram:12345678", "discord:98765432"
    content: str
    platform: str  # "telegram", "discord", "slack", etc.
    platform_message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def platform_prefix(self) -> str:
        """Return the prefix part of sender_id (e.g. 'telegram')."""
        return self.sender_id.split(":")[0] if ":" in self.sender_id else self.platform

    @property
    def platform_id(self) -> str:
        """Return the ID part of sender_id (e.g. '12345678')."""
        return self.sender_id.split(":", 1)[1] if ":" in self.sender_id else self.sender_id


# ── Adapter status ────────────────────────────────────────────────────


class AdapterStatus:
    """Possible states for an adapter."""

    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"
    DISCONNECTED = "disconnected"


# ── Base adapter interface ────────────────────────────────────────────


class BaseAdapter(ABC):
    """Every platform adapter inherits from this.

    Subclasses must implement:
      - send(target_id, content) — push a reply to the platform
      - _poll() — internal loop that produces Messages onto the queue
    """

    def __init__(self, name: str, platform: str) -> None:
        self.name = name
        self.platform = platform
        self.status: str = AdapterStatus.STOPPED
        self._queue: Any = None  # set by GatewayManager
        self._task: Any = None
        self._stop_event: Any = None
        self.message_count: int = 0
        self.error_count: int = 0
        self.last_error: str | None = None

    @abstractmethod
    async def send(self, target_id: str, content: str, **kwargs: Any) -> str:
        """Send a message to a user/chat on this platform.

        Args:
            target_id: Platform-specific target (e.g. chat_id for Telegram).
            content: Message text.

        Returns:
            Platform message ID as a string, or an error description.
        """
        ...

    @abstractmethod
    async def _poll(self) -> None:
        """Platform-specific polling loop.

        Must call ``await self.emit(msg)`` for each incoming message.
        Runs in a background asyncio task.
        """
        ...

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the adapter's polling loop in a background task."""
        if self.status == AdapterStatus.RUNNING:
            logger.warning("[Gateway] %s: already running", self.name)
            return

        self._stop_event = asyncio.Event()
        self.status = AdapterStatus.RUNNING
        self._task = asyncio.create_task(self._run_loop(), name=f"adapter-{self.name}")
        logger.info("[Gateway] %s: started polling", self.name)

    async def stop(self) -> None:
        """Signal the polling loop to stop and wait for it."""
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.status = AdapterStatus.STOPPED
        logger.info("[Gateway] %s: stopped", self.name)

    async def _run_loop(self) -> None:
        """Wrapper around _poll that handles exceptions and restart."""
        try:
            await self._poll()
        except asyncio.CancelledError:
            self.status = AdapterStatus.STOPPED
        except Exception as exc:
            self.status = AdapterStatus.ERROR
            self.last_error = str(exc)
            self.error_count += 1
            logger.exception("[Gateway] %s: polling crashed: %s", self.name, exc)

    async def emit(self, msg: Message) -> None:
        """Push a normalized message onto the gateway queue.

        Called by subclasses from within _poll().
        """
        self.message_count += 1
        if self._queue is not None:
            await self._queue.put(msg)
            logger.debug(
                "[Gateway] %s → queue: %.80s (sender=%s)",
                self.name,
                msg.content,
                msg.sender_id,
            )
        else:
            logger.warning("[Gateway] %s: no queue set, dropping message", self.name)

    def status_info(self) -> dict[str, Any]:
        """Return a snapshot for the monitor UI."""
        return {
            "name": self.name,
            "platform": self.platform,
            "status": self.status,
            "message_count": self.message_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
        }
