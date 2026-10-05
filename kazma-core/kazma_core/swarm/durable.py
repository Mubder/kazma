"""Durable swarm execution — Temporal when configured, in-process otherwise.

The swarm *planner* (DAG, HITL, breakers, phonebook) stays in Kazma.
This module wraps ``SwarmEngine._dispatch_inner`` with a durable workflow
identity and result. An entire agent activity is not replay-safe: a lost
acknowledgement is held for inspection, never retried locally or as a new
activity. Step-level recovery requires idempotency in the effecting tools.

Default: in-process asyncio (one trusted operator).
Opt-in: ``KAZMA_TEMPORAL_HOST`` (or ``TEMPORAL_ADDRESS``) +
the ``durable`` extra (``pip install -e ".[durable]"`` in the Kazma
folder). Kill-switch ``KAZMA_TEMPORAL=0``.
Required (no fallback): ``KAZMA_TEMPORAL_REQUIRED=1``.
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import Any

from kazma_core.install_hint import extra_install_hint

logger = logging.getLogger(__name__)

__all__ = [
    "durable_enabled",
    "durable_host",
    "in_durable_activity",
    "run_activity_payload",
    "run_via_durable",
    "start_temporal_worker",
    "TASK_QUEUE",
]

TASK_QUEUE = "kazma-swarm"
_in_durable_activity: ContextVar[bool] = ContextVar(
    "kazma_durable_activity", default=False
)
_worker_task: Any = None


def durable_host() -> str:
    return (
        os.environ.get("KAZMA_TEMPORAL_HOST")
        or os.environ.get("TEMPORAL_ADDRESS")
        or ""
    ).strip()


def durable_enabled() -> bool:
    raw = (os.environ.get("KAZMA_TEMPORAL") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if not durable_host():
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    # Host set ⇒ on (unless kill-switched above)
    return True


def durable_required() -> bool:
    return (os.environ.get("KAZMA_TEMPORAL_REQUIRED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def in_durable_activity() -> bool:
    return bool(_in_durable_activity.get())


def _sdk_available() -> bool:
    try:
        import temporalio  # noqa: F401

        return True
    except ImportError:
        return False


async def run_via_durable(
    engine: Any,
    task: Any,
    started: float,
    task_span: Any,
) -> Any:
    """Start a Temporal workflow for this swarm task and wait for the result.

    Fallback is safe only before submission. Once submitted, a lost reply is
    an unknown outcome, never permission to execute host effects again.
    """
    from kazma_core.swarm.task import TaskResult

    if in_durable_activity():
        return await engine._dispatch_inner(task, started, task_span)
    if not durable_enabled():
        if durable_required():
            return TaskResult(task_id=task.id, status="failed", error="Required Temporal is disabled or has no host.")
        return await engine._dispatch_inner(task, started, task_span)

    if not _sdk_available():
        msg = (
            "Temporal host is set but temporalio is not installed "
            f"(the durable extra: {extra_install_hint('durable')})."
        )
        if durable_required():
            return TaskResult(task_id=task.id, status="failed", error=msg)
        logger.warning("[durable] %s Falling back to in-process.", msg)
        return await engine._dispatch_inner(task, started, task_span)

    submission_attempted = False
    workflow_id = f"kazma-swarm-{task.id}"
    try:
        from temporalio.client import Client
        from temporalio.common import WorkflowIDReusePolicy

        from kazma_core.swarm.durable_temporal import KazmaSwarmTask

        namespace = (
            os.environ.get("KAZMA_TEMPORAL_NAMESPACE") or "default"
        ).strip() or "default"
        client = await Client.connect(durable_host(), namespace=namespace)
        payload = {
            "task_id": task.id,
            "task": task.to_dict(),
            "started": float(started),
        }
        submission_attempted = True
        try:
            handle = await client.start_workflow(
                KazmaSwarmTask.run,
                payload,
                id=workflow_id,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                task_queue=(os.environ.get("KAZMA_TEMPORAL_QUEUE") or TASK_QUEUE).strip() or TASK_QUEUE,
            )
        except Exception:
            # Both an already-started ID and a lost Start acknowledgement
            # are recovered by reading the original workflow, never a new run.
            handle = client.get_workflow_handle(workflow_id)
        raw = await handle.result()
        if isinstance(raw, dict):
            result = TaskResult.from_dict(raw)
            result.metadata = dict(result.metadata or {})
            result.metadata["durable"] = "temporal"
            return result
        return raw
    except Exception as exc:
        if submission_attempted:
            logger.error("[durable] Workflow %s outcome unknown; local replay refused", workflow_id, exc_info=True)
            return TaskResult(
                task_id=task.id,
                status="failed",
                error=f"Temporal outcome unknown for {workflow_id}. Inspect that workflow before retrying; local replay was refused.",
                metadata={"durable": "temporal", "workflow_id": workflow_id, "execution_outcome": "unknown"},
            )
        if durable_required():
            logger.error("[durable] Temporal failed (required): %s", exc)
            return TaskResult(
                task_id=task.id,
                status="failed",
                error=f"Temporal durable dispatch failed: {exc}",
            )
        logger.warning(
            "[durable] Temporal failed (%s) — in-process fallback", exc
        )
        return await engine._dispatch_inner(task, started, task_span)


async def run_activity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Temporal activity body — runs Kazma ``_dispatch_inner`` once."""
    from kazma_core.swarm.engine import get_swarm_engine
    from kazma_core.swarm.task import SwarmTask, TaskResult

    token = _in_durable_activity.set(True)
    try:
        engine = get_swarm_engine()
        if engine is None:
            return TaskResult(
                task_id=str(payload.get("task_id") or ""),
                status="failed",
                error="No SwarmEngine in this process (Temporal worker must run inside Kazma)",
            ).to_dict()
        task_id = str(payload.get("task_id") or "")
        task = engine._active_tasks.get(task_id)
        if task is None:
            raw_task = payload.get("task") or {}
            task = SwarmTask.from_dict(raw_task)
            engine._active_tasks[task.id] = task
        started = float(payload.get("started") or 0.0)
        result = await engine._dispatch_inner(task, started, None)
        data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
        return data
    finally:
        _in_durable_activity.reset(token)


async def start_temporal_worker() -> None:
    """Boot a same-process Temporal worker. Fail-open — never blocks Kazma boot."""
    global _worker_task
    if not durable_enabled():
        return
    if not _sdk_available():
        logger.info(
            "[durable] Temporal host set but temporalio missing — "
            "in-process swarm only (the durable extra: %s)",
            extra_install_hint("durable"),
        )
        return
    try:
        import asyncio

        from temporalio.client import Client
        from temporalio.worker import Worker

        from kazma_core.swarm.durable_temporal import (
            KazmaSwarmTask,
            kazma_swarm_dispatch,
        )

        namespace = (
            os.environ.get("KAZMA_TEMPORAL_NAMESPACE") or "default"
        ).strip() or "default"
        queue = (
            os.environ.get("KAZMA_TEMPORAL_QUEUE") or TASK_QUEUE
        ).strip() or TASK_QUEUE
        client = await Client.connect(durable_host(), namespace=namespace)
        worker = Worker(
            client,
            task_queue=queue,
            workflows=[KazmaSwarmTask],
            activities=[kazma_swarm_dispatch],
        )
        _worker_task = asyncio.create_task(worker.run(), name="kazma-temporal-worker")
        logger.info("[durable] Temporal worker started queue=%s ns=%s", queue, namespace)
    except Exception:
        logger.warning("[durable] Temporal worker start failed", exc_info=True)
