"""Swarm panel subpackage.

Re-exports sub-routers so the public create_swarm_router API remains 100% compatible.
Also provides backward-compatible aliases for test imports.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

# Import the registration functions from submodules.
from .routes_workers import register_workers_routes
from .routes_tasks import register_tasks_routes
from .routes_metrics import register_metrics_routes
from .routes_general import register_general_routes


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
    register_general_routes(general_router, templates, swarm_manager, config_store)

    return {
        "general": general_router,
        "tasks": tasks_router,
        "workers": workers_router,
    }


def create_swarm_router(
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> APIRouter:
    """Create the Swarm Panel router backed by the shared engine."""
    builder = SwarmRouterBuilder(templates, swarm_manager, config_store)
    return builder.build()


def _reset_swarm_state() -> None:
    """Reset the shared engine for tests."""
    from kazma_ui.services import reset_swarm_service
    reset_swarm_service()
    
    try:
        from kazma_core.swarm import get_swarm_engine, set_swarm_engine, SwarmEngine, SwarmConfig, TaskStore
        engine = get_swarm_engine()
        if engine is not None:
            store = getattr(engine, "_task_store", None)
            if store is not None and hasattr(store, "clear"):
                store.clear()
        # Create a clean engine
        new_engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=TaskStore())
        set_swarm_engine(new_engine)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).debug("_reset_swarm_state failed: %s", exc)


class SwarmRouterBuilder:
    """Backward-compatible wrapper that matches the original SwarmRouterBuilder API.

    Tests import SwarmRouterBuilder and call .build() on it.
    This class wraps the new modular registration functions.
    """

    def __init__(
        self,
        templates: Any,
        swarm_manager: Any = None,
        config_store: Any = None,
    ) -> None:
        self.templates = templates
        self.swarm_manager = swarm_manager
        self.config_store = config_store

        self.router = APIRouter(tags=["swarm"])
        self.tasks_router = APIRouter()
        self.workers_router = APIRouter()
        self.general_router = APIRouter()

    def build(self) -> APIRouter:
        """Build and return the combined router."""
        # Register all routes using the modular functions
        register_workers_routes(self.workers_router, self.templates, self.swarm_manager, self.config_store)
        register_tasks_routes(self.tasks_router, self.templates, self.swarm_manager, self.config_store)
        register_metrics_routes(self.general_router, self.templates, self.swarm_manager, self.config_store)
        register_general_routes(self.general_router, self.templates, self.swarm_manager, self.config_store)

        # Mount sub-routers
        self.router.include_router(self.general_router)
        self.router.include_router(self.tasks_router)
        self.router.include_router(self.workers_router)
        return self.router
