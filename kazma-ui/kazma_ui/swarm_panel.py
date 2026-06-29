"""Swarm Panel, backed by the shared SwarmEngine registry."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

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
    get_swarm_engine = None  # type: ignore[assignment,misc]
    set_swarm_engine = None  # type: ignore[assignment,misc]

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

_SUPPORTED_MODELS: list[str] = []
_SUPPORTED_PROVIDERS: list[str] = []


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


def _create_empty_engine() -> Any:
    """Create an empty shared engine when swarm core is available."""
    if not _has_swarm_core():
        return None
    store = TaskStore() if TaskStore is not None else None
    return SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)


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
        engine = _create_empty_engine()
    set_swarm_engine(engine)
    return engine


def _reset_swarm_state() -> None:
    """Reset the shared engine for tests."""
    if not _has_swarm_core():
        return
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
    return any(worker._running for worker in engine._workers.values())


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


def _serialize_worker(worker: Any) -> dict[str, Any]:
    """Convert a worker object into a response-friendly dict."""
    capabilities = None
    if hasattr(worker, "capabilities") and worker.capabilities is not None:
        if hasattr(worker.capabilities, "to_dict"):
            capabilities = worker.capabilities.to_dict()
        else:
            capabilities = {"role": getattr(worker.capabilities, "role", "")}
    return {
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


def _worker_views(engine: Any) -> list[dict[str, Any]]:
    """Return serialized worker views for templates and APIs."""
    if engine is None:
        return []
    return [_serialize_worker(worker) for worker in engine._workers.values()]


def _build_worker_config(payload: dict[str, Any]) -> Any:
    """Create a WorkerConfig from a UI payload."""
    if WorkerConfig is None:
        return None
    worker_type = {"in-process": "in_process", "telegram": "telegram_bot"}.get(
        payload.get("type", "in-process"),
        "in_process",
    )
    return WorkerConfig(
        name=(payload.get("name") or "").strip(),
        type=worker_type,
        model=payload.get("model", "deepseek-chat"),
        provider=payload.get("provider", "deepseek"),
        role=payload.get("role", ""),
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


def create_swarm_router(
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> APIRouter:
    """Create the Swarm Panel router backed by the shared engine."""
    router = APIRouter(tags=["swarm"])
    _registry = None

    # Wire the SSE streaming endpoint.
    try:
        from kazma_ui.swarm_sse import SSEEventBus, create_sse_router

        _sse_bus = SSEEventBus()
        _sse_router = create_sse_router(event_bus=_sse_bus)
        router.include_router(_sse_router)
        logger.info("[Swarm] SSE streaming router mounted at /api/swarm/tasks/{id}/stream")
    except ImportError:
        _sse_bus = None  # type: ignore[assignment]
        logger.debug("[Swarm] swarm_sse module not available, SSE streaming disabled")

    def _current_engine() -> Any:
        engine = _resolve_engine(swarm_manager)
        # Wire the SSE event bus to the engine on first use.
        if engine is not None and _sse_bus is not None:
            try:
                from kazma_ui.swarm_sse import wire_engine_events
                wire_engine_events(engine, _sse_bus)
            except Exception:
                logger.debug("[Swarm] failed to wire SSE events to engine", exc_info=True)
        return engine

    def _registry_options() -> dict[str, Any] | None:
        nonlocal _registry
        if _registry is None:
            try:
                from kazma_core.model_registry import get_model_registry
                _registry = get_model_registry()
            except RuntimeError:
                return None
        try:
            return _registry.list_unified_options()
        except Exception:
            logger.warning("[Swarm] Failed to read unified model options", exc_info=True)
            return None

    @router.get("/swarm", response_class=HTMLResponse)
    async def swarm_page(request: Request) -> HTMLResponse:
        """Render the Swarm panel."""
        engine = _current_engine()
        workers = _worker_views(engine)
        started = _swarm_started(engine)
        template_path = _TEMPLATE_DIR / "swarm.html"
        if template_path.exists():
            return cast(
                HTMLResponse,
                templates.TemplateResponse(
                    request,
                    "swarm.html",
                    {
                        "workers": workers,
                        "worker_count": len(workers),
                        "started": started,
                        "has_swarm_core": _has_swarm_core(),
                        "config": None,
                        "active_page": "swarm",
                    },
                ),
            )

        return HTMLResponse(_fallback_html(_has_swarm_core(), workers))

    @router.get("/api/swarm/status")
    async def swarm_status() -> dict[str, Any]:
        """Return current worker status."""
        engine = _current_engine()
        workers = _worker_views(engine)
        result: dict[str, Any] = {
            "workers": workers,
            "count": len(workers),
            "started": _swarm_started(engine),
            "has_swarm_core": _has_swarm_core(),
            "setup_instructions": None,
        }
        if not _has_swarm_core():
            result["setup_instructions"] = (
                "kazma_core.swarm is not installed. "
                "Install with: pip install kazma-core[swarm] "
                "or add kazma_core.swarm to your project."
            )
        return result

    @router.post("/api/swarm/dispatch")
    async def swarm_dispatch(payload: dict[str, Any]) -> JSONResponse:
        """Dispatch a task to one or more workers."""
        worker_names = payload.get("workers", [])
        task = str(payload.get("task", "")).strip()
        context = payload.get("context", "")
        task_type = _coerce_task_type(payload, worker_names)
        timeout = _coerce_timeout(payload)

        if task_type == getattr(TaskType, "PIPELINE", None) and not worker_names:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Pipeline requires at least one worker.",
                },
                status_code=400,
            )
        if task_type == getattr(TaskType, "CONSULT", None) and not worker_names:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Consult requires at least one worker.",
                },
                status_code=400,
            )
        if task_type == getattr(TaskType, "CONDITIONAL", None) and not worker_names:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Conditional requires at least one worker.",
                },
                status_code=400,
            )
        if task_type == getattr(TaskType, "CONDITIONAL", None) and not payload.get("metadata", {}).get("routes"):
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Conditional requires a 'routes' mapping in task metadata.",
                },
                status_code=400,
            )
        if not worker_names:
            return JSONResponse(
                {"status": "error", "message": "No workers specified"},
                status_code=400,
            )
        if not task:
            return JSONResponse(
                {"status": "error", "message": "No task specified"},
                status_code=400,
            )

        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse(
                {
                    "status": "warning",
                    "message": (
                        "kazma_core.swarm is not installed — task recorded locally "
                        "but no workers will execute it. Install: pip install kazma-core[swarm]"
                    ),
                    "dispatched": [],
                    "missing": list(worker_names),
                    "results": [],
                }
            )

        dispatched = [name for name in worker_names if engine.get_worker(name) is not None]
        missing = [name for name in worker_names if name not in dispatched]
        results: list[dict[str, Any]] = []
        task_result: Any | None = None
        task_metadata = dict(payload.get("metadata", {})) if isinstance(
            payload.get("metadata"), dict
        ) else {}
        if "max_concurrent" in payload:
            task_metadata["max_concurrent"] = payload.get("max_concurrent")
        if "max_retries" in payload:
            task_metadata["max_retries"] = payload.get("max_retries")

        manager_engine = getattr(swarm_manager, "engine", None)
        if not isinstance(manager_engine, SwarmEngine):
            manager_engine = None
        uses_external_dispatch = (
            swarm_manager is not None
            and manager_engine is None
            and task_type
            not in {
                getattr(TaskType, "PIPELINE", None),
                getattr(TaskType, "FAN_OUT", None),
                getattr(TaskType, "CONSULT", None),
                getattr(TaskType, "CONDITIONAL", None),
            }
        )
        if uses_external_dispatch:
            for name in dispatched:
                worker = engine.get_worker(name)
                if worker is not None:
                    worker.mark_dispatched(task)
                try:
                    result = await swarm_manager.dispatch(name, task, context)
                except Exception as exc:
                    logger.exception("[Swarm] delegated dispatch failed for worker '%s'", name)
                    result = {
                        "worker": name,
                        "task_id": "",
                        "status": "error",
                        "output": "",
                        "error": str(exc)[:500],
                    }
                if worker is not None:
                    worker.mark_completed(result.get("status", "error"))
                results.append(result)
        elif dispatched:
            swarm_task = SwarmTask(
                prompt=task,
                context=context,
                workers=dispatched,
                type=task_type,
                timeout=timeout,
                aggregation=str(payload.get("aggregation") or "collect"),
                metadata=task_metadata,
            )
            if task_type == TaskType.BROADCAST:
                task_result = await engine.broadcast(swarm_task)
            else:
                task_result = await engine.dispatch(swarm_task)
            results = [item.to_dict() for item in task_result.worker_results]

        # Include checkpoint info for HITL paused pipelines.
        checkpoint_info = None
        if (
            task_result is not None
            and task_result.status == "paused"
            and task_result.metadata
        ):
            checkpoint_info = task_result.metadata.get("checkpoint")

        return JSONResponse(
            {
                "status": "ok",
                "message": f"Task dispatched to {len(dispatched)} worker(s)",
                "dispatched": dispatched,
                "missing": missing,
                "task": task,
                "results": results,
                "task_id": None if task_result is None else task_result.task_id,
                "result_status": None if task_result is None else task_result.status,
                "aggregated_output": None if task_result is None else task_result.aggregated_output,
                "individual_opinions": (
                    []
                    if task_result is None
                    else [item.to_dict() for item in task_result.individual_opinions]
                ),
                "synthesized_output": (
                    None if task_result is None else task_result.synthesized_output
                ),
                "error": None if task_result is None else task_result.error,
                "metadata": None if task_result is None else task_result.metadata,
                "checkpoint": checkpoint_info,
            }
        )

    @router.get("/api/swarm/tasks")
    async def swarm_tasks(
        task_type: str | None = Query(default=None, alias="type"),
        status: str | None = Query(default=None),
        worker: str | None = Query(default=None),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
    ) -> JSONResponse:
        """Return completed swarm tasks with pagination and filtering.

        Query parameters:
            type: Filter by task type (dispatch, consult, etc.)
            status: Filter by task status (completed, failed, etc.)
            worker: Filter to tasks involving this worker name
            page: 1-based page number (default: 1)
            pageSize: Items per page (default: 20, max: 100)
        """
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse({"tasks": [], "count": 0})

        # Use TaskStore for paginated queries when available.
        store = getattr(engine, "task_store", None)
        if store is not None:
            tasks, total = store.list_tasks(
                page=page,
                page_size=page_size,
                status=status,
                task_type=task_type,
                worker=worker,
                include_count=True,
            )
            return JSONResponse({
                "tasks": [task.to_dict() for task in tasks],
                "count": len(tasks),
                "total": total,
                "page": page,
                "pageSize": page_size,
            })

        # Fallback to in-memory history.
        tasks = [task.to_dict() for task in engine.list_tasks(task_type)]
        return JSONResponse({"tasks": tasks, "count": len(tasks)})

    @router.get("/api/swarm/tasks/{task_id}")
    async def swarm_task_detail(task_id: str) -> JSONResponse:
        """Return full detail for a single swarm task."""
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )

        # Try TaskStore first (survives restart), then in-memory history.
        store = getattr(engine, "task_store", None)
        task = None
        if store is not None:
            task = store.get_task(task_id)
        if task is None:
            task = engine.get_task(task_id)
        if task is None:
            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' not found"},
                status_code=404,
            )

        return JSONResponse({
            "task": task.to_dict(),
        })

    @router.get("/api/swarm/workers/{name}/metrics")
    async def swarm_worker_metrics(name: str) -> JSONResponse:
        """Return daily metrics for a specific worker."""
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse({"metrics": [], "worker": name})

        store = getattr(engine, "task_store", None)
        if store is None:
            return JSONResponse({"metrics": [], "worker": name})

        metrics = store.get_worker_metrics(name)
        return JSONResponse({"metrics": metrics, "worker": name})

    @router.get("/api/swarm/workers/metrics/all")
    async def swarm_all_worker_metrics() -> JSONResponse:
        """Return aggregated metrics for all workers."""
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse({"metrics": []})

        store = getattr(engine, "task_store", None)
        if store is None:
            return JSONResponse({"metrics": []})

        metrics = store.get_all_worker_metrics()
        return JSONResponse({"metrics": metrics})

    @router.post("/api/swarm/workers/spawn", status_code=201)
    async def swarm_spawn_worker(payload: dict[str, Any]) -> JSONResponse:
        """Dynamically spawn a worker at runtime.

        Creates an InProcessWorker with the given name, role, and
        capabilities.  The worker is immediately available in the
        registry and dispatchable by all orchestration patterns.
        Duplicate names are rejected with 409 Conflict.
        """
        name = (payload.get("name") or "").strip()
        if not name:
            return JSONResponse(
                {"status": "error", "message": "Worker name is required"},
                status_code=400,
            )

        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )

        if engine.get_worker(name) is not None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' already exists"},
                status_code=409,
            )

        role = (payload.get("role") or "").strip()
        capabilities_data = payload.get("capabilities") or {"role": role}
        model = payload.get("model", "")
        provider = payload.get("provider", "")
        worker_type = payload.get("worker_type", "in_process")

        try:
            worker = await engine.spawn_worker(
                name=name,
                role=role,
                capabilities=capabilities_data,
                model=model,
                provider=provider,
                worker_type=worker_type,
            )
        except ValueError as exc:
            return JSONResponse(
                {"status": "error", "message": str(exc)},
                status_code=409,
            )

        logger.info(
            "[Swarm] Worker spawned: %s (role=%s, model=%s/%s)",
            name, role, model, provider,
        )
        return JSONResponse(
            {"status": "ok", "worker": _serialize_worker(worker)},
            status_code=201,
        )

    @router.post("/api/swarm/workers", status_code=201)
    async def swarm_add_worker(payload: dict[str, Any]) -> JSONResponse:
        """Add a worker to the shared engine registry."""
        name = (payload.get("name") or "").strip()
        if not name:
            return JSONResponse(
                {"status": "error", "message": "Worker name is required"},
                status_code=400,
            )

        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse(
                {
                    "status": "error",
                    "message": "Swarm core is not available",
                },
                status_code=503,
            )

        if engine.get_worker(name) is not None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' already exists"},
                status_code=409,
            )

        worker_config = _build_worker_config(payload)
        worker = engine.add_worker(worker_config)
        setattr(worker, "bot_token", payload.get("bot_token"))
        setattr(worker, "endpoint", payload.get("endpoint"))
        setattr(worker, "api_key", payload.get("api_key"))
        _sync_external_manager_add(swarm_manager, worker_config, engine)

        logger.info("[Swarm] Worker added: %s (%s/%s)", name, worker.model, worker.provider)
        return JSONResponse({"status": "ok", "worker": _serialize_worker(worker)}, status_code=201)

    @router.delete("/api/swarm/workers/{name}")
    async def swarm_remove_worker(name: str) -> JSONResponse:
        """Remove a worker from the shared engine registry."""
        engine = _current_engine()
        if engine is None or engine.get_worker(name) is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )

        engine.remove_worker(name)
        _sync_external_manager_remove(swarm_manager, name, engine)
        logger.info("[Swarm] Worker removed: %s", name)
        return JSONResponse({"status": "ok", "message": f"Worker '{name}' removed"})

    @router.get("/api/swarm/workers/{name}/logs")
    async def swarm_worker_logs(name: str) -> JSONResponse:
        """Return log lines for a worker."""
        engine = _current_engine()
        worker = None if engine is None else engine.get_worker(name)
        if worker is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )

        logs = list(worker.logs)
        if not logs:
            logs = [
                f"[{worker.added_at}] Worker '{name}' registered "
                f"(model={worker.model or '?'}, provider={worker.provider or '?'})",
            ]
            if worker.last_task:
                logs.append(
                    f"[{worker.last_heartbeat or worker.added_at}] Last task: {worker.last_task}"
                )
            logs.append(f"Current status: {_worker_status(worker)}")

        return JSONResponse({"logs": logs, "count": len(logs)})

    @router.post("/api/swarm/start")
    async def swarm_start() -> JSONResponse:
        """Start all workers."""
        engine = _current_engine()
        workers = [] if engine is None else list(engine._workers.values())
        if not workers:
            return JSONResponse(
                {"status": "error", "message": "No workers registered — add workers first"},
                status_code=400,
            )
        if _swarm_started(engine):
            return JSONResponse({"status": "ok", "message": "Swarm already started"})

        await engine.start_all()
        logger.info("[Swarm] Started, %d workers online", len(workers))
        return JSONResponse(
            {
                "status": "ok",
                "message": f"Swarm started — {len(workers)} worker(s) online",
                "worker_count": len(workers),
            }
        )

    @router.post("/api/swarm/stop")
    async def swarm_stop() -> JSONResponse:
        """Stop all workers."""
        engine = _current_engine()
        workers = [] if engine is None else list(engine._workers.values())
        if not _swarm_started(engine):
            return JSONResponse({"status": "ok", "message": "Swarm already stopped"})

        await engine.stop_all()
        logger.info("[Swarm] Stopped, %d workers offline", len(workers))
        return JSONResponse(
            {
                "status": "ok",
                "message": f"Swarm stopped — {len(workers)} worker(s) offline",
                "worker_count": len(workers),
            }
        )

    @router.get("/api/swarm/models")
    async def swarm_models() -> dict[str, Any]:
        """Return supported models and providers."""
        options = _registry_options()
        if options is not None:
            return {
                "models": options.get("models", []),
                "providers": options.get("providers", []),
                "provider_entries": options.get("provider_entries", []),
                "provider_models": options.get("provider_models", {}),
                "profiles": options.get("profiles", []),
                "defaults": options.get("defaults", {}),
                "source": "registry",
            }
        return {
            "models": [],
            "providers": [],
            "provider_entries": [],
            "provider_models": {},
            "profiles": [],
            "defaults": {},
            "source": "unavailable",
        }

    @router.get("/api/swarm/circuit-breakers")
    async def swarm_circuit_breakers() -> JSONResponse:
        """Return circuit breaker status for all workers."""
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse({"breakers": {}, "count": 0})
        breakers = engine.get_all_circuit_breaker_status()
        return JSONResponse({"breakers": breakers, "count": len(breakers)})

    @router.get("/api/swarm/workers/{name}/circuit-breaker")
    async def swarm_worker_circuit_breaker(name: str) -> JSONResponse:
        """Return circuit breaker status for a single worker."""
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )
        if engine.get_worker(name) is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )
        breaker_status = engine.get_circuit_breaker_status(name)
        return JSONResponse({"worker": name, "circuit_breaker": breaker_status})

    @router.post("/api/swarm/workers/{name}/circuit-breaker/reset")
    async def swarm_reset_circuit_breaker(name: str) -> JSONResponse:
        """Manually reset a worker's circuit breaker to closed state."""
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )
        if engine.get_worker(name) is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )
        breaker = engine.reset_circuit_breaker(name)
        logger.info("[Swarm] Circuit breaker reset for worker '%s'", name)
        return JSONResponse({
            "status": "ok",
            "message": f"Circuit breaker reset for worker '{name}'",
            "worker": name,
            "circuit_breaker": breaker.to_dict(),
        })

    # ------------------------------------------------------------------
    # HITL Checkpoint endpoints
    # ------------------------------------------------------------------

    @router.post("/api/swarm/tasks/{task_id}/approve")
    async def swarm_approve_checkpoint(task_id: str) -> JSONResponse:
        """Approve an HITL checkpoint and resume the pipeline."""
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )

        checkpoint_info = engine.get_checkpoint_info(task_id)
        if checkpoint_info is None:
            # Check if the task exists but is not paused.
            task_obj = engine.get_task(task_id)
            if task_obj is not None and task_obj.status != "paused":
                return JSONResponse(
                    {
                        "status": "error",
                        "message": f"Task '{task_id}' is not paused (status: {task_obj.status.value})",
                    },
                    status_code=409,
                )
            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' not found"},
                status_code=404,
            )

        result = await engine.approve_checkpoint(task_id)
        if result is None:
            return JSONResponse(
                {"status": "error", "message": f"Failed to approve checkpoint for task '{task_id}'"},
                status_code=500,
            )

        return JSONResponse({
            "status": result.status,
            "message": "Checkpoint approved, pipeline resumed",
            "task_id": result.task_id,
            "worker_results": [item.to_dict() for item in result.worker_results],
            "aggregated_output": result.aggregated_output,
            "error": result.error,
            "metadata": result.metadata,
        })

    @router.post("/api/swarm/tasks/{task_id}/reject")
    async def swarm_reject_checkpoint(task_id: str) -> JSONResponse:
        """Reject an HITL checkpoint and abort the pipeline."""
        engine = _current_engine()
        if not _has_swarm_core() or engine is None:
            return JSONResponse(
                {"status": "error", "message": "Swarm core is not available"},
                status_code=503,
            )

        checkpoint_info = engine.get_checkpoint_info(task_id)
        if checkpoint_info is None:
            task_obj = engine.get_task(task_id)
            if task_obj is not None and task_obj.status != "paused":
                return JSONResponse(
                    {
                        "status": "error",
                        "message": f"Task '{task_id}' is not paused (status: {task_obj.status.value})",
                    },
                    status_code=409,
                )
            return JSONResponse(
                {"status": "error", "message": f"Task '{task_id}' not found"},
                status_code=404,
            )

        result = await engine.reject_checkpoint(task_id)
        if result is None:
            return JSONResponse(
                {"status": "error", "message": f"Failed to reject checkpoint for task '{task_id}'"},
                status_code=500,
            )

        return JSONResponse({
            "status": result.status,
            "message": "Checkpoint rejected, pipeline aborted",
            "task_id": result.task_id,
            "worker_results": [item.to_dict() for item in result.worker_results],
            "aggregated_output": result.aggregated_output,
            "error": result.error,
            "metadata": result.metadata,
        })

    return router


