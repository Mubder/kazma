"""Metrics routes for the swarm panel.

Extracted for maintainability.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from kazma_ui.services import get_swarm_service


def register_metrics_routes(
    router: APIRouter,
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> None:
    """Register metrics related routes."""
    @router.get("/api/swarm/metrics")
    async def swarm_metrics() -> JSONResponse:
        svc = get_swarm_service()
        engine = svc._get_engine() if hasattr(svc, "_get_engine") else None
        if engine is None:
            return JSONResponse({"metrics": {}})
        # Example using public
        if hasattr(engine, "get_all_circuit_breaker_status"):
            breakers = engine.get_all_circuit_breaker_status()
            return JSONResponse({"metrics": {"breakers": breakers}})
        return JSONResponse({"metrics": {}})

    # More metrics and SSE hooks extracted here.
