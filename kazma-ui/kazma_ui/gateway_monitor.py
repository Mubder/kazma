"""Gateway Monitor — FastAPI router for the Gateway Monitor view.

Provides endpoints for:
  - GET /api/gateway/status  — snapshot of all adapters + queue log
  - POST /api/gateway/adapter/{name}/start  — start an adapter
  - POST /api/gateway/adapter/{name}/stop   — stop an adapter
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

_ROADMAP_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "roadmaps.json"


def create_gateway_router(gateway: Any) -> APIRouter:
    """Create the gateway monitor router with a reference to the GatewayManager."""

    router = APIRouter(prefix="/api/gateway", tags=["gateway"])

    @router.get("/status")
    async def gateway_status() -> dict[str, Any]:
        """Return full gateway status for the monitor UI."""
        return gateway.status_info()

    @router.post("/adapter/{name}/start")
    async def start_adapter(name: str) -> JSONResponse:
        success = await gateway.start_adapter(name)
        return JSONResponse(
            content={"status": "ok" if success else "error", "adapter": name},
            status_code=200 if success else 404,
        )

    @router.post("/adapter/{name}/stop")
    async def stop_adapter(name: str) -> JSONResponse:
        success = await gateway.stop_adapter(name)
        return JSONResponse(
            content={"status": "ok" if success else "error", "adapter": name},
            status_code=200 if success else 404,
        )

    @router.get("/roadmap")
    async def get_roadmap() -> dict[str, Any]:
        """Return the roadmap JSON."""
        try:
            return json.loads(_ROADMAP_PATH.read_text())
        except Exception as e:
            return {"error": str(e), "phases": []}

    return router
