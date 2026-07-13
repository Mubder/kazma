"""Swarm panel subpackage.

Live implementation of the Swarm UI routers. The historical sibling module
``kazma_ui/swarm_panel.py`` was shadowed by this package and is deleted;
any unique behavior (SSE mount + engine wiring) lives here.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from .routes_general import register_general_routes
from .routes_metrics import register_metrics_routes
from .routes_tasks import register_tasks_routes
from .routes_workers import register_workers_routes

logger = logging.getLogger(__name__)


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
        from kazma_core.swarm import (
            SwarmConfig,
            SwarmEngine,
            TaskStore,
            get_swarm_engine,
            set_swarm_engine,
        )

        engine = get_swarm_engine()
        if engine is not None:
            store = getattr(engine, "_task_store", None)
            if store is not None and hasattr(store, "clear"):
                store.clear()
        new_engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=TaskStore())
        set_swarm_engine(new_engine)
    except Exception as exc:
        logger.debug("_reset_swarm_state failed: %s", exc)


class SwarmRouterBuilder:
    """Build the combined swarm panel router (routes + task SSE stream).

    Tests import SwarmRouterBuilder and call .build() on it.
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
        self._sse_bus: Any = None

        # Mount swarm task SSE stream on the parent router (used by swarm.js).
        try:
            from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

            self._sse_bus = SSEEventBus()
            self.router.include_router(create_sse_router(event_bus=self._sse_bus))
            logger.info("[Swarm] SSE streaming router mounted at /api/swarm/tasks/{id}/stream")
        except ImportError:
            self._sse_bus = None
            logger.debug("[Swarm] swarm_sse module not available; SSE streaming disabled")

        # Register bus on the service facade so later engine resolve re-wires it.
        if self._sse_bus is not None:
            try:
                from kazma_ui.services import get_swarm_service

                get_swarm_service().register_sse_bus(self._sse_bus)
            except Exception:
                logger.debug("[Swarm] failed to register SSE bus on SwarmService", exc_info=True)

    def _wire_sse_to_engine(self) -> None:
        """Attach the SSE bus to the current SwarmEngine when available."""
        if self._sse_bus is None:
            return
        try:
            from kazma_ui.services import get_swarm_service
            from kazma_ui.swarm_sse import wire_engine_events

            engine = get_swarm_service().resolve_engine(self.swarm_manager)
            if engine is not None:
                wire_engine_events(engine, self._sse_bus)
        except Exception:
            logger.debug("[Swarm] failed to wire SSE events to engine", exc_info=True)

    def build(self) -> APIRouter:
        """Build and return the combined router."""
        register_workers_routes(
            self.workers_router, self.templates, self.swarm_manager, self.config_store
        )
        register_tasks_routes(
            self.tasks_router, self.templates, self.swarm_manager, self.config_store
        )
        register_metrics_routes(
            self.general_router, self.templates, self.swarm_manager, self.config_store
        )
        register_general_routes(
            self.general_router, self.templates, self.swarm_manager, self.config_store
        )

        self.router.include_router(self.general_router)
        self.router.include_router(self.tasks_router)
        self.router.include_router(self.workers_router)

        # Best-effort engine wiring at build time (engine may not exist yet).
        self._wire_sse_to_engine()
        return self.router