def _fallback_html(has_core: bool, workers: list[dict[str, Any]]) -> str:
    """Inline HTML fallback for /swarm when the template is unavailable."""
    setup_banner = ""
    if not has_core:
        setup_banner = """
        <div style="background:#fff3cd;border:1px solid #ffc107;padding:12px 20px;
                    border-radius:6px;margin-bottom:20px;font-family:sans-serif;">
          ⚠️ <strong>kazma_core.swarm is not installed.</strong>
          Workers can be registered, but they won't execute tasks.
          Install: <code>pip install kazma-core[swarm]</code>
        </div>"""

    worker_rows = ""
    for worker in sorted(workers, key=lambda item: item["name"]):
        color = {"online": "#28a745", "offline": "#dc3545", "busy": "#ffc107"}.get(
            worker["status"],
            "#6c757d",
        )
        worker_rows += f"""
        <tr>
          <td>{worker['name']}</td>
          <td>{worker.get('model', '?')}</td>
          <td>{worker.get('provider', '?')}</td>
          <td>{worker.get('type', 'in-process')}</td>
          <td><span style="color:{color};font-weight:bold;">● {worker['status']}</span></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Kazma — Swarm Panel</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117; color: #c9d1d9; padding: 24px; }}
    h1 {{ color: #58a6ff; margin-bottom: 8px; }}
    h2 {{ color: #8b949e; font-weight: 400; margin-bottom: 24px; }}
    .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 8px;
              padding: 20px; margin-bottom: 20px; }}
    .card h3 {{ color: #e6edf3; margin-bottom: 12px; }}
    label {{ display: block; margin: 8px 0 4px; color: #8b949e; font-size: 0.9em; }}
    input, select, textarea {{
      width: 100%; padding: 8px 12px; background: #0d1117; border: 1px solid #30363d;
      border-radius: 4px; color: #c9d1d9; font-size: 14px;
    }}
    button {{ padding: 8px 20px; border: none; border-radius: 4px; font-size: 14px;
              cursor: pointer; margin-right: 8px; margin-top: 8px; }}
    .btn-primary {{ background: #238636; color: #fff; }}
    .btn-danger {{ background: #da3633; color: #fff; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th {{ text-align: left; padding: 8px 12px; border-bottom: 1px solid #30363d; color: #8b949e; }}
    td {{ padding: 8px 12px; border-bottom: 1px solid #21262d; }}
    .row {{ display: flex; gap: 20px; flex-wrap: wrap; }}
    .col {{ flex: 1; min-width: 300px; }}
    .toast {{ position: fixed; top: 20px; right: 20px; padding: 12px 20px;
              border-radius: 6px; font-size: 14px; z-index: 1000; display: none; }}
    .toast-success {{ background: #238636; color: #fff; }}
    .toast-error {{ background: #da3633; color: #fff; }}
  </style>
</head>
<body>
  <h1>🐝 Kazma Swarm Panel</h1>
  <h2>Multi-worker AI agent orchestration</h2>

  {setup_banner}

  <div class="row">
    <div class="col">
      <div class="card">
        <h3>⚙️ Controls</h3>
        <button class="btn-primary" onclick="swarmAction('start')">▶ Start All</button>
        <button class="btn-danger" onclick="swarmAction('stop')">⏹ Stop All</button>
        <span id="swarm-status" style="margin-left:12px;color:#8b949e;"></span>
      </div>

      <div class="card">
        <h3>👷 Workers</h3>
        <table>
          <thead><tr>
            <th>Name</th><th>Model</th><th>Provider</th><th>Type</th><th>Status</th>
          </tr></thead>
          <tbody id="worker-table">{worker_rows or '<tr><td colspan="5" style="color:#8b949e;">No workers registered</td></tr>'}</tbody>
        </table>
      </div>
    </div>

    <div class="col">
      <div class="card">
        <h3>➕ Add Worker</h3>
        <label>Name</label>
        <input id="add-name" placeholder="worker-1">
        <label>Model</label>
        <input id="add-model" placeholder="gpt-4o-mini">
        <label>Provider</label>
        <input id="add-provider" placeholder="openai">
        <label>Bot Token (optional)</label>
        <input id="add-token" placeholder="telegram-bot-token" type="password">
        <label>Type</label>
        <select id="add-type">
          <option value="in-process">In-Process</option>
          <option value="telegram">Telegram</option>
        </select>
        <button class="btn-primary" onclick="addWorker()">Add Worker</button>
        <button class="btn-danger" onclick="removeWorker()">Remove</button>
      </div>

      <div class="card">
        <h3>📤 Dispatch Task</h3>
        <label>Worker(s) — comma separated</label>
        <input id="dispatch-workers" placeholder="worker-1, worker-2">
        <label>Task</label>
        <textarea id="dispatch-task" rows="3" placeholder="Describe the task..."></textarea>
        <label>Context (optional)</label>
        <input id="dispatch-context" placeholder="Extra context...">
        <button class="btn-primary" onclick="dispatchTask()">Send Task</button>
      </div>
    </div>
  </div>

  <div id="toast" class="toast"></div>

  <script>
    function showToast(msg, ok) {{
      const t = document.getElementById('toast');
      t.textContent = msg;
      t.className = 'toast ' + (ok ? 'toast-success' : 'toast-error');
      t.style.display = 'block';
      setTimeout(() => t.style.display = 'none', 3000);
    }}

    async function swarmAction(action) {{
      try {{
        const r = await fetch('/api/swarm/' + action, {{ method: 'POST' }});
        const d = await r.json();
        showToast(d.message || d.status, r.ok);
        setTimeout(() => location.reload(), 500);
      }} catch (e) {{ showToast('Network error: ' + e, false); }}
    }}

    async function addWorker() {{
      const payload = {{
        name: document.getElementById('add-name').value,
        model: document.getElementById('add-model').value,
        provider: document.getElementById('add-provider').value,
        bot_token: document.getElementById('add-token').value || null,
        type: document.getElementById('add-type').value,
      }};
      try {{
        const r = await fetch('/api/swarm/workers', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        const d = await r.json();
        showToast(d.message || 'Worker added', r.ok);
        if (r.ok) setTimeout(() => location.reload(), 500);
      }} catch (e) {{ showToast('Network error: ' + e, false); }}
    }}

    async function removeWorker() {{
      const name = document.getElementById('add-name').value;
      if (!name) {{ showToast('Enter worker name to remove', false); return; }}
      try {{
        const r = await fetch('/api/swarm/workers/' + encodeURIComponent(name), {{ method: 'DELETE' }});
        const d = await r.json();
        showToast(d.message || 'Worker removed', r.ok);
        if (r.ok) setTimeout(() => location.reload(), 500);
      }} catch (e) {{ showToast('Network error: ' + e, false); }}
    }}

    async function dispatchTask() {{
      const workers = document.getElementById('dispatch-workers').value
        .split(',').map(s => s.trim()).filter(Boolean);
      const payload = {{
        workers: workers,
        task: document.getElementById('dispatch-task').value,
        context: document.getElementById('dispatch-context').value,
      }};
      try {{
        const r = await fetch('/api/swarm/dispatch', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        const d = await r.json();
        showToast(d.message || d.status, r.ok);
      }} catch (e) {{ showToast('Network error: ' + e, false); }}
    }}
  </script>
</body>
</html>"""
