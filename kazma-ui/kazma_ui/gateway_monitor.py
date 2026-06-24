"""Gateway Monitor — FastAPI router for the Gateway Monitor view.

Provides endpoints for:
  - GET /api/gateway/status  — snapshot of all adapters + queue depth
  - GET /api/gateway/roadmap — project roadmap JSON
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

_ROADMAP_PATH = Path(__file__).resolve().parent.parent / "data" / "roadmaps.json"


def create_gateway_router(gateway: Any) -> APIRouter:
    """Create the gateway monitor router with a reference to the GatewayManager.

    Uses GatewayManager.stats property for status, and GatewayManager
    start()/stop() methods for lifecycle control.

    Args:
        gateway: GatewayManager instance.

    Returns:
        APIRouter mounted at /api/gateway.
    """

    router = APIRouter(prefix="/api/gateway", tags=["gateway"])

    @router.get("/status")
    async def gateway_status() -> dict[str, Any]:
        """Return full gateway status for the monitor UI.

        Uses the GatewayManager.stats property which includes:
          - started: bool
          - shutdown_signalled: bool
          - adapters: list of {name, running}
          - queue_depth: int
          - queue_maxsize: int
          - handler_registered: bool
        """
        info = dict(gateway.stats)
        info["server_time"] = time.time()
        return info

    @router.post("/start")
    async def start_gateway() -> dict[str, Any]:
        """Start the gateway and all adapters."""
        await gateway.start()
        return {"status": "started", **gateway.stats}

    @router.post("/stop")
    async def stop_gateway() -> dict[str, Any]:
        """Stop the gateway and all adapters."""
        await gateway.stop()
        return {"status": "stopped", **gateway.stats}

    @router.get("/roadmap")
    async def get_roadmap() -> dict[str, Any]:
        """Return the roadmap JSON."""
        try:
            return json.loads(_ROADMAP_PATH.read_text())
        except Exception as e:
            return {"error": str(e), "phases": []}

    return router
