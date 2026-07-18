"""Broadcast dispatch path — extracted from SwarmEngine (S5)."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import TYPE_CHECKING

from kazma_core.swarm.blackboard import BlackboardStore
from kazma_core.swarm.dispatch_helpers import (
    aggregate_outputs,
    overall_status,
    resolve_max_concurrent,
)
from kazma_core.swarm.reliability import BoundedConcurrency
from datetime import UTC, datetime

from kazma_core.swarm.task import SwarmTask, TaskResult, TaskStatus, WorkerResult

__all__ = ["broadcast_task"]

if TYPE_CHECKING:
    from kazma_core.swarm.engine import SwarmEngine

logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def broadcast_task(engine: "SwarmEngine", task: SwarmTask) -> TaskResult:
    """Dispatch a task to all registered workers or the targeted subset."""
    started = perf_counter()
    task.started_at = task.started_at or _utc_now_iso()
    task.status = TaskStatus.RUNNING
    engine._active_tasks[task.id] = task

    task_span = engine._tracing_emitter.start_task_span(
        task_id=task.id,
        task_type=task.type.value,
        workers=list(task.workers),
    )

    blackboard = BlackboardStore()
    dispatch_context = engine._build_dispatch_context(task, blackboard=blackboard)

    target_names = list(task.workers) if task.workers else list(engine._workers.keys())
    if not target_names:
        engine._tracing_emitter.end_span(task_span, status="ok")
        return engine._finalize_task(
            task,
            worker_results=[],
            status="success",
            aggregated_output=None,
            duration_seconds=perf_counter() - started,
            metadata=await engine._build_result_metadata(blackboard),
        )

    max_concurrent = resolve_max_concurrent(
        task, max(1, int(getattr(engine.config, "max_concurrent", 5) or 5))
    )
    concurrency = BoundedConcurrency(max_concurrent=max_concurrent)

    async def _dispatch_with_concurrency(name: str) -> WorkerResult:
        async with concurrency:
            return await engine._dispatch_worker_by_name(
                name,
                task.prompt,
                dispatch_context,
                timeout=task.timeout,
                validation_schema=task.validation_schema,
            )

    worker_results = await asyncio.gather(
        *(_dispatch_with_concurrency(name) for name in target_names)
    )
    result_status = overall_status(worker_results)
    aggregated_output = aggregate_outputs(worker_results)
    error = None
    if result_status != "success":
        error_messages = [result.error for result in worker_results if result.error]
        error = "; ".join(error_messages) if error_messages else None

    span_status = "ok" if result_status in ("success", "partial") else "error"
    engine._tracing_emitter.end_span(task_span, status=span_status)

    return engine._finalize_task(
        task,
        worker_results=worker_results,
        status=result_status,
        aggregated_output=aggregated_output,
        error=error,
        duration_seconds=perf_counter() - started,
        metadata=await engine._build_result_metadata(blackboard),
    )
