"""Public service facade for Swarm UI.

Provides clean, stable API for the swarm panel and other UI components.
Eliminates direct private attribute access (._workers, ._task_handles, etc.)
and hasattr fallbacks on the engine.

All components should use get_swarm_service() instead of reaching into
SwarmEngine internals.
"""

from __future__ import annotations

from typing import Any, Optional

logger = __import__("logging").getLogger(__name__)


class SwarmService:
    """Facade over SwarmEngine and related components."""

    def __init__(self) -> None:
        self._engine: Any = None

    def _get_engine(self) -> Any:
        """Get the current engine, preferring public APIs."""
        if self._engine is not None:
            return self._engine
        try:
            from kazma_core.swarm import get_swarm_engine

            eng = get_swarm_engine()
            if eng is not None:
                self._engine = eng
            return self._engine
        except Exception as exc:
            logger.debug("Failed to get swarm engine via public API: %s", exc)
            return None

    def list_workers(self) -> list[dict[str, Any]]:
        """Return list of workers via public ``list_workers()`` only."""
        engine = self._get_engine()
        if engine is None or not hasattr(engine, "list_workers"):
            return []
        try:
            workers = engine.list_workers()
            return [self._serialize_worker(w) for w in workers]
        except Exception as exc:
            logger.debug("list_workers failed: %s", exc)
            return []

    def get_worker(self, name: str) -> Optional[dict[str, Any]]:
        engine = self._get_engine()
        if engine is None or not hasattr(engine, "get_worker"):
            return None
        try:
            w = engine.get_worker(name)
            return self._serialize_worker(w) if w else None
        except Exception as exc:
            logger.debug("get_worker failed: %s", exc)
            return None

    def register_task_handle(self, task_id: str, handle: Any) -> None:
        engine = self._get_engine()
        if engine is None or not hasattr(engine, "register_task_handle"):
            return
        try:
            engine.register_task_handle(task_id, handle)
        except Exception as exc:
            logger.debug("register_task_handle failed: %s", exc)

    def unregister_task_handle(self, task_id: str) -> None:
        engine = self._get_engine()
        if engine is None or not hasattr(engine, "unregister_task_handle"):
            return
        try:
            engine.unregister_task_handle(task_id)
        except Exception as exc:
            logger.debug("unregister_task_handle failed: %s", exc)

    def get_task_handle(self, task_id: str) -> Any | None:
        engine = self._get_engine()
        if engine is None or not hasattr(engine, "get_task_handle"):
            return None
        try:
            return engine.get_task_handle(task_id)
        except Exception as exc:
            logger.debug("get_task_handle failed: %s", exc)
            return None

    def get_active_task(self, task_id: str) -> Any | None:
        engine = self._get_engine()
        if engine is None or not hasattr(engine, "get_active_task"):
            return None
        try:
            return engine.get_active_task(task_id)
        except Exception as exc:
            logger.debug("get_active_task failed: %s", exc)
            return None

    def set_sse_bus(self, bus: Any) -> None:
        engine = self._get_engine()
        if engine is None:
            return
        if hasattr(engine, "set_sse_bus"):
            try:
                engine.set_sse_bus(bus)
            except Exception as exc:
                logger.debug("set_sse_bus failed: %s", exc)

    def has_swarm_core(self) -> bool:
        """Return whether kazma_core.swarm is importable."""
        try:
            from kazma_core.swarm import SwarmConfig, SwarmEngine, SwarmTask, TaskType, WorkerConfig, get_swarm_engine, set_swarm_engine
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
        except ImportError:
            return False

    def is_started(self) -> bool:
        """Return whether any worker is running."""
        engine = self._get_engine()
        if engine is None:
            return False
        workers = self.list_workers()
        return any(worker.get("status") in ("online", "busy") for worker in workers)

    def resolve_engine(self, swarm_manager: Any = None) -> Any:
        """Resolve the engine used by the router at request time."""
        if not self.has_swarm_core():
            return None

        from kazma_core.swarm import SwarmConfig, SwarmEngine, TaskStore, get_swarm_engine, set_swarm_engine

        engine: Any | None = None
        if isinstance(swarm_manager, SwarmEngine):
            engine = swarm_manager
        else:
            engine = getattr(swarm_manager, "engine", None)
            if not isinstance(engine, SwarmEngine):
                engine = None
            if engine is None:
                engine = get_swarm_engine()

        if engine is None:
            # Create an empty shared engine
            store = getattr(self, "_shared_task_store", None) or (TaskStore() if TaskStore is not None else None)
            engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]), task_store=store)
        
        if engine is not None:
            self._shared_task_store = getattr(engine, "task_store", None) or getattr(self, "_shared_task_store", None)
            set_swarm_engine(engine)
            self._engine = engine
        return engine

    def get_config_store(self) -> Any:
        """Return the ConfigStore."""
        try:
            from kazma_core.config_store import get_config_store
            return get_config_store()
        except Exception:
            logger.debug("[Swarm] ConfigStore unavailable", exc_info=True)
            return None

    def get_output_target(self) -> dict[str, Any]:
        """Return the current swarm output-routing target config."""
        cs = self.get_config_store()
        if cs is None:
            return {"platform": "telegram", "chat_id": None, "enabled": False, "bot_token": ""}
        target = cs.get("swarm.output_target", None)
        if not isinstance(target, dict):
            target = {"platform": "telegram", "chat_id": None, "enabled": False, "bot_token": ""}
        target.setdefault("platform", "telegram")
        target.setdefault("chat_id", None)
        target.setdefault("enabled", False)
        target.setdefault("bot_token", "")
        return target

    def set_output_target(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Set the swarm output-routing target config."""
        cs = self.get_config_store()
        if cs is None:
            raise RuntimeError("Config store unavailable")
        
        if payload.get("clear"):
            cs.delete("swarm.output_target")
            return {"platform": "telegram", "chat_id": None, "enabled": False, "bot_token": ""}
        
        chat_id = payload.get("chat_id")
        if chat_id in (None, ""):
            raise ValueError("chat_id is required")
        try:
            chat_id = int(chat_id)
        except (TypeError, ValueError):
            raise ValueError("chat_id must be an integer")

        bot_token = str(payload.get("bot_token", "") or "").strip()
        target = {
            "platform": str(payload.get("platform") or "telegram"),
            "chat_id": chat_id,
            "enabled": bool(payload.get("enabled", True)),
            "bot_token": bot_token,
        }
        cs.set("swarm.output_target", target, category="swarm")
        return target

    def get_autoscaler(self) -> Any:
        """Fetch the current engine's AutoScaler."""
        engine = self._get_engine()
        if engine is None:
            return None
        if hasattr(engine, "get_autoscaler"):
            return engine.get_autoscaler()
        return getattr(engine, "_autoscaler", None)

    def get_circuit_breaker_status(self, name: str) -> dict[str, Any]:
        """Return circuit breaker status for a worker."""
        engine = self._get_engine()
        if engine is not None and hasattr(engine, "get_circuit_breaker_status"):
            try:
                return engine.get_circuit_breaker_status(name)
            except Exception as exc:
                logger.debug("Circuit breaker status failed for %s: %s", name, exc)
        return {"state": "closed", "consecutive_failures": 0}

    def _serialize_worker(self, worker: Any) -> dict[str, Any]:
        """Minimal serialization, matching previous behavior."""
        if worker is None:
            return {}
        if hasattr(worker, "to_dict"):
            try:
                return worker.to_dict()
            except Exception as exc:
                logger.debug("worker.to_dict failed: %s", exc)
        name = getattr(worker, "name", str(worker))
        status = "offline"
        if getattr(worker, "_running", False):
            status = "busy" if getattr(worker, "busy", False) else "online"
        
        model = getattr(getattr(worker, "config", None), "model", None) or getattr(worker, "model", "?")
        role = getattr(getattr(worker, "config", None), "role", None) or getattr(worker, "role", None)
        provider = getattr(getattr(worker, "config", None), "provider", None) or getattr(worker, "provider", "?")
        worker_type = getattr(getattr(worker, "config", None), "type", None) or getattr(worker, "worker_type", "in_process")

        result = {
            "name": name,
            "status": status,
            "model": model,
            "provider": provider,
            "type": worker_type,
            "role": role,
            "bot_token": "***" if getattr(worker, "bot_token", None) else None,
            "added_at": getattr(worker, "added_at", None),
            "last_task": getattr(worker, "last_task", None),
            "last_heartbeat": getattr(worker, "last_heartbeat", None),
            "logs": list(getattr(worker, "logs", [])),
        }
        
        capabilities = getattr(worker, "capabilities", None)
        if capabilities is not None:
            if hasattr(capabilities, "to_dict"):
                result["capabilities"] = capabilities.to_dict()
            else:
                result["capabilities"] = {"role": getattr(capabilities, "role", "")}

        result["circuit_breaker"] = self.get_circuit_breaker_status(name)
        return result


_service_instance: SwarmService | None = None


def get_swarm_service() -> SwarmService:
    """Return the process-wide SwarmService facade singleton."""
    global _service_instance
    if _service_instance is None:
        _service_instance = SwarmService()
    return _service_instance


def reset_swarm_service() -> None:
    """Test helper to reset the facade."""
    global _service_instance
    _service_instance = None

