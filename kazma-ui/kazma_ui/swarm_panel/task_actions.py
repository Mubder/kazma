"""The Swarm page's Cancel and Retry, as functions any surface can call.

The page's routes and the run supervisor's actions (``kazma_core.runs``) do
the SAME thing through these two functions: one control per action, so a
button on a chat card cannot skip a check the page makes. Retry dispatches
through the page's own background wrapper (output routing, handle
registration), which lives inside the routes' registration; that wrapper is
handed over with :func:`set_dispatcher` when the routes are registered.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = ["cancel_task", "retry_task", "set_dispatcher", "set_engine_resolver"]

logger = logging.getLogger(__name__)

_dispatch: Callable[[Any, Any], Awaitable[None]] | None = None
_resolve_engine: Callable[[], Any] | None = None


def set_dispatcher(dispatch: Callable[[Any, Any], Awaitable[None]]) -> None:
    """The page's background dispatch: ``dispatch(engine, task)``."""
    global _dispatch
    _dispatch = dispatch


def set_engine_resolver(resolve: Callable[[], Any]) -> None:
    """How the page finds the live engine."""
    global _resolve_engine
    _resolve_engine = resolve


def _engine() -> Any:
    if _resolve_engine is not None:
        return _resolve_engine()
    from kazma_core.swarm.engine import get_swarm_engine

    return get_swarm_engine()


async def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a running or paused task. ``{"ok", "code", "message"}``."""
    engine = _engine()
    if engine is None:
        return {"ok": False, "code": "engine_unavailable", "message": "Swarm engine not available"}
    # A task in flight, or a pipeline paused before a restart (restored as a
    # paused in-flight task, SW-E09): the panel offers Cancel on both.
    active = (engine.get_active_task(task_id) if hasattr(engine, "get_active_task")
              else getattr(engine, "_active_tasks", {}).get(task_id))
    if active is None:
        task_obj = engine.get_task(task_id)
        if task_obj is None or task_obj.status != "paused":
            return {"ok": False, "code": "not_active",
                    "message": f"Task '{task_id}' is not active (already completed or not found)"}
    if await engine.cancel_task(task_id):
        return {"ok": True, "code": "cancelled", "message": f"Task '{task_id}' cancelled"}
    return {"ok": False, "code": "cancel_failed", "message": f"Failed to cancel task '{task_id}'"}


async def retry_task(task_id: str, *, acknowledge_unknown: bool = False) -> dict[str, Any]:
    """Retry a failed, timed-out or cancelled task as a fresh dispatch.

    ``code`` ``retry_outcome_unknown`` (with ``tools``) when the original's
    effects are unknown and *acknowledge_unknown* was not given (SW-E08).
    """
    engine = _engine()
    if engine is None:
        return {"ok": False, "code": "engine_unavailable", "message": "Swarm engine not available"}
    if _dispatch is None:
        return {"ok": False, "code": "dispatch_unavailable",
                "message": "The Swarm page is not mounted; retry from it once it is."}
    from kazma_core.swarm.task_control import RetryNeedsAcknowledgement

    try:
        new_task = await engine.retry_task(task_id, acknowledge_unknown=acknowledge_unknown)
    except RetryNeedsAcknowledgement as exc:
        return {"ok": False, "code": "retry_outcome_unknown", "message": str(exc), "tools": exc.tools}
    if new_task is None:
        return {"ok": False, "code": "not_found", "message": f"Task '{task_id}' not found"}
    await _dispatch(engine, new_task)
    return {"ok": True, "code": "retried", "message": f"Retrying task '{task_id}' as '{new_task.id}'",
            "old_task_id": task_id, "new_task_id": new_task.id}
