"""Gateway Monitor — FastAPI router for the Gateway Monitor view.

Provides endpoints for:
  - GET  /api/gateway/status         — full status (adapters, persistence, threads)
  - POST /api/gateway/start          — start gateway
  - POST /api/gateway/stop           — stop gateway
  - GET  /api/gateway/roadmap        — project roadmap JSON

DELETE /api/sessions/{thread_id} is registered in dashboard.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

_ROADMAP_PATH = Path(__file__).resolve().parent.parent / "data" / "roadmaps.json"


def create_gateway_router(
    gateway: Any,
    session_store: Any = None,
    checkpointer: Any = None,
) -> APIRouter:
    """Create the gateway monitor router.

    Args:
        gateway:        GatewayManager instance.
        session_store:  SQLiteSessionStore for session deletion.
        checkpointer:   AsyncSqliteSaver for checkpoint deletion.

    Returns:
        APIRouter mounted at /api/gateway + /api/sessions.
    """

    router = APIRouter(tags=["gateway"])

    @router.get("/api/gateway/status")
    async def gateway_status() -> dict[str, Any]:
        """Full status for the Gateway Monitor panel.

        Returns:
            {
                "adapters": [{"platform", "status", "uptime_seconds"}],
                "persistence": {"session_store": {...}, "checkpointer": {...}, "active_threads": N},
                "threads": [{"thread_id", "platform", "display_name", "status", "last_active_seconds"}],
            }
        """
        return await gateway.get_status()

    @router.post("/api/gateway/start")
    async def start_gateway() -> dict[str, Any]:
        """Start the gateway and all adapters."""
        await gateway.start()
        return {"status": "started", **gateway.stats}

    @router.post("/api/gateway/stop")
    async def stop_gateway() -> dict[str, Any]:
        """Stop the gateway and all adapters."""
        await gateway.stop()
        return {"status": "stopped", **gateway.stats}

    @router.get("/api/gateway/roadmap")
    async def get_roadmap() -> dict[str, Any]:
        """Return the roadmap JSON."""
        try:
            return json.loads(_ROADMAP_PATH.read_text())
        except Exception as e:
            return {"error": str(e), "phases": []}

    # NOTE: DELETE /api/sessions/{thread_id} is registered in dashboard.py
    # to avoid duplicate route registration. This router only handles
    # gateway-specific endpoints.

    return router
