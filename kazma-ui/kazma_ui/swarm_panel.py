"""Swarm Panel, backed by the shared SwarmEngine registry.

TODO (audit): Small split started. See architecture docs.
Next steps: extract worker CRUD, task dispatch, metrics into submodules.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)

from .services import get_swarm_service  # new public facade

try:
    from kazma_core.swarm import (
        SwarmConfig,
        SwarmEngine,
        SwarmManager,
        SwarmTask,
        TaskStore,
        TaskType,
        WorkerCapabilities,
        WorkerConfig,
        get_swarm_engine,
        set_swarm_engine,
    )
except ImportError:  # pragma: no cover
    SwarmConfig = None  # type: ignore[assignment,misc]
    SwarmEngine = None  # type: ignore[assignment,misc]
    SwarmManager = None  # type: ignore[assignment,misc]
    SwarmTask = None  # type: ignore[assignment,misc]
    TaskStore = None  # type: ignore[assignment,misc]
    TaskType = None  # type: ignore[assignment,misc]
    WorkerCapabilities = None  # type: ignore[assignment,misc]
    WorkerConfig = None  # type: ignore[assignment,misc]
    get_swarm_engine = None  # type: ignore[assignment]
    set_swarm_engine = None  # type: ignore[assignment]

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _has_swarm_core() -> bool:
    """Return whether kazma_core.swarm is importable."""
    return all(
        item is not None
        for item in (
            SwarmConfig,
            SwarmEngine,
            SwarmTask,
            TaskType,
            WorkerConfig,
            get_swarm_engine,
            set_swarm_engine,
        )
    )


def _create_empty_engine(task_store: Any = None) -> Any:
    """Create an empty shared engine when swarm core is available.

    Args:
        task_store: Optional shared store to keep persistence consistent.
    """
    if not _has_swarm_core():
        return None
    store = task_store or (TaskStore() if TaskStore is not None else None)
    return SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)


class _SharedTaskStore:
    """Encapsulated shared task store state (replaces bare module-level global)."""
    _instance: Any | None = None

    @classmethod
    def get(cls) -> Any | None:
        return cls._instance

    @classmethod
    def set(cls, value: Any | None) -> None:
        cls._instance = value

    @classmethod
    def reset(cls) -> None:
        cls._instance = None


def _resolve_engine(swarm_manager: Any = None) -> Any:
    """Resolve the engine used by the router at request time."""
    if not _has_swarm_core():
        return None

    engine: Any | None
    if isinstance(swarm_manager, SwarmEngine):
        engine = swarm_manager
    else:
        engine = getattr(swarm_manager, "engine", None)
        if not isinstance(engine, SwarmEngine):
            engine = None
        if engine is None:
            engine = get_swarm_engine()

    if engine is None:
        engine = _create_empty_engine(_SharedTaskStore.get())
    else:
        # Share the existing engine's task store with fallback engines.
        _SharedTaskStore.set(getattr(engine, "task_store", None) or _SharedTaskStore.get())
    if engine is not None:
        set_swarm_engine(engine)
    return engine


def _reset_swarm_state() -> None:
    """Reset the shared engine for tests."""
    if not _has_swarm_core():
        return
    _SharedTaskStore.reset()
    engine = _create_empty_engine()
    # Clear persisted task/metric data so tests start with a clean slate.
    store = getattr(engine, "_task_store", None)
    if store is not None and hasattr(store, "clear"):
        store.clear()
    set_swarm_engine(engine)


def _swarm_started(engine: Any) -> bool:
    """Return whether any worker is running."""
    if engine is None:
        return False
    svc = get_swarm_service()
    workers = svc.list_workers() if hasattr(svc, 'list_workers') else getattr(engine, "_workers", {}).values()
    return any(getattr(worker, "_running", False) for worker in workers)


def _worker_status(worker: Any) -> str:
    """Return the UI-facing worker state."""
    if not worker._running:
        return "offline"
    if worker.busy:
        return "busy"
    return "online"


def _coerce_task_type(payload: dict[str, Any], worker_names: list[str]) -> Any:
    """Resolve the requested swarm task type from the API payload."""
    if TaskType is None:
        return None

    raw_value = str(payload.get("pattern") or payload.get("type") or "").strip().lower()
    normalized = raw_value.replace("-", "_")

    if normalized in {"fan_out", "fanout"}:
        return TaskType.FAN_OUT
    if normalized == "pipeline":
        return TaskType.PIPELINE
    if normalized == "consult":
        return TaskType.CONSULT
    if normalized == "conditional":
        return TaskType.CONDITIONAL
    if normalized == "broadcast":
        return TaskType.BROADCAST
    if normalized == "dispatch":
        return TaskType.DISPATCH
    return TaskType.DISPATCH if len(worker_names) == 1 else TaskType.BROADCAST


def _coerce_timeout(payload: dict[str, Any]) -> float:
    """Return a numeric task timeout from the API payload."""
    try:
        return float(payload.get("timeout", 300.0))
    except (TypeError, ValueError):
        return 300.0


def _flatten_swarm_task(task: Any) -> dict[str, Any]:
    """Flatten a SwarmTask (with nested result) to the UI-expected shape.

    The dashboard and detail view expect TaskResult-like fields at the top
    level: ``task_id``, ``status``, ``worker_results``, ``aggregated_output``,
    ``synthesized_output``, ``duration_seconds``, etc.  ``SwarmTask.to_dict()``
    nests those under a ``result`` key, so this helper promotes them while
    preserving task-level identity fields (``id``/``type``/``prompt``).
    """
    data = task.to_dict() if hasattr(task, "to_dict") else dict(task)
    result = data.get("result") or {}
    return {
        "id": data.get("id"),
        "task_id": data.get("id"),
        "type": data.get("type"),
        "prompt": data.get("prompt"),
        "context": data.get("context"),
        "workers": data.get("workers", []),
        "status": result.get("status", data.get("status")),
        "created_at": data.get("created_at"),
        "started_at": data.get("started_at"),
        "completed_at": data.get("completed_at"),
        "duration_seconds": result.get("duration_seconds"),
        "total_cost": result.get("total_cost"),
        "total_tokens": result.get("total_tokens"),
        "worker_results": result.get("worker_results", []),
        "individual_opinions": result.get("individual_opinions", []),
        "aggregated_output": result.get("aggregated_output"),
        "synthesized_output": result.get("synthesized_output"),
        "error": result.get("error"),
        "metadata": result.get("metadata", data.get("metadata", {})),
    }


def _serialize_worker(worker: Any, engine: Any = None) -> dict[str, Any]:
    """Convert a worker object into a response-friendly dict.

    If ``engine`` is provided, the circuit breaker status is included.
    """
    capabilities = None
    if hasattr(worker, "capabilities") and worker.capabilities is not None:
        if hasattr(worker.capabilities, "to_dict"):
            capabilities = worker.capabilities.to_dict()
        else:
            capabilities = {"role": getattr(worker.capabilities, "role", "")}
    result = {
        "name": worker.name,
        "model": worker.model or "?",
        "provider": worker.provider or "?",
        "type": worker.worker_type,
        "role": worker.role,
        "status": _worker_status(worker),
        "bot_token": "***" if getattr(worker, "bot_token", None) else None,
        "added_at": worker.added_at,
        "last_task": worker.last_task,
        "last_heartbeat": worker.last_heartbeat,
        "logs": list(worker.logs),
        "capabilities": capabilities,
    }
    # Attach circuit breaker state when the engine is available.
    if engine is not None and hasattr(engine, "get_circuit_breaker_status"):
        try:
            result["circuit_breaker"] = engine.get_circuit_breaker_status(worker.name)
        except Exception as exc:
            logger.debug("Circuit breaker status failed for %s: %s", worker.name, exc)
            result["circuit_breaker"] = {"state": "closed", "consecutive_failures": 0}
    return result


def _worker_views(engine: Any) -> list[dict[str, Any]]:
    """Return serialized worker views for templates and APIs."""
    if engine is None:
        return []
    svc = get_swarm_service()
    workers = svc.list_workers() if hasattr(svc, 'list_workers') else getattr(engine, "_workers", {}).values()
    return [_serialize_worker(worker, engine) for worker in workers]


def _build_worker_config(payload: dict[str, Any]) -> Any:
    """Create a WorkerConfig from a UI payload."""
    if WorkerConfig is None:
        return None
    worker_type = {"in-process": "in_process", "telegram": "telegram_bot"}.get(
        payload.get("type", "in-process"),
        "in_process",
    )
    # Build capabilities from the payload so workers are routable
    caps_data = payload.get("capabilities") or {}
    capabilities = None
    if caps_data:
        try:
            from kazma_core.swarm.config import WorkerCapabilities
            capabilities = WorkerCapabilities.from_dict(caps_data)
        except Exception as exc:
            logger.debug("WorkerCapabilities parse failed: %s", exc)
            capabilities = None

    return WorkerConfig(
        name=(payload.get("name") or "").strip(),
        type=worker_type,
        model=payload.get("model", "deepseek-chat"),
        provider=payload.get("provider", "deepseek"),
        role=payload.get("role", ""),
        system_prompt=payload.get("system_prompt", ""),
        capabilities=capabilities,
    )


def _sync_external_manager_add(
    swarm_manager: Any,
    worker_config: Any,
    engine: Any,
) -> None:
    """Keep mock or external managers informed about UI-added workers."""
    manager_engine = getattr(swarm_manager, "engine", None)
    if not isinstance(manager_engine, SwarmEngine):
        manager_engine = None
    if swarm_manager is None or manager_engine is engine:
        return
    add_worker = getattr(swarm_manager, "add_worker", None)
    if callable(add_worker):
        add_worker(worker_config)


def _sync_external_manager_remove(
    swarm_manager: Any,
    name: str,
    engine: Any,
) -> None:
    """Keep mock or external managers informed about UI removals."""
    manager_engine = getattr(swarm_manager, "engine", None)
    if not isinstance(manager_engine, SwarmEngine):
        manager_engine = None
    if swarm_manager is None or manager_engine is engine:
        return
    remove_worker = getattr(swarm_manager, "remove_worker", None)
    if callable(remove_worker):
        try:
            remove_worker(name)
        except Exception as exc:
            logger.warning(
                "[Swarm] Failed to remove worker '%s' from delegated manager: %s",
                name,
                exc,
            )


class SwarmRouterBuilder:
    """Builder that decomposes the massive swarm panel router into modular sub-routers."""

    def __init__(self, templates: Any, swarm_manager: Any = None, config_store: Any = None) -> None:
        self.templates = templates
        self.swarm_manager = swarm_manager
        self.config_store = config_store

        self.router = APIRouter(tags=["swarm"])
        self.tasks_router = APIRouter()
        self.workers_router = APIRouter()
        self.general_router = APIRouter()

        self._registry = None
        self._sse_bus = None

        # Wire the SSE streaming endpoint on the parent router.
        try:
            from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

            self._sse_bus = SSEEventBus()
            _sse_router = create_sse_router(event_bus=self._sse_bus)
            self.router.include_router(_sse_router)
            logger.info("[Swarm] SSE streaming router mounted at /api/swarm/tasks/{id}/stream")
        except ImportError:
            self._sse_bus = None
            logger.debug("[Swarm] swarm_sse module not available, SSE streaming disabled")

    def _current_engine(self) -> Any:
        engine = _resolve_engine(self.swarm_manager)
        # Wire the SSE event bus to the engine on first use.
        if engine is not None and self._sse_bus is not None:
            try:
                from kazma_ui.swarm_sse import wire_engine_events
                wire_engine_events(engine, self._sse_bus)
            except Exception:
                logger.debug("[Swarm] failed to wire SSE events to engine", exc_info=True)
        return engine

    def _registry_options(self) -> dict[str, Any] | None:
        if self._registry is None:
            try:
                from kazma_core.model_registry import get_model_registry
                self._registry = get_model_registry()
            except RuntimeError:
                return None
        try:
            return self._registry.list_unified_options()
        except Exception:
            logger.warning("[Swarm] Failed to read unified model options", exc_info=True)
            return None

    def _config_store(self) -> Any:
        """Return the injected ConfigStore, or instantiate a fresh one."""
        if self.config_store is not None:
            return self.config_store
        try:
            from kazma_core.config_store import get_config_store
            return get_config_store()
        except Exception:
            logger.debug("[Swarm] ConfigStore unavailable", exc_info=True)
            return None

    def _build_general_routes(self) -> None:
        """Delegated to routes_general."""
        from .swarm_panel.routes_general import register_general_routes
        register_general_routes(
            self.general_router,
            self.templates,
            self.swarm_manager,
            self.config_store,
        )

    def _build_tasks_routes(self) -> None:
        """Delegated."""
        from .swarm_panel.routes_tasks import register_tasks_routes
        register_tasks_routes(self.tasks_router, self.templates, self.swarm_manager, self.config_store)
    def _build_workers_routes(self) -> None:
        """Delegated to sub module for decomposition."""
        from .swarm_panel.routes_workers import register_workers_routes
        register_workers_routes(self.workers_router, self.templates, self.swarm_manager, self.config_store)

    def build(self) -> APIRouter:
        self._build_general_routes()
        self._build_tasks_routes()
        self._build_workers_routes()

        # Mount the decoupled sub-routers on the parent router
        self.router.include_router(self.general_router)
        self.router.include_router(self.tasks_router)
        self.router.include_router(self.workers_router)
        return self.router


def create_swarm_router(
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> APIRouter:
    """Create the Swarm Panel router backed by the shared engine."""
    builder = SwarmRouterBuilder(templates, swarm_manager, config_store)
    return builder.build()
