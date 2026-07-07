"""Swarm Panel, backed by the shared SwarmEngine registry.

TODO (audit): Small split started. See architecture docs.
Next steps: extract worker CRUD, task dispatch, metrics into submodules.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

logger = logging.getLogger(__name__)

from .services import get_swarm_service, reset_swarm_service  # new public facade

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
            import html as _html

            lines = ["graph TD"]
            # Style definitions for luxury, enterprise feel
            lines.append("    classDef agent fill:#1a1b26,stroke:#7aa2f7,stroke-width:2px,color:#c0caf5;")
            lines.append("    classDef router fill:#1e1e2e,stroke:#f5c2e7,stroke-width:2px,color:#cdd6f4;")
            lines.append("    classDef default fill:#15161e,stroke:#414868,stroke-width:1px,color:#a9b1d6;")

            def _safe_id(name: str) -> str:
                return "".join(c if c.isalnum() else "_" for c in name)

            def _mermaid_escape(s: str) -> str:
                """Escape HTML entities and Mermaid metacharacters for safe labels."""
                return _html.escape(s, quote=True).replace("&#x27;", "'").replace("]", "&#93;").replace("|", "&#124;")

            # Add nodes
            for node_id, node in workflow.nodes.items():
                node_type = getattr(node, "type", "dispatch")
                prompt = getattr(node, "prompt_template", "")
                prompt_preview = (prompt[:30] + "...") if len(prompt) > 30 else prompt
                label = f'"{_mermaid_escape(node_id)}<br/><small style=\'color:#565f89;\'>{_mermaid_escape(node_type)}</small>"'
                safe_node_id = _safe_id(node_id)
                lines.append(f"    {safe_node_id}[{label}]")
                
                # Apply style classes
                if "router" in node_type.lower() or "dispatch" in node_type.lower() or "decision" in node_type.lower():
                    lines.append(f"    class {safe_node_id} router;")
                else:
                    lines.append(f"    class {safe_node_id} agent;")

            # Add edges with conditions
            for edge in workflow.edges:
                src = edge.from_node
                tgt = edge.to_node
                safe_src = _safe_id(src)
                safe_tgt = _safe_id(tgt)
                cond = getattr(edge, "condition", "")
                if cond:
                    lines.append(f'    {safe_src} -->|"{_mermaid_escape(cond)}"| {safe_tgt}')
                else:
                    lines.append(f"    {safe_src} --> {safe_tgt}")

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
            except Exception as _e:
                logger.debug("[Swarm] invalid JSON in DAG validate: %s", _e)
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
                except ValidationError:
                    return JSONResponse(
                        content={"valid": False, "error": "Workflow validation failed. Check node and edge definitions."},
                        status_code=422,
                    )
                except Exception:
                    return JSONResponse(
                        content={"valid": False, "error": "Validation error. Check workflow definition syntax."},
                        status_code=422,
                    )

            # Try parsing definition_str as JSON or YAML
            try:
                parsed_data = yaml.safe_load(definition_str)
                if not isinstance(parsed_data, dict):
                    return JSONResponse(
                        content={"valid": False, "error": "Parsed workflow must be a top-level JSON/YAML object/dictionary"},
                        status_code=422,
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
            except yaml.YAMLError:
                return JSONResponse(
                    content={"valid": False, "error": "YAML/JSON syntax error. Check workflow definition format."},
                    status_code=422,
                )
            except ValidationError:
                return JSONResponse(
                    content={"valid": False, "error": "Workflow validation failed. Check node and edge definitions."},
                    status_code=422,
                )
            except Exception:
                return JSONResponse(
                    content={"valid": False, "error": "Validation error. Check workflow definition syntax."},
                    status_code=422,
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
