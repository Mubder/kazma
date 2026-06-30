"""Platform-agnostic message bus for swarm worker output streaming.

Workers stream logs, status updates, and interim outputs through
the bus without knowing the specific platform (Telegram/Discord/Slack).
The bus routes messages to the active adapter, formats Swarm Report
cards, and supports Human-in-the-Loop approval requests.

Platform-specific adapters live in kazma-gateway/adapters/ (e.g.
telegram_bus.py) to keep kazma-core platform-neutral.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Approval timeout: how long to wait before auto-rejecting.
_DEFAULT_APPROVAL_TIMEOUT = 60.0  # seconds
_MAX_BUS_MESSAGE_LEN = 4096       # Telegram message limit


# ── Data models ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class BusMessage:
    """A single log line or status update from a worker."""

    worker_name: str
    worker_role: str
    content: str
    level: str = "info"  # "info" | "warn" | "error" | "success"
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(slots=True)
class SwarmReport:
    """Structured report card for a completed worker task."""

    worker_name: str
    worker_role: str
    status: str           # "success" | "error" | "timeout" | "rejected"
    output: str           # truncated final output
    duration_ms: float = 0.0
    task_id: str = ""


@dataclass(slots=True)
class ApprovalRequest:
    """HITL approval request for a high-impact worker output."""

    worker_name: str
    task_description: str
    proposed_output: str  # truncated for display
    task_id: str = ""


# ── Platform adapter (ABC) ────────────────────────────────────────────────


class BusAdapter(ABC):
    """Platform-specific output channel.

    Each platform (Telegram, Discord, Slack) provides a concrete
    implementation in kazma-gateway/adapters/ that knows how to
    format and deliver messages.
    """

    @abstractmethod
    async def send(self, message: BusMessage) -> None:
        """Deliver a single log/status line to the platform."""
        ...

    @abstractmethod
    async def send_report(self, report: SwarmReport) -> None:
        """Deliver a formatted Swarm Report card."""
        ...

    @abstractmethod
    async def request_approval(self, approval: ApprovalRequest) -> bool:
        """Request HITL approval. Returns True if approved, False if rejected or timed out."""
        ...


# ── Null adapter (for headless / no-chat mode) ────────────────────────────


class NullBusAdapter(BusAdapter):
    """Drops all messages. Used when no platform adapter is connected."""

    async def send(self, message: BusMessage) -> None:
        pass

    async def send_report(self, report: SwarmReport) -> None:
        pass

    async def request_approval(self, approval: ApprovalRequest) -> bool:
        return True  # auto-approve when no adapter is present


# ── Message Bus ────────────────────────────────────────────────────────────


class SwarmMessageBus:
    """Routes worker output to the active platform adapter.

    Workers call ``stream()`` for log lines and ``report()`` for
    final results.  The bus formats output as Swarm Report cards
    when a platform adapter is connected, or drops messages when
    no adapter is present (headless mode).
    """

    def __init__(self, adapter: BusAdapter | None = None) -> None:
        self._adapter: BusAdapter = adapter or NullBusAdapter()
        self._subscribers: list[callable] = []  # type: ignore[type-arg]

    def set_adapter(self, adapter: BusAdapter) -> None:
        """Swap the active adapter (e.g. after gateway restart)."""
        self._adapter = adapter
        logger.info("[SwarmMessageBus] Adapter set to %s", type(adapter).__name__)

    @property
    def adapter(self) -> BusAdapter:
        return self._adapter

    def subscribe(self, callback: callable) -> None:  # type: ignore[type-arg]
        """Register a callback for bus events (used by TUI panels)."""
        self._subscribers.append(callback)

    async def _notify_subscribers(self, event_type: str, data: dict[str, Any]) -> None:
        for cb in self._subscribers:
            try:
                result = cb(event_type, data)
                import asyncio
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.debug("[SwarmMessageBus] subscriber callback failed", exc_info=True)

    async def stream(
        self, worker_name: str, worker_role: str, content: str, level: str = "info"
    ) -> None:
        """Log a status line from a worker."""
        msg = BusMessage(
            worker_name=worker_name,
            worker_role=worker_role,
            content=content,
            level=level,
        )
        await self._adapter.send(msg)
        await self._notify_subscribers("stream", {
            "worker_name": worker_name,
            "worker_role": worker_role,
            "content": content,
            "level": level,
            "timestamp": msg.timestamp,
        })

    async def report(
        self,
        worker_name: str,
        worker_role: str,
        status: str,
        output: str,
        duration_ms: float = 0.0,
        task_id: str = "",
    ) -> None:
        """Send a completion report card for a worker task."""
        report = SwarmReport(
            worker_name=worker_name,
            worker_role=worker_role,
            status=status,
            output=output[:500],
            duration_ms=duration_ms,
            task_id=task_id,
        )
        await self._adapter.send_report(report)
        await self._notify_subscribers("report", {
            "worker_name": worker_name,
            "worker_role": worker_role,
            "status": status,
            "duration_ms": duration_ms,
            "task_id": task_id,
        })

    async def request_approval(
        self,
        worker_name: str,
        task_description: str,
        proposed_output: str,
        task_id: str = "",
        timeout: float = 60.0,
    ) -> bool:
        """Request HITL approval. Returns True if approved, False if rejected or timed out."""
        import asyncio
        approval = ApprovalRequest(
            worker_name=worker_name,
            task_description=task_description,
            proposed_output=proposed_output[:300],
            task_id=task_id,
        )
        try:
            return await asyncio.wait_for(
                self._adapter.request_approval(approval),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("HITL approval timed out after %.1fs for %s", timeout, worker_name)
            return False


# Module-level singleton for the bus.
_bus: SwarmMessageBus | None = None


def get_message_bus() -> SwarmMessageBus:
    """Return the shared SwarmMessageBus instance."""
    global _bus
    if _bus is None:
        _bus = SwarmMessageBus()
    return _bus


def set_message_bus(bus: SwarmMessageBus) -> None:
    """Replace the shared SwarmMessageBus instance."""
    global _bus
    _bus = bus
