"""Gateway Monitor — FastAPI router for the Gateway Monitor view.

Endpoints:
  - GET  /api/gateway/status  — snapshot of adapters, persistence, threads
  - GET  /api/gateway/roadmap — project roadmap JSON

Designed to work with the kazma-gateway GatewayManager API.
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

# ── Mock persistence/threads (swap for real LangGraph data when gw-012 ships) ──

_MOCK_PERSISTENCE = {
    "session_store": {"type": "sqlite", "size_kb": 124, "path": "~/.kazma/sessions.db"},
    "checkpointer": {"type": "sqlite", "size_kb": 2100, "path": "~/.kazma/checkpoints.db"},
    "active_threads": 0,
}

_MOCK_THREADS: list[dict[str, Any]] = [
    # {
    #     "thread_id": "uuid-...",
    #     "platform": "telegram",
    #     "display_name": "user_A",
    #     "status": "active",
    #     "last_active_seconds": 120,
    # },
]


def _adapter_info(adapter: Any) -> dict[str, Any]:
    """Extract a status snapshot from an adapter."""
    running = getattr(adapter, "_running", False)
    status = "running" if running else "stopped"
    name = getattr(adapter, "name", "unknown")
    platform = getattr(adapter, "platform", name)
    return {
        "name": name,
        "platform": platform,
        "status": status,
        "message_count": getattr(adapter, "_message_count", 0),
        "error_count": getattr(adapter, "_error_count", 0),
        "last_error": getattr(adapter, "_last_error", None),
    }


def create_gateway_router(gateway: Any) -> APIRouter:
    """Create the gateway monitor router."""

    # ── Accumulated metrics ──
    _prev_counts: dict[str, int] = {"inbound": 0, "outbound": 0, "errors": 0}

    router = APIRouter(prefix="/api/gateway", tags=["gateway"])

    @router.get("/status")
    async def gateway_status() -> dict[str, Any]:
        """Return full gateway status for the monitor UI."""
        adapters = [_adapter_info(a) for a in gateway.adapters]
        queue = getattr(gateway, "queue", None)

        # Aggregate metrics from all adapters
        inbound_total = sum(a.get("message_count", 0) for a in adapters)
        errors_total = sum(a.get("error_count", 0) for a in adapters)
        # Outbound approximates inbound minus errors (one reply per msg)
        outbound_total = max(0, inbound_total - errors_total)

        return {
            "started": getattr(gateway, "_started", False),
            "queue_size": queue.qsize() if queue else 0,
            "queue_max": queue.maxsize if queue and hasattr(queue, "maxsize") else 100,
            "adapters": adapters,
            "metrics": {
                "inbound_total": inbound_total,
                "outbound_total": outbound_total,
                "errors_total": errors_total,
            },
            "persistence": _MOCK_PERSISTENCE,
            "threads": _MOCK_THREADS,
            "server_time": time.time(),
        }

    @router.get("/roadmap")
    async def get_roadmap() -> dict[str, Any]:
        try:
            return json.loads(_ROADMAP_PATH.read_text())
        except Exception as e:
            return {"error": str(e), "phases": []}

    return router
