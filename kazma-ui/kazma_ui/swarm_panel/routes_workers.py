"""Worker routes for the swarm panel.

Extracted from the original god module for single-responsibility.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from kazma_ui.services import get_swarm_service

logger = logging.getLogger(__name__)

__all__ = ["register_workers_routes"]


def _serialize_worker(worker: Any) -> dict[str, Any]:
    """Delegate to service or minimal serialize."""
    svc = get_swarm_service()
    if hasattr(svc, "_serialize_worker"):
        return svc._serialize_worker(worker)
    # fallback
    if worker is None:
        return {}
    if hasattr(worker, "to_dict"):
        try:
            return worker.to_dict()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("worker.to_dict failed: %s", exc)
    name = getattr(worker, "name", str(worker))
    status = "offline"
    if getattr(worker, "_running", False):
        status = "busy" if getattr(worker, "busy", False) else "online"
    return {"name": name, "status": status, "model": getattr(worker, "model", "?")}


def _worker_status(worker: Any) -> str:
    if not getattr(worker, "_running", False):
        return "offline"
    if getattr(worker, "busy", False):
        return "busy"
    return "online"


def _has_swarm_core() -> bool:
    try:
        from kazma_core.swarm import get_swarm_engine
        return get_swarm_engine() is not None
    except Exception:
        return False


def _sync_external_manager_add(swarm_manager: Any, worker_config: Any, engine: Any) -> None:
    if swarm_manager is None:
        return
    try:
        if hasattr(swarm_manager, "add_worker"):
            swarm_manager.add_worker(worker_config)
    except Exception as exc:
        logger.debug("external manager add failed: %s", exc)


def _sync_external_manager_remove(swarm_manager: Any, name: str, engine: Any) -> None:
    if swarm_manager is None:
        return
    try:
        if hasattr(swarm_manager, "remove_worker"):
            swarm_manager.remove_worker(name)
    except Exception as exc:
        logger.debug("external manager remove failed: %s", exc)


def register_workers_routes(
    router: APIRouter,
    templates: Any,
    swarm_manager: Any = None,
    config_store: Any = None,
) -> None:
    """Register all worker-related routes on the given router."""

    @router.get("/api/swarm/workers/{name}/metrics")
    async def swarm_worker_metrics(name: str) -> JSONResponse:
        """Return daily metrics for a specific worker."""
        svc = get_swarm_service()
        engine = svc._get_engine() if hasattr(svc, "_get_engine") else None
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
        svc = get_swarm_service()
        engine = svc._get_engine() if hasattr(svc, "_get_engine") else None
        if not _has_swarm_core() or engine is None:
            return JSONResponse({"metrics": []})

        store = getattr(engine, "task_store", None)
        if store is None:
            return JSONResponse({"metrics": []})

        metrics = store.get_all_worker_metrics()
        return JSONResponse({"metrics": metrics})

    @router.post("/api/swarm/workers/spawn", status_code=201)
    async def swarm_spawn_worker(payload: dict[str, Any]) -> JSONResponse:
        """Dynamically spawn a worker at runtime."""
        name = (payload.get("name") or "").strip()
        if not name:
            return JSONResponse(
                {"status": "error", "message": "Worker name is required"},
                status_code=400,
            )

        svc = get_swarm_service()
        engine = svc._get_engine() if hasattr(svc, "_get_engine") else None
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

        # Sync to persistent WorkerRegistry
        try:
            from kazma_core.swarm.registry import WorkerEntry, get_worker_registry
            registry = get_worker_registry()
            registry.register(WorkerEntry(
                name=name,
                expertise=[role] if role else ["general"],
                roles=["leaf"],
                model=model,
                provider=provider,
                worker_type=worker_type,
                system_prompt=payload.get("system_prompt", ""),
            ))
            logger.info("[Swarm] WorkerRegistry synced (spawn): %s", name)
        except Exception as exc:
            logger.warning("[Swarm] WorkerRegistry sync failed (spawn): %s", exc)

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

        svc = get_swarm_service()
        engine = svc._get_engine() if hasattr(svc, "_get_engine") else None
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

        # Use service or build config
        worker_config = _build_worker_config(payload) if '_build_worker_config' in globals() else payload
        worker = engine.add_worker(worker_config) if hasattr(engine, 'add_worker') else None
        if worker:
            setattr(worker, "bot_token", payload.get("bot_token"))
            setattr(worker, "endpoint", payload.get("endpoint"))
            setattr(worker, "api_key", payload.get("api_key"))
        _sync_external_manager_add(swarm_manager, worker_config, engine)

        # Sync to persistent WorkerRegistry
        try:
            from kazma_core.swarm.registry import WorkerEntry, get_worker_registry
            reg_caps = payload.get("capabilities") or {}
            registry = get_worker_registry()
            registry.register(WorkerEntry(
                name=name,
                expertise=(reg_caps.get("expertise") if reg_caps else None) or [payload.get("role", "leaf")],
                roles=[payload.get("role", "leaf")] if payload.get("role") else ["leaf"],
                model=getattr(worker, 'model', '') if worker else '',
                provider=getattr(worker, 'provider', '') if worker else '',
                worker_type=getattr(worker_config, 'type', 'in_process') if hasattr(worker_config, 'type') else "in_process",
                system_prompt=payload.get("system_prompt", ""),
            ))
            logger.info("[Swarm] WorkerRegistry synced: %s", name)
        except Exception as exc:
            logger.warning("[Swarm] WorkerRegistry sync failed: %s", exc)

        logger.info("[Swarm] Worker added: %s (%s/%s)", name, getattr(worker, 'model', ''), getattr(worker, 'provider', ''))
        return JSONResponse({"status": "ok", "worker": _serialize_worker(worker)}, status_code=201)

    @router.delete("/api/swarm/workers/{name}")
    async def swarm_remove_worker(name: str) -> JSONResponse:
        """Remove a worker from the shared engine registry."""
        svc = get_swarm_service()
        engine = svc._get_engine() if hasattr(svc, "_get_engine") else None
        if engine is None or engine.get_worker(name) is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )

        if hasattr(engine, 'remove_worker'):
            engine.remove_worker(name)
        _sync_external_manager_remove(swarm_manager, name, engine)

        # Sync to persistent WorkerRegistry
        try:
            from kazma_core.swarm.registry import get_worker_registry
            registry = get_worker_registry()
            registry.delete(name)
            logger.info("[Swarm] WorkerRegistry removed: %s", name)
        except Exception as exc:
            logger.warning("[Swarm] WorkerRegistry sync failed: %s", exc)

        logger.info("[Swarm] Worker removed: %s", name)
        return JSONResponse({"status": "ok", "message": f"Worker '{name}' removed"})

    @router.put("/api/swarm/workers/{name}")
    async def swarm_update_worker(name: str, payload: dict[str, Any]) -> JSONResponse:
        """Update worker configuration (model, provider, expertise, etc.)."""
        svc = get_swarm_service()
        engine = svc._get_engine()
        if engine is None or engine.get_worker(name) is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )

        # Update in-engine worker
        worker = engine.get_worker(name)
        if "model" in payload:
            worker.model = payload["model"]
        if "provider" in payload:
            worker.provider = payload["provider"]
        if "role" in payload:
            worker.role = payload["role"]

        # Sync to persistent WorkerRegistry
        try:
            from kazma_core.swarm.registry import get_worker_registry
            registry = get_worker_registry()
            update_kwargs = {}
            if "model" in payload:
                update_kwargs["model"] = payload["model"]
            if "provider" in payload:
                update_kwargs["provider"] = payload["provider"]
            if "role" in payload:
                update_kwargs["roles"] = [payload["role"]]
            if "system_prompt" in payload:
                update_kwargs["system_prompt"] = payload["system_prompt"]
            if "expertise" in payload:
                update_kwargs["expertise"] = payload["expertise"]
            if update_kwargs:
                registry.update(name, **update_kwargs)
                logger.info("[Swarm] WorkerRegistry updated: %s", name)
        except Exception as exc:
            logger.warning("[Swarm] WorkerRegistry sync failed: %s", exc)

        logger.info("[Swarm] Worker updated: %s", name)
        return JSONResponse({"status": "ok", "worker": _serialize_worker(worker)})

    @router.get("/api/swarm/workers/{name}/logs")
    async def swarm_worker_logs(name: str) -> JSONResponse:
        """Return log lines for a worker."""
        svc = get_swarm_service()
        engine = svc._get_engine()
        worker = None if engine is None else engine.get_worker(name)
        if worker is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )

        logs = list(worker.logs)
        if not logs:
            logs = [
                f"[{getattr(worker, 'added_at', '')}] Worker '{name}' registered "
                f"(model={worker.model or '?'}, provider={worker.provider or '?'})",
            ]
            last_task = getattr(worker, 'last_task', None)
            if last_task:
                logs.append(
                    f"[{getattr(worker, 'last_heartbeat', None) or getattr(worker, 'added_at', '')}] Last task: {last_task}"
                )
            logs.append(f"Current status: {_worker_status(worker)}")

        return JSONResponse({"logs": logs, "count": len(logs)})

    @router.post("/api/swarm/start")
    async def swarm_start() -> JSONResponse:
        """Start all workers."""
        svc = get_swarm_service()
        engine = svc._get_engine()
        workers = [] if engine is None else engine.list_workers() if hasattr(engine, "list_workers") else list(getattr(engine, "_workers", {}).values())
        if not workers:
            return JSONResponse(
                {"status": "error", "message": "No workers registered — add workers first"},
                status_code=400,
            )
        if svc.is_started():
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
        svc = get_swarm_service()
        engine = svc._get_engine()
        workers = [] if engine is None else engine.list_workers() if hasattr(engine, "list_workers") else list(getattr(engine, "_workers", {}).values())
        if not svc.is_started():
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

    @router.post("/api/swarm/workers/{name}/start")
    async def worker_start(name: str) -> JSONResponse:
        """Start a single worker by name."""
        svc = get_swarm_service()
        engine = svc._get_engine()
        if engine is None or engine.get_worker(name) is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )
        ok = await engine.start_worker(name)
        if ok:
            return JSONResponse({"status": "ok", "message": f"Worker '{name}' started"})
        return JSONResponse(
            {"status": "error", "message": f"Failed to start worker '{name}'"},
            status_code=500,
        )

    @router.post("/api/swarm/workers/{name}/stop")
    async def worker_stop(name: str) -> JSONResponse:
        """Stop a single worker by name."""
        svc = get_swarm_service()
        engine = svc._get_engine()
        if engine is None or engine.get_worker(name) is None:
            return JSONResponse(
                {"status": "error", "message": f"Worker '{name}' not found"},
                status_code=404,
            )
        ok = await engine.stop_worker(name)
        if ok:
            return JSONResponse({"status": "ok", "message": f"Worker '{name}' stopped"})
        return JSONResponse(
            {"status": "error", "message": f"Failed to stop worker '{name}'"},
            status_code=500,
        )

    @router.get("/api/swarm/circuit-breakers")
    async def swarm_circuit_breakers() -> JSONResponse:
        """Return circuit breaker status for all workers."""
        svc = get_swarm_service()
        engine = svc._get_engine()
        if not svc.has_swarm_core() or engine is None:
            return JSONResponse({"breakers": {}, "count": 0})
        breakers = engine.get_all_circuit_breaker_status()
        return JSONResponse({"breakers": breakers, "count": len(breakers)})

    @router.get("/api/swarm/workers/{name}/circuit-breaker")
    async def swarm_worker_circuit_breaker(name: str) -> JSONResponse:
        """Return circuit breaker status for a single worker."""
        svc = get_swarm_service()
        engine = svc._get_engine()
        if not svc.has_swarm_core() or engine is None:
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
        svc = get_swarm_service()
        engine = svc._get_engine()
        if not svc.has_swarm_core() or engine is None:
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
            "circuit_breaker": breaker.to_dict() if hasattr(breaker, "to_dict") else str(breaker),
        })

def _build_worker_config(payload: dict[str, Any]) -> Any:
    """Create a WorkerConfig from a UI payload."""
    try:
        from kazma_core.swarm import WorkerConfig
    except ImportError:
        return payload

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
