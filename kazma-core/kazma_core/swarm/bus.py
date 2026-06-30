"""Platform-agnostic message bus for swarm worker output streaming.

Workers stream logs, status updates, and interim outputs through
the bus without knowing the specific platform (Telegram/Discord/Slack).
The bus routes messages to the active adapter, formats Swarm Report
cards, and supports Human-in-the-Loop approval requests.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Approval timeout: how long to wait for a reaction before auto-rejecting.
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
    implementation that knows how to format and deliver messages.

    On Telegram this means calling the sendMessage REST API.
    On Discord it means posting to a webhook or channel.
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


# ── Telegram adapter ───────────────────────────────────────────────────────


class TelegramBusAdapter(BusAdapter):
    """Deliver bus messages to a Telegram chat via HTTP API.

    Requires a bot token and chat ID.  Messages are formatted with
    MarkdownV2 for bold, code blocks, and structured output.

    Args:
        bot_token:  Telegram bot token (e.g. ``123:abc``).
        chat_id:    Target chat (group or DM).
    """

    def __init__(self, bot_token: str, chat_id: int | str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._pending_approvals: dict[str, asyncio.Event] = {}
        self._pending_results: dict[str, bool] = {}
        self._http: Any = None  # lazy httpx client

    async def _ensure_http(self) -> Any:
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def _post(self, text: str, parse_mode: str = "MarkdownV2") -> None:
        """Send a text message to the configured chat."""
        try:
            http = await self._ensure_http()
            await http.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={
                    "chat_id": self._chat_id,
                    "text": text[:4096],
                    "parse_mode": parse_mode,
                },
            )
        except Exception as exc:
            logger.warning("[TelegramBus] sendMessage failed: %s", exc)

    def _escape_md(self, text: str) -> str:
        """Escape MarkdownV2 special characters for Telegram."""
        chars = r"_*[]()~`>#+-=|{}.!"
        for c in chars:
            text = text.replace(c, f"\\{c}")
        return text

    async def send(self, message: BusMessage) -> None:
        icon = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "success": "✅"}.get(message.level, "📍")
        safe_name = self._escape_md(message.worker_name)
        safe_content = self._escape_md(message.content[:300])
        text = f"{icon} *{safe_name}* \\[{message.level}\\]\n{safe_content}"
        if message.worker_role:
            text += f"\n\\_Role: {self._escape_md(message.worker_role)}\\_"
        await self._post(text)

    async def send_report(self, report: SwarmReport) -> None:
        icon = {"success": "✅", "error": "❌", "timeout": "⏰", "rejected": "🚫"}.get(report.status, "📍")
        safe_name = self._escape_md(report.worker_name)
        safe_output = self._escape_md(report.output[:500])
        text = (
            f"🐝 *SWARM REPORT*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Worker: {safe_name}\n"
            f"Role: {self._escape_md(report.worker_role)}\n"
            f"Status: {icon} {report.status}\n"
            f"Duration: {report.duration_ms / 1000:.1f}s\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"```\n{safe_output}\n```"
        )
        await self._post(text)

    async def request_approval(self, approval: ApprovalRequest) -> bool:
        safe_name = self._escape_md(approval.worker_name)
        safe_task = self._escape_md(approval.task_description[:200])
        safe_output = self._escape_md(approval.proposed_output[:300])
        text = (
            f"⚠️ *APPROVAL REQUIRED*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Worker: {safe_name}\n"
            f"Task: {safe_task}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Proposed output:\n"
            f"```\n{safe_output}\n```\n\n"
            f"React 👍 to approve or 👎 to reject\n"
            f"\\(auto\\-reject in {_DEFAULT_APPROVAL_TIMEOUT:.0f}s\\)"
        )
        await self._post(text)

        # Wait for reaction (callback-based approval)
        event = asyncio.Event()
        self._pending_approvals[approval.task_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=_DEFAULT_APPROVAL_TIMEOUT)
            return self._pending_results.get(approval.task_id, False)
        except asyncio.TimeoutError:
            logger.warning("[TelegramBus] Approval timed out for task %s", approval.task_id)
            return False
        finally:
            self._pending_approvals.pop(approval.task_id, None)
            self._pending_results.pop(approval.task_id, None)

    def approve(self, task_id: str) -> None:
        """Signal approval for a pending approval request."""
        if task_id in self._pending_approvals:
            self._pending_results[task_id] = True
            self._pending_approvals[task_id].set()

    def reject(self, task_id: str) -> None:
        """Signal rejection for a pending approval request."""
        if task_id in self._pending_approvals:
            self._pending_results[task_id] = False
            self._pending_approvals[task_id].set()

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


# ── Message Bus ────────────────────────────────────────────────────────────


class SwarmMessageBus:
    """Routes worker output to the active platform adapter.

    Workers call ``stream()`` for log lines and ``report()`` for
    final results.  The bus formats output as Swarm Report cards
    when a platform adapter is connected, or drops messages when
    no adapter is present (headless mode).

    Usage::

        bus = SwarmMessageBus(TelegramBusAdapter(token, chat_id))
        await bus.stream(worker, "Starting analysis...")
        await bus.report(worker, result)

    """

    def __init__(self, adapter: BusAdapter | None = None) -> None:
        self._adapter: BusAdapter = adapter or NullBusAdapter()

    def set_adapter(self, adapter: BusAdapter) -> None:
        """Swap the active adapter (e.g. after gateway restart)."""
        self._adapter = adapter
        logger.info("[SwarmMessageBus] Adapter set to %s", type(adapter).__name__)

    @property
    def adapter(self) -> BusAdapter:
        return self._adapter

    async def stream(self, worker_name: str, worker_role: str, content: str, level: str = "info") -> None:
        """Log a status line from a worker."""
        await self._adapter.send(BusMessage(
            worker_name=worker_name,
            worker_role=worker_role,
            content=content,
            level=level,
        ))

    async def report(self, worker_name: str, worker_role: str, status: str,
                     output: str, duration_ms: float = 0.0, task_id: str = "") -> None:
        """Send a completion report card for a worker task."""
        await self._adapter.send_report(SwarmReport(
            worker_name=worker_name,
            worker_role=worker_role,
            status=status,
            output=output[:500],
            duration_ms=duration_ms,
            task_id=task_id,
        ))

    async def request_approval(self, worker_name: str, task_description: str,
                               proposed_output: str, task_id: str = "") -> bool:
        """Request HITL approval for a high-impact output. Returns True if approved."""
        return await self._adapter.request_approval(ApprovalRequest(
            worker_name=worker_name,
            task_description=task_description,
            proposed_output=proposed_output[:300],
            task_id=task_id,
        ))


# Module-level singleton for the bus (optional — can also be injected).
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
