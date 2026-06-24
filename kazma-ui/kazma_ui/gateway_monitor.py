"""Gateway Monitor — FastAPI router for the Gateway Monitor view.

Provides endpoints for:
<<<<<<< HEAD
  - GET /api/gateway/status  — snapshot of all adapters + queue depth
  - GET /api/gateway/roadmap — project roadmap JSON
=======
  - GET /api/gateway/status  — snapshot of all adapters + queue info
  - GET /api/gateway/roadmap  — project roadmap JSON

Designed to work with the kazma-gateway GatewayManager API.
>>>>>>> d7c7d00 (feat(ui): persistence-aware resume indicator + reset + gateway panel)
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

# ── Adapter status helpers ───────────────────────────────────────────

ADAPTER_PLATFORM = {
    "telegram": {"icon": "✈", "color": "#229ED9"},
    "discord": {"icon": "◆", "color": "#5865F2"},
    "slack": {"icon": "#", "color": "#4A154B"},
}


def _adapter_info(adapter: Any) -> dict[str, Any]:
    """Extract a status snapshot from an adapter."""
    running = getattr(adapter, "_running", False)
    status = "running" if running else "stopped"
    name = getattr(adapter, "name", "unknown")
    platform = getattr(adapter, "platform", name)
    plat = ADAPTER_PLATFORM.get(platform, {"icon": "🔌", "color": "#3b82f6"})
    return {
        "name": name,
        "platform": platform,
        "status": status,
        "icon": plat["icon"],
        "color": plat["color"],
        "message_count": getattr(adapter, "_message_count", 0),
        "error_count": getattr(adapter, "_error_count", 0),
        "last_error": getattr(adapter, "_last_error", None),
    }


def create_gateway_router(gateway: Any) -> APIRouter:
<<<<<<< HEAD
    """Create the gateway monitor router with a reference to the GatewayManager.

    Uses GatewayManager.stats property for status, and GatewayManager
    start()/stop() methods for lifecycle control.

    Args:
        gateway: GatewayManager instance.

    Returns:
        APIRouter mounted at /api/gateway.
    """
=======
    """Create the gateway monitor router."""
>>>>>>> d7c7d00 (feat(ui): persistence-aware resume indicator + reset + gateway panel)

    router = APIRouter(prefix="/api/gateway", tags=["gateway"])

    @router.get("/status")
    async def gateway_status() -> dict[str, Any]:
        """Return full gateway status for the monitor UI."""
        adapters = [_adapter_info(a) for a in gateway.adapters]
        queue = getattr(gateway, "queue", None)
        queue_size = queue.qsize() if queue else 0
        queue_max = queue.maxsize if queue and hasattr(queue, "maxsize") else 100

<<<<<<< HEAD
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
=======
        return {
            "started": getattr(gateway, "_started", False),
            "queue_size": queue_size,
            "queue_max": queue_max,
            "adapters": adapters,
            "server_time": time.time(),
        }
>>>>>>> d7c7d00 (feat(ui): persistence-aware resume indicator + reset + gateway panel)

    @router.get("/roadmap")
    async def get_roadmap() -> dict[str, Any]:
        """Return the roadmap JSON."""
        try:
            return json.loads(_ROADMAP_PATH.read_text())
        except Exception as e:
            return {"error": str(e), "phases": []}

    return router
