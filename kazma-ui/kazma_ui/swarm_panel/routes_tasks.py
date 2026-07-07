"""Task routes for the swarm panel.

Extracted for maintainability.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from kazma_ui.services import get_swarm_service


def register_tasks_routes(
    router: APIRouter,
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> None:
    """Register task related routes."""
    @router.get("/api/swarm/tasks")
    async def swarm_active_tasks() -> JSONResponse:
        svc = get_swarm_service()
        engine = svc._get_engine() if hasattr(svc, "_get_engine") else None
        if engine is None:
            return JSONResponse({"tasks": []})
        # Use public if available
        if hasattr(engine, "list_active_tasks"):
            tasks = engine.list_active_tasks()
            return JSONResponse({"tasks": [t.to_dict() if hasattr(t, 'to_dict') else dict(t) for t in tasks]})
        return JSONResponse({"tasks": []})

    # Additional task routes would be extracted here using the facade.
    # This keeps the public API stable.
