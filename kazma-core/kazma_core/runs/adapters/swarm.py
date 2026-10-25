"""Swarm tasks as runs.

Source of truth: the swarm task store, and the engine's in-flight runs for
whether a RUNNING row still has an owner in this process (a row left RUNNING
by a process that died has none: the next start holds it unknown, SW-E16).
Progress is the engine's own events (``SseBridge.emit`` stamps
``runs.progress``). Actions are the Swarm page's Cancel and Retry
(``kazma_ui.swarm_panel.task_actions``); a task whose effects are unknown is
retried only as ``retry_checked`` (SW-E08).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kazma_core.runs import progress
from kazma_core.runs.adapters._common import epoch, origin_from, short_title
from kazma_core.runs.contract import ACTIVE_STATES, ActionResult, Run, RunState

__all__ = ["ADAPTER"]

logger = logging.getLogger(__name__)

_FINISHED = ("failed", "timeout", "cancelled", "completed")
_PER_STATUS = 40


def _engine() -> Any:
    """The live swarm engine, or None (the one place this adapter finds it)."""
    from kazma_core.swarm.engine import get_swarm_engine

    return get_swarm_engine()


class _SwarmAdapter:
    STATE_MAP: dict[str, str] = {
        "pending": RunState.QUEUED,
        "running": RunState.RUNNING,
        "paused": RunState.WAITING_HUMAN,
        "completed": RunState.DONE,
        "failed": RunState.FAILED,
        "timeout": RunState.FAILED,
        "cancelled": RunState.CANCELLED,
    }

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        engine = _engine()
        active = dict(getattr(engine, "_active_tasks", {}) or {}) if engine is not None else {}
        store = getattr(engine, "_task_store", None) if engine is not None else None
        rows = await asyncio.to_thread(self._stored, store, now - window_s) if store is not None else []
        tasks: dict[str, Any] = {t.id: t for t in rows}
        tasks.update(active)
        return [self._run(task, task.id in active, engine is not None) for task in tasks.values()]

    @staticmethod
    def _stored(store: Any, since: float) -> list[Any]:
        """RUNNING and PENDING rows, and the newest finished ones since *since*."""
        found: list[Any] = []
        for status in ("running", "pending", "paused", *_FINISHED):
            listed = store.list_tasks(status=status, page_size=_PER_STATUS)
            for task in listed:
                if status in _FINISHED and (epoch(task.completed_at) or 0.0) < since:
                    continue
                found.append(task)
        return found

    def _run(self, task: Any, owned: bool, engine_up: bool) -> Run:
        from kazma_core.swarm.task_control import _outcome_unknown

        native = str(getattr(task.status, "value", task.status))
        state = RunState(self.STATE_MAP.get(native, RunState.UNKNOWN))
        unknown, tools = _outcome_unknown(task)
        effects = "unknown" if unknown else ("known" if tools else "none")
        started = epoch(task.started_at) or epoch(task.created_at)
        stamp = progress.last(f"swarm_task:{task.id}")
        progress_at = max(started or 0.0, stamp[0]) if stamp else started
        if state in (RunState.RUNNING, RunState.WAITING_HUMAN):
            actions: tuple[str, ...] = ("cancel",) if owned or state == RunState.WAITING_HUMAN else ()
        elif state in (RunState.FAILED, RunState.CANCELLED):
            actions = ("retry_checked",) if unknown else ("retry",)
        else:
            actions = ()
        meta = task.metadata or {}
        detail = ""
        if task.result is not None and state == RunState.FAILED:
            detail = short_title(getattr(task.result, "error", "") or "", 200)
        return Run(
            run_id=f"swarm_task:{task.id}", kind="swarm_task", native_id=task.id,
            title=short_title(task.prompt), state=state, native_state=native,
            started_at=started, progress_at=progress_at,
            # The engine's last event, said as words, while the task runs.
            progress_note=(stamp[1] or "").replace("_", " ") if stamp and state in ACTIVE_STATES else "",
            owner_alive=(owned if state == RunState.RUNNING else None),
            ended_at=epoch(task.completed_at), effects=effects,
            version=f"{task.generation}:{native}", actions=actions,
            link="/swarm?task=" + task.id, detail=detail,
            origin=origin_from(meta.get("source_delivery_target"), meta.get("source_thread_id")),
            lineage="swarm_task:" + str(meta.get("retry_of") or task.id),
        )

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        from kazma_ui.swarm_panel import task_actions

        if action == "cancel":
            outcome = await task_actions.cancel_task(native_id)
        elif action in ("retry", "retry_checked"):
            outcome = await task_actions.retry_task(native_id, acknowledge_unknown=action == "retry_checked")
        else:
            return ActionResult(False, "not_offered", f"Swarm tasks do not take {action}.")
        return ActionResult(bool(outcome.get("ok")), str(outcome.get("code") or ""),
                            str(outcome.get("message") or ""))


ADAPTER = _SwarmAdapter()
