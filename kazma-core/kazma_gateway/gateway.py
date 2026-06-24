"""GatewayManager — Owns the asyncio.Queue, manages adapter lifecycle.

The GatewayManager is the central hub that:
  1. Holds the asyncio.Queue that all adapters push Messages into.
  2. Starts/stops adapters.
  3. Provides a consumer API for the agent brain to pull messages.
  4. Exposes status for the Gateway Monitor UI.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from kazma_gateway.base import BaseAdapter, Message

logger = logging.getLogger(__name__)

_QUEUE_MAXSIZE = 5000


class GatewayManager:
    """Manages platform adapters and the shared message queue.

    Usage:
        gateway = GatewayManager()
        telegram = TelegramAdapter(token="...")
        gateway.register(telegram)
        await gateway.start_all()

        msg = await gateway.consume()   # blocks until a message arrives
        await gateway.send(msg.sender_id, "Hello back!")
    """

    def __init__(self, queue_maxsize: int = _QUEUE_MAXSIZE) -> None:
        self.queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=queue_maxsize)
        self._adapters: dict[str, BaseAdapter] = {}
        self._queue_log: list[dict[str, Any]] = []
        self._queue_log_max: int = 200
        self._started: bool = False

    # ── Adapter management ───────────────────────────────────────────

    def register(self, adapter: BaseAdapter) -> None:
        """Register an adapter with the gateway.

        Sets the adapter's internal queue reference so it can emit messages.
        """
        adapter._queue = self.queue
        self._adapters[adapter.name] = adapter
        logger.info("[Gateway] Registered adapter: %s (%s)", adapter.name, adapter.platform)

    def unregister(self, name: str) -> None:
        """Remove and stop an adapter."""
        adapter = self._adapters.pop(name, None)
        if adapter:
            asyncio.create_task(adapter.stop())
            logger.info("[Gateway] Unregistered adapter: %s", name)

    def get_adapter(self, name: str) -> BaseAdapter | None:
        return self._adapters.get(name)

    def get_adapter_by_platform(self, platform: str) -> BaseAdapter | None:
        for a in self._adapters.values():
            if a.platform == platform:
                return a
        return None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def start_all(self) -> None:
        """Start every registered adapter."""
        self._started = True
        for adapter in self._adapters.values():
            await adapter.start()
        logger.info("[Gateway] All adapters started (%d)", len(self._adapters))

    async def stop_all(self) -> None:
        """Stop every adapter gracefully."""
        for adapter in self._adapters.values():
            await adapter.stop()
        self._started = False
        logger.info("[Gateway] All adapters stopped")

    async def start_adapter(self, name: str) -> bool:
        """Start a single adapter by name."""
        adapter = self._adapters.get(name)
        if adapter:
            await adapter.start()
            return True
        return False

    async def stop_adapter(self, name: str) -> bool:
        """Stop a single adapter by name."""
        adapter = self._adapters.get(name)
        if adapter:
            await adapter.stop()
            return True
        return False

    # ── Message consumption ──────────────────────────────────────────

    async def consume(self, timeout: float | None = None) -> Message | None:
        """Pull the next message from the queue.

        Blocks until a message arrives or the timeout expires.
        Returns None on timeout.
        """
        try:
            msg = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            self._log_queue_event("inbound", msg)
            return msg
        except asyncio.TimeoutError:
            return None

    async def send(self, target_id: str, content: str, **kwargs: Any) -> str:
        """Route an outgoing message to the correct adapter.

        Inspects the target_id prefix (e.g. 'telegram:123456') to find
        the right platform adapter, then calls adapter.send().
        """
        prefix = target_id.split(":")[0] if ":" in target_id else target_id
        adapter = self.get_adapter_by_platform(prefix)
        if not adapter:
            return f"Error: No adapter for platform '{prefix}'"
        if adapter.status != "running":
            return f"Error: Adapter '{prefix}' is not running"
        result = await adapter.send(target_id, content, **kwargs)
        self._log_queue_event("outbound", Message(
            sender_id=target_id,
            content=content,
            platform=prefix,
        ), status=result[:80])
        return result

    # ── Queue log (for UI) ───────────────────────────────────────────

    def _log_queue_event(self, direction: str, msg: Message, status: str = "ok") -> None:
        entry = {
            "ts": time.time(),
            "direction": direction,
            "sender_id": msg.sender_id,
            "content": msg.content[:120],
            "platform": msg.platform,
            "status": status,
        }
        self._queue_log.append(entry)
        if len(self._queue_log) > self._queue_log_max:
            self._queue_log = self._queue_log[-self._queue_log_max:]

    # ── Status snapshot (for monitor UI) ────────────────────────────

    def status_info(self) -> dict[str, Any]:
        """Return full status for the Gateway Monitor view."""
        return {
            "started": self._started,
            "queue_size": self.queue.qsize(),
            "queue_max": _QUEUE_MAXSIZE,
            "adapters": [
                a.status_info() for a in self._adapters.values()
            ],
            "queue_log": self._queue_log[-50:],  # last 50 events
        }


# ── Module-level singleton ────────────────────────────────────────────

_gateway: GatewayManager | None = None


def get_gateway() -> GatewayManager:
    """Return the singleton GatewayManager, creating it if needed."""
    global _gateway
    if _gateway is None:
        _gateway = GatewayManager()
    return _gateway
