"""SSE event bridge for SwarmEngine — extracted helper (S3/S5).

Keeps engine free of bus emit boilerplate. Safe no-op when no bus is set.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SseBridge:
    """Thin wrapper around an optional SSEEventBus-like object."""

    def __init__(self) -> None:
        self._bus: Any = None

    def set_bus(self, bus: Any) -> None:
        """Register bus (must implement ``emit(task_id, event, data)``)."""
        self._bus = bus

    @property
    def bus(self) -> Any:
        return self._bus

    def emit(self, task_id: str, event: str, data: dict[str, Any]) -> None:
        """Emit an event if a bus is registered; swallow bus errors at DEBUG."""
        if self._bus is None:
            return
        try:
            self._bus.emit(task_id, event, data)
        except Exception as sse_exc:
            logger.debug(
                "[SseBridge] emit failed for %s:%s: %s",
                task_id,
                event,
                sse_exc,
                exc_info=True,
            )
