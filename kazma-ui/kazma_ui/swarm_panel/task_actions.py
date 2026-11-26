"""The Swarm page's Cancel and Retry, as functions any surface can call.

The page's routes and the run supervisor's actions (``kazma_core.runs``) do
the SAME thing through these two functions: one control per action, so a
button on a chat card cannot skip a check the page makes. Retry dispatches
through the page's own background wrapper (output routing, handle
registration), which lives inside the routes' registration; that wrapper is
handed over with :func:`set_dispatcher` when the routes are registered.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

__all__ = [
    "RESEARCH_KIND", "caller_may_act_on", "cancel_task", "retry_task", "set_dispatcher",
    "set_engine_resolver", "task_refusal",
]

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


#: A research task's ``metadata.kind`` (the Research page lists by it).
RESEARCH_KIND = "research"


def caller_may_act_on(task: Any, *, answering: bool) -> bool:
    """Whether the work this call is bound to may change or answer *task*
    (a ``SwarmTask`` or its dict, of the caller's space): the one rule for
    both route families, by the task's kind (Spaces S1-17 follow-up).

    A research task (``metadata.kind`` ``research``) follows decision N32,
    as research sessions do: its author or the space's owner changes it --
    archive, restore, delete, cancel, retry -- on the Research page and the
    Swarm page alike. Any other task: *answering* its approval (a pipeline
    checkpoint's Approve or Reject) follows the rule of who answers a card
    (N4: the person's approvers when the admin set some); changing it
    (Cancel, Retry, archive, delete) is the person it acts for
    (``task_is_callers``, the S2-05 review: approvers answer a person's
    cards, they do not take the person's tasks from them). Always True while
    Spaces is not enforced. Raises ``IdentityUnavailable``. Blocking (the
    identity store)."""
    from kazma_core.spaces.shared_work import may_change
    from kazma_core.swarm.single_operator import space_of_task, task_answered_by_caller, task_is_callers

    meta = (task.get("metadata") if isinstance(task, dict) else getattr(task, "metadata", None)) or {}
    if isinstance(meta, dict) and meta.get("kind") == RESEARCH_KIND:
        return may_change(space_of_task(task), meta.get("asker"))
    return task_answered_by_caller(task) if answering else task_is_callers(task)


def task_refusal(engine: Any, task_id: str, *, answering: bool = False) -> dict[str, Any] | None:
    """Why the work this call is bound to may not act on *task_id*, or None:
    the one check every surface that acts on a task makes -- the page's
    Cancel, Retry, Approve and Reject, the run supervisor's actions -- so a
    retry cannot take a member's task into the caller's space through the
    admission stamp (Spaces S1-16 follow-up).

    While Spaces is not enforced: None. Once it is, ``code`` ``no_space``
    (no space bound: work that is no one's acts on no one's task),
    ``not_found`` (unknown, or another space's: one answer for both),
    ``not_yours`` (``caller_may_act_on``: a research task's author or the
    space's owner, N32; any other task's asker, or with *answering* -- its
    approval -- whoever answers the asker's cards, N4) or
    ``identity_unavailable``. Blocking (the task store, the identity store).
    """
    from kazma_core.spaces.identity import IdentityUnavailable
    from kazma_core.spaces.scope import NoSpaceBound, spaces_enforced
    from kazma_core.swarm.single_operator import task_of_this_space

    if not spaces_enforced():
        return None
    store = getattr(engine, "task_store", None) or getattr(engine, "_task_store", None)
    task = (engine.get_active_task(task_id) if hasattr(engine, "get_active_task") else None) \
        or engine.get_task(task_id) or (store.get_task(task_id) if store is not None else None)
    try:
        if task is None or not task_of_this_space(task):
            return {"ok": False, "code": "not_found", "message": f"Task '{task_id}' not found"}
        if not caller_may_act_on(task, answering=answering):
            research = (getattr(task, "metadata", None) or {}).get("kind") == RESEARCH_KIND
            return {"ok": False, "code": "not_yours",
                    "message": ("Only the person who started this research, or the space's owner, can change it."
                                if research else "Only the person who asked for this task can answer it.")}
    except NoSpaceBound:
        return {"ok": False, "code": "no_space", "message": "This work is not tied to a space."}
    except IdentityUnavailable:
        logger.warning("[swarm] who may act on task %s cannot be read; refused", task_id)
        return {"ok": False, "code": "identity_unavailable",
                "message": "Who this task is for cannot be read right now; try again."}
    return None


async def cancel_task(task_id: str) -> dict[str, Any]:
    """Cancel a running or paused task. ``{"ok", "code", "message"}``."""
    engine = _engine()
    if engine is None:
        return {"ok": False, "code": "engine_unavailable", "message": "Swarm engine not available"}
    refused = await asyncio.to_thread(task_refusal, engine, task_id)
    if refused is not None:
        return refused
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
    refused = await asyncio.to_thread(task_refusal, engine, task_id)
    if refused is not None:
        return refused
    from kazma_core.swarm.task_control import RetryNeedsAcknowledgement

    try:
        new_task = await engine.retry_task(task_id, acknowledge_unknown=acknowledge_unknown)
    except RetryNeedsAcknowledgement as exc:
        from kazma_core.errors import validation_error

        return {"ok": False, "code": "retry_outcome_unknown", "message": validation_error(exc, max_chars=600),
                "tools": exc.tools}
    if new_task is None:
        return {"ok": False, "code": "not_found", "message": f"Task '{task_id}' not found"}
    await _dispatch(engine, new_task)
    return {"ok": True, "code": "retried", "message": f"Retrying task '{task_id}' as '{new_task.id}'",
            "old_task_id": task_id, "new_task_id": new_task.id}
