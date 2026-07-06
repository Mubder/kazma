"""Swarm Panel, backed by the shared SwarmEngine registry."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

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
    return [_serialize_worker(worker, engine) for worker in engine._workers.values()]


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
        router = self.general_router
        _current_engine = self._current_engine
        _config_store = self._config_store
        _registry_options = self._registry_options
        templates = self.templates

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

        def _workflow_to_mermaid(workflow: Any) -> str:
            """Generate a Mermaid.js diagram string from a DAGWorkflow."""
            lines = ["graph TD"]
            # Style definitions for luxury, enterprise feel
            lines.append("    classDef agent fill:#1a1b26,stroke:#7aa2f7,stroke-width:2px,color:#c0caf5;")
            lines.append("    classDef router fill:#1e1e2e,stroke:#f5c2e7,stroke-width:2px,color:#cdd6f4;")
            lines.append("    classDef default fill:#15161e,stroke:#414868,stroke-width:1px,color:#a9b1d6;")

            # Add nodes
            for node_id, node in workflow.nodes.items():
                node_type = getattr(node, "type", "dispatch")
                prompt = getattr(node, "prompt_template", "")
                prompt_preview = (prompt[:30] + "...") if len(prompt) > 30 else prompt
                label = f'"{node_id}<br/><small style=\'color:#565f89;\'>{node_type}</small>"'
                lines.append(f"    {node_id}[{label}]")
                
                # Apply style classes
                if "router" in node_type.lower() or "dispatch" in node_type.lower() or "decision" in node_type.lower():
                    lines.append(f"    class {node_id} router;")
                else:
                    lines.append(f"    class {node_id} agent;")

            # Add edges with conditions
            for edge in workflow.edges:
                src = edge.from_node
                tgt = edge.to_node
                cond = getattr(edge, "condition", "")
                if cond:
                    cond_escaped = cond.replace('"', "'")
                    lines.append(f'    {src} -->|"{cond_escaped}"| {tgt}')
                else:
                    lines.append(f"    {src} --> {tgt}")

            return "\n".join(lines)

        @router.post("/api/swarm/workflows/validate")
        async def validate_workflow(request: Request) -> JSONResponse:
            """Validate and parse a YAML/JSON DAG workflow definition.

            Accepts:
                { "workflow_definition": "..." } (string can be JSON or YAML)
                Or raw JSON matching DAGWorkflow.
            """
            from pydantic import ValidationError
            import yaml
            
            try:
                from kazma_core.swarm.dag_schema import DAGWorkflow
            except ImportError:
                return JSONResponse(
                    content={"valid": False, "error": "DAGWorkflow schema is not available in kazma_core"},
                    status_code=400,
                )

            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    content={"valid": False, "error": "Invalid request body; must be valid JSON"},
                    status_code=400,
                )

            definition_str = body.get("workflow_definition")
            if not definition_str:
                # Fallback to the whole body if it's already a dict representation
                try:
                    workflow = DAGWorkflow(**body)
                    return JSONResponse(
                        content={
                            "valid": True,
                            "workflow": workflow.model_dump(),
                            "nodes": [n.model_dump() for n in workflow.nodes.values()],
                            "edges": [e.model_dump() for e in workflow.edges],
                            "mermaid": _workflow_to_mermaid(workflow),
                        }
                    )
                except ValidationError as err:
                    return JSONResponse(
                        content={"valid": False, "error": str(err)},
                        status_code=200,
                    )
                except Exception as exc:
                    return JSONResponse(
                        content={"valid": False, "error": f"Validation error: {exc}"},
                        status_code=200,
                    )

            # Try parsing definition_str as JSON or YAML
            try:
                parsed_data = yaml.safe_load(definition_str)
                if not isinstance(parsed_data, dict):
                    return JSONResponse(
                        content={"valid": False, "error": "Parsed workflow must be a top-level JSON/YAML object/dictionary"},
                        status_code=200,
                    )
                
                workflow = DAGWorkflow(**parsed_data)
                return JSONResponse(
                    content={
                        "valid": True,
                        "workflow": workflow.model_dump(),
                        "nodes": [n.model_dump() for n in workflow.nodes.values()],
                        "edges": [e.model_dump() for e in workflow.edges],
                        "mermaid": _workflow_to_mermaid(workflow),
                    }
                )
            except yaml.YAMLError as yerr:
                return JSONResponse(
                    content={"valid": False, "error": f"YAML/JSON Syntax Error: {yerr}"},
                    status_code=200,
                )
            except ValidationError as err:
                return JSONResponse(
                    content={"valid": False, "error": str(err)},
                    status_code=200,
                )
            except Exception as exc:
                return JSONResponse(
                    content={"valid": False, "error": f"Validation error: {exc}"},
                    status_code=200,
                )

        @router.get("/api/swarm/output-target")
        async def get_output_target() -> JSONResponse:
            """Return the current swarm output-routing target.

            Shape: ``{"platform": "telegram", "chat_id": -100…, "enabled": bool}``
            or an empty config when unset.
            """
            cs = _config_store()
            if cs is None:
                return JSONResponse(
                    {"status": "error", "message": "Config store unavailable"},
                    status_code=503,
                )
            target = cs.get("swarm.output_target", None)
            if not isinstance(target, dict):
                target = {"platform": "telegram", "chat_id": None, "enabled": False, "bot_token": ""}
            target.setdefault("platform", "telegram")
            target.setdefault("chat_id", None)
            target.setdefault("enabled", False)
            target.setdefault("bot_token", "")
            # Mask bot_token in GET response to avoid exposing it in the API
            if target.get("bot_token"):
                target = {**target, "bot_token": "***"}
            # Serialize chat_id as a string so large Telegram supergroup IDs
            # (>2^53) survive JSON.parse on the client without precision loss.
            if target["chat_id"] is not None:
                target = {**target, "chat_id": str(target["chat_id"])}
            return JSONResponse({"output_target": target})

        @router.put("/api/swarm/output-target")
        async def set_output_target(payload: dict[str, Any]) -> JSONResponse:
            """Set or clear the swarm output-routing target.

            Expected body:
                {"platform": "telegram", "chat_id": 1804015016, "enabled": true,
                 "bot_token": "8668...:AAF..."}   — dedicated swarm bot mode
                {"platform": "telegram", "chat_id": -100123, "enabled": true}  — gateway mode
                {"clear": true}  — remove the target entirely
            """
            cs = _config_store()
            if cs is None:
                return JSONResponse(
                    {"status": "error", "message": "Config store unavailable"},
                    status_code=503,
                )

            # Clear branch
            if payload.get("clear"):
                cs.delete("swarm.output_target")
                return JSONResponse({
                    "status": "ok",
                    "output_target": {
                        "platform": "telegram", "chat_id": None,
                        "enabled": False, "bot_token": "",
                    },
                })

            chat_id = payload.get("chat_id")
            if chat_id in (None, ""):
                return JSONResponse(
                    {"status": "error", "message": "chat_id is required"},
                    status_code=400,
                )
            try:
                chat_id = int(chat_id)
            except (TypeError, ValueError):
                return JSONResponse(
                    {"status": "error", "message": "chat_id must be an integer"},
                    status_code=400,
                )

            bot_token = str(payload.get("bot_token", "") or "").strip()

            target = {
                "platform": str(payload.get("platform") or "telegram"),
                "chat_id": chat_id,
                "enabled": bool(payload.get("enabled", True)),
                "bot_token": bot_token,
            }
            cs.set("swarm.output_target", target, category="swarm")
            logger.info("[Swarm] Output target set: chat_id=%s, bot_token=%s", chat_id, "set" if bot_token else "none")
            # Mask bot_token in response to avoid exposing it
            response_target = {**target, "bot_token": "***" if bot_token else ""}
            return JSONResponse({"status": "ok", "output_target": response_target})

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

        @router.get("/api/swarm/templates")
        async def list_templates() -> JSONResponse:
            """List all worker templates for auto-scaling."""
            engine = _current_engine()
            if engine is None:
                return JSONResponse({"templates": [], "instances": []})
            scaler = engine.get_autoscaler()
            if scaler is None:
                return JSONResponse({"templates": [], "instances": []})
            return JSONResponse({
                "templates": [t.to_dict() for t in scaler.list_templates()],
                "instances": scaler.get_instance_info(),
            })

        @router.post("/api/swarm/templates")
        async def add_template(payload: dict[str, Any]) -> JSONResponse:
            """Register a new worker template."""
            engine = _current_engine()
            if engine is None:
                return JSONResponse({"status": "error", "message": "Swarm not available"}, status_code=503)
            scaler = engine.get_autoscaler()
            if scaler is None:
                return JSONResponse({"status": "error", "message": "AutoScaler not available"}, status_code=503)
            from kazma_core.swarm.autoscaler import WorkerTemplate
            template = WorkerTemplate.from_dict(payload)
            if not template.name:
                return JSONResponse({"status": "error", "message": "Template name required"}, status_code=400)
            scaler.register_template(template)
            scaler.save_templates()
            return JSONResponse({"status": "ok", "template": template.to_dict()})

        @router.delete("/api/swarm/templates/{name}")
        async def delete_template(name: str) -> JSONResponse:
            """Remove a template and reap its instances."""
            engine = _current_engine()
            if engine is None:
                return JSONResponse({"status": "error", "message": "Swarm not available"}, status_code=503)
            scaler = engine.get_autoscaler()
            if scaler is None:
                return JSONResponse({"status": "error", "message": "AutoScaler not available"}, status_code=503)
            scaler.unregister_template(name)
            scaler.save_templates()
            return JSONResponse({"status": "ok"})

        @router.post("/api/swarm/autoscaler/reap")
        async def reap_idle_workers() -> JSONResponse:
            """Trigger idle worker reaping."""
            engine = _current_engine()
            if engine is None:
                return JSONResponse({"status": "error", "message": "Swarm not available"}, status_code=503)
            scaler = engine.get_autoscaler()
            if scaler is None:
                return JSONResponse({"status": "error", "message": "AutoScaler not available"}, status_code=503)
            reaped = scaler.reap_idle()
            return JSONResponse({"status": "ok", "reaped": reaped})

    def _build_tasks_routes(self) -> None:
        router = self.tasks_router
        _current_engine = self._current_engine
        swarm_manager = self.swarm_manager

        @router.post("/api/swarm/dispatch")
        async def swarm_dispatch(payload: dict[str, Any]) -> JSONResponse:
            """Dispatch a task to one or more workers.

            When ``background`` is True in the payload, the task is dispatched
            asynchronously and the response returns immediately with just the
            ``task_id``. The client should subscribe to SSE or poll
            ``GET /api/swarm/tasks/active`` for live status. This enables the
            Active Tasks tab to show running tasks.

            When ``background`` is False (default, backward-compatible), the
            endpoint blocks until the task completes and returns full results.
            """
            worker_names = payload.get("workers", [])
            task = str(payload.get("task", "")).strip()
            context = payload.get("context", "")
            task_type = _coerce_task_type(payload, worker_names)
            timeout = _coerce_timeout(payload)
            background = bool(payload.get("background", False))

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
                return JSONResponse(
                    {
                        "status": "ok",
                        "message": f"Task delegated to SwarmManager for {len(dispatched)} worker(s)",
                        "dispatched": dispatched,
                        "missing": missing,
                        "task": task,
                        "results": results,
                        "task_id": None,
                        "result_status": "success" if any(r.get("status") == "success" for r in results) else "error",
                    }
                )

            swarm_task = SwarmTask(
                prompt=task,
                context=context,
                workers=dispatched,
                type=task_type,
                timeout=timeout,
                aggregation=str(payload.get("aggregation") or "collect"),
                fallback_chain=list(payload.get("fallback_chain", [])),
                metadata=task_metadata,
            )
            if task_type == TaskType.BROADCAST:
                if background:
                    _handle = asyncio.create_task(engine.broadcast(swarm_task))
                    engine._task_handles[swarm_task.id] = _handle
                    # Clean up handle on completion to prevent memory leak
                    _handle.add_done_callback(
                        lambda h, tid=swarm_task.id: engine._task_handles.pop(tid, None)
                    )
                    return JSONResponse({
                        "status": "ok",
                        "message": f"Task dispatched (background) to {len(dispatched)} worker(s)",
                        "task_id": swarm_task.id,
                        "result_status": "running",
                        "dispatched": dispatched,
                    })
                task_result = await engine.broadcast(swarm_task)
            else:
                if background:
                    _handle = asyncio.create_task(engine.dispatch(swarm_task))
                    engine._task_handles[swarm_task.id] = _handle
                    # Clean up handle on completion to prevent memory leak
                    _handle.add_done_callback(
                        lambda h, tid=swarm_task.id: engine._task_handles.pop(tid, None)
                    )
                    return JSONResponse({
                        "status": "ok",
                        "message": f"Task dispatched (background) to {len(dispatched)} worker(s)",
                        "task_id": swarm_task.id,
                        "result_status": "running",
                        "dispatched": dispatched,
                    })
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

        @router.get("/api/swarm/tasks/active")
        async def swarm_active_tasks() -> JSONResponse:
            """Return all in-flight (running or paused) tasks.

            This endpoint powers the Active Tasks tab. Tasks are tracked in
            the engine's ``_active_tasks`` dict from dispatch start until
            finalization (completion, failure, or timeout). Paused tasks
            (HITL checkpoints) remain visible until approved or rejected.
            """
            engine = _current_engine()
            if not _has_swarm_core() or engine is None:
                return JSONResponse({"tasks": [], "count": 0})
            active = engine.list_active_tasks()
            flat = [_flatten_swarm_task(t) for t in active]
            return JSONResponse({"tasks": flat, "count": len(flat)})

        @router.get("/api/swarm/tasks")
        async def swarm_tasks(
            task_type: str | None = Query(default=None, alias="type"),
            status: str | None = Query(default=None),
            worker: str | None = Query(default=None),
            q: str | None = Query(default=None),
            page: int = Query(default=1, ge=1),
            page_size: int = Query(default=20, alias="pageSize", ge=1, le=100),
        ) -> JSONResponse:
            """Return completed swarm tasks with pagination, filtering, and search.

            Query parameters:
                type: Filter by task type (dispatch, consult, etc.)
                status: Filter by task status (completed, failed, etc.)
                worker: Filter to tasks involving this worker name
                q: Server-side full-text search on prompt text
                page: 1-based page number (default: 1)
                pageSize: Items per page (default: 20, max: 100)
            """
            engine = _current_engine()
            if not _has_swarm_core() or engine is None:
                return JSONResponse({"tasks": [], "count": 0})

            # Use TaskStore for paginated queries when available.
            store = getattr(engine, "task_store", None) or getattr(engine, "_task_store", None)
            if store is not None:
                tasks, total = store.list_tasks(
                    page=page,
                    page_size=page_size,
                    status=status,
                    task_type=task_type,
                    worker=worker,
                    include_count=True,
                )
                # Server-side search on prompt text
                if q:
                    q_lower = q.lower()
                    tasks = [t for t in tasks if q_lower in (t.prompt or "").lower()]
                    total = len(tasks)
                return JSONResponse({
                    "tasks": [_flatten_swarm_task(task) for task in tasks],
                    "count": len(tasks),
                    "total": total,
                    "page": page,
                    "pageSize": page_size,
                })

            # Fallback to in-memory history.
            tasks = [task.to_dict() for task in engine.list_tasks(task_type)]
            return JSONResponse({"tasks": tasks, "count": len(tasks)})

        @router.get("/api/swarm/tasks/export", response_model=None)
        async def swarm_tasks_export(
            format: str = Query(default="json"),
            status: str | None = Query(default=None),
        ):
            """Export task history as JSON or CSV."""
            engine = _current_engine()
            if not _has_swarm_core() or engine is None:
                return JSONResponse({"tasks": []})

            store = getattr(engine, "task_store", None) or getattr(engine, "_task_store", None)
            if store is not None:
                tasks, _ = store.list_tasks(page=1, page_size=1000, status=status, include_count=True)
            else:
                tasks = []

            flat = [_flatten_swarm_task(task) for task in tasks]

            if format.lower() == "csv":
                import csv
                import io

                output = io.StringIO()
                if flat:
                    writer = csv.DictWriter(output, fieldnames=flat[0].keys())
                    writer.writeheader()
                    writer.writerows(flat)
                return Response(
                    content=output.getvalue(),
                    media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=swarm_tasks.csv"},
                )

            return JSONResponse({"tasks": flat, "count": len(flat)})

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
                "task": _flatten_swarm_task(task),
            })

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

        @router.post("/api/swarm/tasks/{task_id}/cancel")
        async def swarm_cancel_task(task_id: str) -> JSONResponse:
            """Cancel a running task."""
            engine = _current_engine()
            if engine is None:
                return JSONResponse(
                    {"status": "error", "message": "Swarm engine not available"},
                    status_code=503,
                )
            # Check the task is active
            if task_id not in engine._active_tasks:
                return JSONResponse(
                    {"status": "error", "message": f"Task '{task_id}' is not active (already completed or not found)"},
                    status_code=404,
                )
            cancelled = await engine.cancel_task(task_id)
            if cancelled:
                return JSONResponse(
                    {"status": "ok", "message": f"Task '{task_id}' cancelled", "task_id": task_id}
                )
            return JSONResponse(
                {"status": "error", "message": f"Failed to cancel task '{task_id}'"},
                status_code=500,
            )

        @router.post("/api/swarm/tasks/{task_id}/retry")
        async def swarm_retry_task(task_id: str) -> JSONResponse:
            """Retry a failed/timeout/cancelled task by re-dispatching."""
            engine = _current_engine()
            if engine is None:
                return JSONResponse(
                    {"status": "error", "message": "Swarm engine not available"},
                    status_code=503,
                )
            new_task = await engine.retry_task(task_id)
            if new_task is None:
                return JSONResponse(
                    {"status": "error", "message": f"Task '{task_id}' not found"},
                    status_code=404,
                )
            # Dispatch in the background (non-blocking)
            handle = asyncio.create_task(engine.dispatch(new_task))
            engine._task_handles[new_task.id] = handle
            handle.add_done_callback(
                lambda h, tid=new_task.id: engine._task_handles.pop(tid, None)
            )
            return JSONResponse(
                {
                    "status": "ok",
                    "message": f"Retrying task '{task_id}' as '{new_task.id}'",
                    "old_task_id": task_id,
                    "new_task_id": new_task.id,
                }
            )

    def _build_workers_routes(self) -> None:
        router = self.workers_router
        _current_engine = self._current_engine
        swarm_manager = self.swarm_manager

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

            # Sync to persistent WorkerRegistry
            try:
                from kazma_core.swarm.registry import WorkerEntry, get_worker_registry
                reg_caps = payload.get("capabilities") or {}
                registry = get_worker_registry()
                registry.register(WorkerEntry(
                    name=name,
                    expertise=(reg_caps.get("expertise") if reg_caps else None) or [payload.get("role", "leaf")],
                    roles=[payload.get("role", "leaf")] if payload.get("role") else ["leaf"],
                    model=worker.model,
                    provider=worker.provider,
                    worker_type=worker_config.type if hasattr(worker_config, "type") else "in_process",
                    system_prompt=payload.get("system_prompt", ""),
                ))
                logger.info("[Swarm] WorkerRegistry synced: %s", name)
            except Exception as exc:
                logger.warning("[Swarm] WorkerRegistry sync failed: %s", exc)

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
            engine = _current_engine()
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

        @router.post("/api/swarm/workers/{name}/start")
        async def worker_start(name: str) -> JSONResponse:
            """Start a single worker by name."""
            engine = _current_engine()
            if engine is None or name not in engine._workers:
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
            engine = _current_engine()
            if engine is None or name not in engine._workers:
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
    from html import escape as _html_escape
    for worker in sorted(workers, key=lambda item: item["name"]):
        color = {"online": "#28a745", "offline": "#dc3545", "busy": "#ffc107"}.get(
            worker["status"],
            "#6c757d",
        )
        worker_rows += f"""
        <tr>
          <td>{_html_escape(str(worker['name']))}</td>
          <td>{_html_escape(str(worker.get('model', '?')))}</td>
          <td>{_html_escape(str(worker.get('provider', '?')))}</td>
          <td>{_html_escape(str(worker.get('type', 'in-process')))}</td>
          <td><span style="color:{color};font-weight:bold;">● {_html_escape(str(worker['status']))}</span></td>
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
