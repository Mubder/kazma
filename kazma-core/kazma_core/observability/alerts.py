"""Alerts module for system observability."""

from __future__ import annotations

import logging
from kazma_core.swarm.bus import get_message_bus

logger = logging.getLogger(__name__)


class AlertDispatcher:
    """Dispatches subsystem status alerts to active platform adapters."""

    @staticmethod
    async def broadcast_alert(
        title: str,
        subsystem: str,
        status: str,
        reason: str,
        callback_id: str,
        button_text: str,
    ) -> None:
        """Broadcasts a rich status/permission alert to the active message bus."""
        logger.warning(
            "[AlertDispatcher] Broadcasting alert: %s (%s is %s: %s)",
            title,
            subsystem,
            status,
            reason,
        )
        try:
            bus = get_message_bus()
            await bus.send_alert(
                title=title,
                subsystem=subsystem,
                status=status,
                reason=reason,
                callback_id=callback_id,
                button_text=button_text,
            )
        except Exception as exc:
            logger.error("[AlertDispatcher] Failed to broadcast alert: %s", exc, exc_info=True)
