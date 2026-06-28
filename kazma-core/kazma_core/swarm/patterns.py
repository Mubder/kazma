"""Orchestration pattern implementations for the swarm engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.task import SwarmTask, WorkerResult

logger = logging.getLogger(__name__)

DispatchWorkerByName = Callable[
    [str, str, str | SwarmDispatchContext],
    Awaitable[WorkerResult],
]


class PipelineConfigurationError(ValueError):
    """Raised when a pipeline task is missing required configuration."""


@dataclass
class PatternExecution:
    """Normalized result returned by an orchestration pattern."""

    worker_results: list[WorkerResult]
    status: str
    aggregated_output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _last_success_output(worker_results: list[WorkerResult]) -> str | None:
    """Return the most recent successful worker output, if any."""
    for worker_result in reversed(worker_results):
        if worker_result.status == "success":
            return worker_result.output
    return None


def _failure_status(worker_results: list[WorkerResult], failed_result: WorkerResult) -> str:
    """Map a pipeline step failure into an overall pipeline status."""
    if failed_result.status == "timeout":
        return "timeout"
    if any(result.status == "success" for result in worker_results[:-1]):
        return "partial"
    return "failed"


async def _build_blackboard_metadata(blackboard: BlackboardStore) -> dict[str, Any]:
    """Return result metadata containing the shared blackboard snapshot."""
    snapshot = await blackboard.snapshot()
    return {
        "blackboard": snapshot,
        "blackboard_snapshot": snapshot,
    }


async def _build_pipeline_context_text(
    task: SwarmTask,
    previous_result: WorkerResult | None,
    blackboard: BlackboardStore,
) -> str:
    """Compose the text payload passed to the next pipeline worker."""
    if previous_result is None:
        return task.context

    sections = [section for section in [task.context.strip()] if section]
    sections.append(
        f"Previous worker ({previous_result.worker}) output:\n{previous_result.output}"
    )

    blackboard_snapshot = await blackboard.snapshot()
    if blackboard_snapshot:
        sections.append(
            "Shared blackboard snapshot:\n"
            + json.dumps(
                blackboard_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        )

    return "\n\n".join(sections)


async def execute_pipeline(
    task: SwarmTask,
    *,
    dispatch_worker_by_name: DispatchWorkerByName,
) -> PatternExecution:
    """Execute a task through workers sequentially, sharing one blackboard."""
    if not task.workers:
        raise PipelineConfigurationError("Pipeline requires at least one worker.")

    blackboard = BlackboardStore()
    worker_results: list[WorkerResult] = []
    previous_result: WorkerResult | None = None

    for step_index, worker_name in enumerate(task.workers, start=1):
        context_text = await _build_pipeline_context_text(
            task,
            previous_result,
            blackboard,
        )
        dispatch_context = SwarmDispatchContext(
            context_text,
            blackboard=blackboard,
            metadata={
                **task.metadata,
                "pipeline_step": step_index,
                "worker_name": worker_name,
            },
            task_id=task.id,
            task_type=task.type.value,
        )

        try:
            worker_result = await asyncio.wait_for(
                dispatch_worker_by_name(worker_name, task.prompt, dispatch_context),
                timeout=task.timeout,
            )
        except TimeoutError:
            logger.warning(
                "[SwarmPatterns] pipeline worker '%s' timed out after %.2fs",
                worker_name,
                task.timeout,
            )
            worker_result = WorkerResult(
                worker=worker_name,
                task_id=task.id,
                status="timeout",
                output="",
                error=f"Worker '{worker_name}' timed out after {task.timeout:g}s.",
            )

        if not worker_result.task_id:
            worker_result.task_id = task.id
        worker_results.append(worker_result)

        if worker_result.status != "success":
            return PatternExecution(
                worker_results=worker_results,
                status=_failure_status(worker_results, worker_result),
                aggregated_output=_last_success_output(worker_results),
                error=worker_result.error or f"Worker '{worker_name}' failed.",
                metadata=await _build_blackboard_metadata(blackboard),
            )

        await blackboard.set("last_worker", worker_result.worker)
        await blackboard.set("last_output", worker_result.output)
        await blackboard.set("pipeline_step", step_index)
        await blackboard.update(
            "pipeline_outputs",
            lambda current: [
                *(current or []),
                {
                    "worker": worker_result.worker,
                    "output": worker_result.output,
                },
            ],
        )
        previous_result = worker_result

    return PatternExecution(
        worker_results=worker_results,
        status="success",
        aggregated_output=worker_results[-1].output,
        metadata=await _build_blackboard_metadata(blackboard),
    )
