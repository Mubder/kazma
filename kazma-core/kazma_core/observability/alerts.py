"""Alerts module for system observability."""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class AlertChannel(abc.ABC):
    """Abstract base class for pluggable alert delivery channels."""

    @abc.abstractmethod
    def name(self) -> str:
        """The unique name of this channel."""
        pass

    @abc.abstractmethod
    async def deliver(self, alert: dict[str, Any]) -> None:
        """Deliver the structured alert payload to the channel."""
        pass


class BusAlertChannel(AlertChannel):
    """Deliver alerts to the active SwarmMessageBus platform adapters (Telegram/Slack/Discord)."""

    def name(self) -> str:
        return "message_bus"

    async def deliver(self, alert: dict[str, Any]) -> None:
        try:
            from kazma_core.swarm.bus import get_message_bus
            bus = get_message_bus()
            await bus.send_alert(
                title=alert["title"],
                subsystem=alert["subsystem"],
                status=alert["status"],
                reason=alert["reason"],
                callback_id=alert["callback_id"],
                button_text=alert["button_text"],
            )
        except Exception as exc:
            logger.error("[BusAlertChannel] Failed to deliver alert: %s", exc, exc_info=True)


class LogAlertChannel(AlertChannel):
    """Deliver alerts to the system log."""

    def name(self) -> str:
        return "log"

    async def deliver(self, alert: dict[str, Any]) -> None:
        logger.warning(
            "[LogAlertChannel] Alert: %s (Subsystem: %s, Status: %s, Reason: %s)",
            alert["title"],
            alert["subsystem"],
            alert["status"],
            alert["reason"],
        )


class AlertDispatcher:
    """Dispatches subsystem status alerts to active platform adapters."""

    _channels: list[AlertChannel] = []
    _recent_alerts: list[dict[str, Any]] = []
    _initialized = False

    @classmethod
    def _init_default_channels(cls) -> None:
        if not cls._initialized:
            cls._channels = [BusAlertChannel(), LogAlertChannel()]
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
    def get_recent_alerts(cls) -> list[dict[str, Any]]:
        """Return recently broadcasted alerts."""
        return list(cls._recent_alerts)

    @classmethod
    def clear_alerts(cls) -> None:
        """Clear the in-memory alerts buffer."""
        cls._recent_alerts.clear()

    @classmethod
    async def trigger_system_alert(cls, subsystem: str = "Memory", status: str = "DEGRADED", message: str = "") -> None:
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
    ) -> None:
        """Broadcasts a rich status/permission alert to the active message bus."""
        cls._init_default_channels()

        alert_id = f"alert-{int(time.time())}-{subsystem.lower()}"
        alert_payload = {
            "id": alert_id,
            "title": title,
            "subsystem": subsystem,
            "status": status,
            "reason": reason,
            "callback_id": callback_id,
            "button_text": button_text,
            "timestamp": time.time(),
        }

        # Keep in-memory ring-buffer (max 50 alerts)
        cls._recent_alerts.append(alert_payload)
        if len(cls._recent_alerts) > 50:
            cls._recent_alerts.pop(0)

        tasks = []
        for channel in cls._channels:
            tasks.append(asyncio.create_task(channel.deliver(alert_payload)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def trigger_system_alert(subsystem: str = "Memory", status: str = "DEGRADED", message: str = "") -> None:
    """Trigger a system health alert and broadcast it to active platform adapters."""
    await AlertDispatcher.trigger_system_alert(subsystem=subsystem, status=status, message=message)
