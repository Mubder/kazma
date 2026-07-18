"""Alerts module for system observability."""

from __future__ import annotations

import abc
import asyncio
from dataclasses import asdict, dataclass
import logging
import time
from typing import Any

__all__ = ["AlertChannel", "AlertDispatcher", "AlertPayload", "BusAlertChannel", "LogAlertChannel", "PassThroughAlertChannel", "SseAlertChannel", "trigger_system_alert"]

logger = logging.getLogger(__name__)


@dataclass
class AlertPayload:
    """Structured and typed representation of a system health alert."""

    id: str
    title: str
    subsystem: str
    status: str
    reason: str
    callback_id: str
    button_text: str
    timestamp: float
    severity: str = "ERROR"  # e.g., "INFO", "WARNING", "ERROR", "CRITICAL"

    def __getitem__(self, key: str) -> Any:
        """Allow read-only dictionary-like subscription access for backward compatibility."""
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        """Allow dict-style safe retrieval for backward compatibility."""
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        """Convert the structured alert payload into a standard dictionary."""
        return asdict(self)


class AlertChannel(abc.ABC):
    """Abstract base class for pluggable alert delivery channels."""

    @abc.abstractmethod
    def name(self) -> str:
        """The unique name of this channel."""
        pass

    @abc.abstractmethod
    async def deliver(self, alert: AlertPayload) -> None:
        """Deliver the structured alert payload to the channel."""
        pass


class BusAlertChannel(AlertChannel):
    """Deliver alerts to the active SwarmMessageBus platform adapters (Telegram/Slack/Discord)."""

    def name(self) -> str:
        return "message_bus"

    async def deliver(self, alert: AlertPayload) -> None:
        try:
            from kazma_core.swarm.bus import get_message_bus
            bus = get_message_bus()
            await bus.send_alert(
                title=alert.title,
                subsystem=alert.subsystem,
                status=alert.status,
                reason=alert.reason,
                callback_id=alert.callback_id,
                button_text=alert.button_text,
            )
        except Exception as exc:
            logger.error("[BusAlertChannel] Failed to deliver alert: %s", exc, exc_info=True)


class LogAlertChannel(AlertChannel):
    """Deliver alerts to the system log."""

    def name(self) -> str:
        return "log"

    async def deliver(self, alert: AlertPayload) -> None:
        logger.warning(
            "[LogAlertChannel] [%s] Alert: %s (Subsystem: %s, Status: %s, Reason: %s)",
            alert.severity,
            alert.title,
            alert.subsystem,
            alert.status,
            alert.reason,
        )


class SseAlertChannel(AlertChannel):
    """Deliver alerts to Web SSE clients."""

    def name(self) -> str:
        return "sse"

    async def deliver(self, alert: AlertPayload) -> None:
        try:
            from kazma_core.swarm.engine import get_swarm_engine
            engine = get_swarm_engine()
            if engine and hasattr(engine, "_sse_bus") and engine._sse_bus:
                # Emit system alert event
                engine._sse_bus.emit(
                    task_id="system",
                    event="system_alert",
                    data=alert.to_dict()
                )
        except Exception as exc:
            logger.debug("[SseAlertChannel] Failed to deliver SSE alert: %s", exc)


class PassThroughAlertChannel(AlertChannel):
    """A pass-through channel that delivers alerts to a registered callback or queue."""

    def __init__(self, callback: Any) -> None:
        self._callback = callback

    def name(self) -> str:
        return "pass_through"

    async def deliver(self, alert: AlertPayload) -> None:
        if self._callback:
            try:
                if asyncio.iscoroutinefunction(self._callback):
                    await self._callback(alert)
                else:
                    self._callback(alert)
            except Exception as exc:
                logger.error("[PassThroughAlertChannel] Callback failed: %s", exc, exc_info=True)


