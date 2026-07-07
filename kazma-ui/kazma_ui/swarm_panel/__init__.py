"""Swarm panel subpackage.

Re-exports sub-routers so the public create_swarm_router API remains 100% compatible.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

# Import the registration functions from submodules.
# These will be implemented to add routes to the provided routers.
from .routes_workers import register_workers_routes
from .routes_tasks import register_tasks_routes
from .routes_metrics import register_metrics_routes


def create_swarm_panel_routers(
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> dict[str, APIRouter]:
    """Return the sub-routers for the swarm panel.

    This keeps the public surface stable while allowing internal decomposition.
    """
    general_router = APIRouter()
    tasks_router = APIRouter()
    workers_router = APIRouter()

    register_workers_routes(workers_router, templates, swarm_manager, config_store)
    register_tasks_routes(tasks_router, templates, swarm_manager, config_store)
    register_metrics_routes(general_router, templates, swarm_manager, config_store)

    return {
        "general": general_router,
        "tasks": tasks_router,
        "workers": workers_router,
    }
