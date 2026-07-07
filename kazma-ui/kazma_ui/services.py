"""Public service facade for Swarm UI.

Provides clean, stable API for the swarm panel and other UI components.
Eliminates direct private attribute access (._workers, ._task_handles, etc.)
and hasattr fallbacks on the engine.

All components should use get_swarm_service() instead of reaching into
SwarmEngine internals.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

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
        """Return list of workers using public API if available."""
        engine = self._get_engine()
        if engine is None:
            return []
        if hasattr(engine, "list_workers"):
            try:
                workers = engine.list_workers()
                return [
                    self._serialize_worker(w) for w in workers
                ]
            except Exception as exc:
                logger.debug("list_workers failed: %s", exc)
                return []
        # Fallback only if no public method (should not happen after refactor)
        try:
            raw = getattr(engine, "_workers", {}) or {}
            return [self._serialize_worker(w) for w in raw.values()]
        except Exception as exc:
            logger.debug("fallback list_workers failed: %s", exc)
            return []

    def get_worker(self, name: str) -> Optional[dict[str, Any]]:
        engine = self._get_engine()
        if engine is None:
            return None
        if hasattr(engine, "get_worker"):
            try:
                w = engine.get_worker(name)
                return self._serialize_worker(w) if w else None
            except Exception as exc:
                logger.debug("get_worker failed: %s", exc)
                return None
        raw = getattr(engine, "_workers", {}) or {}
        w = raw.get(name)
        return self._serialize_worker(w) if w else None

    def register_task_handle(self, task_id: str, handle: Any) -> None:
        engine = self._get_engine()
        if engine is None:
            return
        if hasattr(engine, "register_task_handle"):
            try:
                engine.register_task_handle(task_id, handle)
                return
            except Exception as exc:
                logger.debug("register_task_handle failed: %s", exc)
        # last resort
        try:
            if not hasattr(engine, "_task_handles"):
                engine._task_handles = {}
            engine._task_handles[task_id] = handle
        except Exception:
            pass

    def unregister_task_handle(self, task_id: str) -> None:
        engine = self._get_engine()
        if engine is None:
            return
        if hasattr(engine, "unregister_task_handle"):
            try:
                engine.unregister_task_handle(task_id)
                return
            except Exception as exc:
                logger.debug("unregister_task_handle failed: %s", exc)
        try:
            handles = getattr(engine, "_task_handles", {})
            handles.pop(task_id, None)
        except Exception:
            pass

    def get_task_handle(self, task_id: str) -> Any | None:
        engine = self._get_engine()
        if engine is None:
            return None
        if hasattr(engine, "get_task_handle"):
            try:
                return engine.get_task_handle(task_id)
            except Exception as exc:
                logger.debug("get_task_handle failed: %s", exc)
                return None
        try:
            return getattr(engine, "_task_handles", {}).get(task_id)
        except Exception:
            return None

    def get_active_task(self, task_id: str) -> Any | None:
        engine = self._get_engine()
        if engine is None:
            return None
        if hasattr(engine, "get_active_task"):
            try:
                return engine.get_active_task(task_id)
            except Exception as exc:
                logger.debug("get_active_task failed: %s", exc)
                return None
        try:
            return getattr(engine, "_active_tasks", {}).get(task_id)
        except Exception:
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

    def _serialize_worker(self, worker: Any) -> dict[str, Any]:
        """Minimal serialization, matching previous behavior."""
        if worker is None:
            return {}
        if hasattr(worker, "to_dict"):
            try:
                return worker.to_dict()
            except Exception:
                pass
        name = getattr(worker, "name", str(worker))
        status = "offline"
        if getattr(worker, "_running", False):
            status = "busy" if getattr(worker, "busy", False) else "online"
        model = getattr(getattr(worker, "config", None), "model", None) or getattr(worker, "model", "?")
        return {
            "name": name,
            "status": status,
            "model": model,
            "role": getattr(getattr(worker, "config", None), "role", None),
        }


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