class AlertDispatcher:
    """Dispatches subsystem status alerts to active platform adapters."""

    _channels: list[AlertChannel] = []
    _recent_alerts: list[AlertPayload] = []
    _initialized = False

    @classmethod
    def _init_default_channels(cls) -> None:
        if not cls._initialized:
            cls._channels = [BusAlertChannel(), LogAlertChannel(), SseAlertChannel()]
            cls._initialized = True

    @classmethod
    def register_channel(cls, channel: AlertChannel) -> None:
        """Register a new pluggable alert delivery channel."""
        cls._init_default_channels()
        cls._channels = [c for c in cls._channels if c.name() != channel.name()]
        cls._channels.append(channel)
        logger.info("[AlertDispatcher] Registered alert channel: %s", channel.name())

    @classmethod
    def unregister_channel(cls, name: str) -> None:
        """Unregister an alert channel by name."""
        cls._init_default_channels()
        cls._channels = [c for c in cls._channels if c.name() != name]
        logger.info("[AlertDispatcher] Unregistered alert channel: %s", name)

    @classmethod
    def get_recent_alerts(cls) -> list[AlertPayload]:
        """Return recently broadcasted alerts."""
        return list(cls._recent_alerts)

    @classmethod
    def clear_alerts(cls) -> None:
        """Clear the in-memory alerts buffer."""
        cls._recent_alerts.clear()

    @classmethod
    def resolve_alerts_for_subsystem(cls, subsystem: str) -> None:
        """Clear alerts for a specific subsystem once it is resolved."""
        cls._init_default_channels()
        cls._recent_alerts = [a for a in cls._recent_alerts if a.subsystem.lower() != subsystem.lower()]

    @classmethod
    def get_channels(cls) -> list[AlertChannel]:
        """Return registered alert channels."""
        cls._init_default_channels()
        return list(cls._channels)

    @classmethod
    async def trigger_system_alert(
        cls,
        subsystem: str = "Memory",
        status: str = "DEGRADED",
        message: str = "",
        severity: str = "ERROR",
    ) -> None:
        """Trigger a system health alert and broadcast it to active adapters."""
        title = f"Permission Required: {subsystem} Subsystem" if status == "DEGRADED" else f"KAZMA SYSTEM HEALTH: {subsystem} Active"
        callback_id = "sentence-transformers" if "sentence-transformers" in message or "sentence_transformers" in message else f"{subsystem.lower()}-init"
        button_text = "Install ML Dependencies" if "sentence-transformers" in message or "sentence_transformers" in message else "Resolve Subsystem Issue"

        await cls.broadcast_alert(
            title=title,
            subsystem=subsystem,
            status=status,
            reason=message,
            callback_id=callback_id,
            button_text=button_text,
            severity=severity,
        )

    @classmethod
    async def broadcast_alert(
        cls,
        title: str,
        subsystem: str,
        status: str,
        reason: str,
        callback_id: str,
        button_text: str,
        severity: str = "ERROR",
    ) -> None:
        """Broadcasts a rich status/permission alert to the active message bus."""
        cls._init_default_channels()

        alert_id = f"alert-{int(time.time())}-{subsystem.lower()}"
        alert_payload = AlertPayload(
            id=alert_id,
            title=title,
            subsystem=subsystem,
            status=status,
            reason=reason,
            callback_id=callback_id,
            button_text=button_text,
            timestamp=time.time(),
            severity=severity,
        )

        # Keep in-memory ring-buffer (max 50 alerts)
        cls._recent_alerts.append(alert_payload)
        if len(cls._recent_alerts) > 50:
            cls._recent_alerts.pop(0)

        tasks = []
        for channel in cls._channels:
            tasks.append(asyncio.create_task(channel.deliver(alert_payload)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def trigger_system_alert(
    subsystem: str = "Memory",
    status: str = "DEGRADED",
    message: str = "",
    severity: str = "ERROR",
) -> None:
    """Trigger a system health alert and broadcast it to active platform adapters."""
    await AlertDispatcher.trigger_system_alert(
        subsystem=subsystem, status=status, message=message, severity=severity
    )
