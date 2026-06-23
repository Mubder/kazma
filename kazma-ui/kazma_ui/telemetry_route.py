"""Telemetry SSE Route — Streams hardware metrics to the frontend.

Provides GET /api/telemetry/stream as a Server-Sent Events endpoint
that pushes CPU/RAM/GPU/VRAM snapshots at 1 Hz to the Chart.js dashboard.

SSE format::

    data: {"cpu": 45.2, "ram_used_gb": 16.4, "ram_total_gb": 32.0,
           "gpu": 88.0, "vram_used_gb": 14.2, "vram_total_gb": 24.0,
           "timestamp": 1719162000.0}

    data: {"cpu": 42.1, ...}

Each ``data:`` line is a JSON object.  The stream runs indefinitely
until the client disconnects (``asyncio.CancelledError``).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["telemetry"])


def create_telemetry_router(monitor: Any = None) -> APIRouter:
    """Create the telemetry SSE router.

    Args:
        monitor: HardwareMonitor instance.  Created lazily if None.

    Returns:
        APIRouter with GET /api/telemetry/stream registered.
    """
    r = APIRouter(tags=["telemetry"])

    _monitor = monitor

    def _get_monitor() -> Any:
        nonlocal _monitor
        if _monitor is None:
            from kazma_core.telemetry import HardwareMonitor

            _monitor = HardwareMonitor()
        return _monitor

    @r.get("/api/telemetry/stream")
    async def telemetry_stream() -> StreamingResponse:
        """Stream hardware telemetry as Server-Sent Events.

        Returns:
            StreamingResponse with Content-Type text/event-stream.
            Each event is a ``data: <json>`` line at 1 Hz.

        The stream terminates cleanly when:
          - The client closes the connection (CancelledError).
          - The server shuts down.
        """
        hw = _get_monitor()

        async def _event_generator() -> AsyncGenerator[str, None]:
            logger.info("Telemetry SSE stream opened")
            try:
                async for snapshot in hw.stream(interval=1.0):
                    payload = json.dumps(snapshot.to_dict(), ensure_ascii=False)
                    yield f"data: {payload}\n\n"
            except asyncio.CancelledError:
                logger.info("Telemetry SSE stream closed (client disconnect)")
            except Exception as exc:
                logger.error("Telemetry SSE stream error: %s", exc)
                error_payload = json.dumps({"error": str(exc), "timestamp": 0})
                yield f"data: {error_payload}\n\n"

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @r.get("/api/telemetry/snapshot")
    async def telemetry_snapshot() -> dict[str, Any]:
        """Single telemetry reading (non-streaming).

        Returns:
            Dict with cpu, ram, gpu, vram, timestamp fields.
        """
        hw = _get_monitor()
        snapshot = await hw.get_stats()
        return snapshot.to_dict()

    return r
