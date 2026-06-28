"""Consult orchestration helpers for the swarm engine."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from kazma_core.swarm.aggregator import ResultAggregator
from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.task import SwarmTask, WorkerCapabilities, WorkerResult
from kazma_core.swarm.worker import SwarmWorker

logger = logging.getLogger(__name__)

DispatchWorkerByName = Callable[
    [str, str, str | SwarmDispatchContext],
    Awaitable[WorkerResult],
]
ResolveWorker = Callable[[str], SwarmWorker | None]

_CONSULT_SYNTHESIS_SYSTEM_PROMPT = """You are the orchestrator for a swarm consultation.

Read each worker opinion carefully and synthesize a final answer that references
every successful worker by name. Attribute each recommendation to the worker
who made it, for example "Worker A suggests ..." or "Worker B recommends ...".
If workers disagree, call out the disagreement clearly before giving the final
recommendation.
"""

_CONSULT_SYNTHESIS_INSTRUCTION = (
    "Produce a synthesized consultation answer. Reference every successful "
    "worker at least once and attribute each point with that worker's name."
)


class ConsultationConfigurationError(ValueError):
    """Raised when a consult task is missing required configuration."""


@dataclass
class ConsultationExecution:
    """Normalized output returned by the consult orchestration pattern."""

    worker_results: list[WorkerResult]
    individual_opinions: list[WorkerResult]
    status: str
    aggregated_output: str | None = None
    synthesized_output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def _join_errors(worker_results: list[WorkerResult]) -> str | None:
    """Join non-empty worker errors into a single message."""
    error_messages = [result.error for result in worker_results if result.error]
    if not error_messages:
        return None
    return "; ".join(error_messages)


def _consult_status(
    worker_results: list[WorkerResult],
    individual_opinions: list[WorkerResult],
) -> str:
    """Map consult outcomes to success, partial, or failed."""
    if not worker_results:
        return "failed"
    if len(individual_opinions) == len(worker_results):
        return "success"
    if individual_opinions:
        return "partial"
    return "failed"


async def _build_blackboard_metadata(blackboard: BlackboardStore) -> dict[str, Any]:
    """Return result metadata containing the shared blackboard snapshot."""
    snapshot = await blackboard.snapshot()
    return {
        "blackboard": snapshot,
        "blackboard_snapshot": snapshot,
    }


def _worker_capabilities(worker: SwarmWorker | None) -> WorkerCapabilities:
    """Return normalized worker capabilities for consult prompting."""
    if worker is None:
        return WorkerCapabilities()
    capabilities = WorkerCapabilities.from_dict(worker.capabilities)
    if not capabilities.role:
        capabilities.role = worker.role
    return capabilities


def _build_consult_system_prompt(
    worker_name: str,
    worker: SwarmWorker | None,
) -> str:
    """Build the role-aware consult system prompt for one worker."""
    capabilities = _worker_capabilities(worker)
    expertise = ", ".join(capabilities.expertise) or "general software engineering"
    sections = [
        f"You are worker '{worker_name}' participating in a swarm consultation.",
        f"Your role is '{capabilities.role or worker_name}'.",
        f"Your expertise includes: {expertise}.",
    ]
    if capabilities.model_specialty:
        sections.append(
            f"Your model specialty is '{capabilities.model_specialty}'."
        )
    sections.extend(
        [
            "Respond independently using only the original question and context.",
            "Do not assume you know any other worker's opinion or output.",
            "Give a concise recommendation with a short justification.",
        ]
    )
    return "\n".join(sections)


async def execute_consult(
    task: SwarmTask,
    *,
    resolve_worker: ResolveWorker,
    dispatch_worker_by_name: DispatchWorkerByName,
    aggregator: ResultAggregator,
    max_concurrent: int,
) -> ConsultationExecution:
    """Execute a consult task across workers and synthesize the opinions."""
    if not task.workers:
        raise ConsultationConfigurationError("Consult requires at least one worker.")

    blackboard = BlackboardStore()
    semaphore = asyncio.Semaphore(max(1, min(max_concurrent, len(task.workers))))

    async def dispatch_one(worker_name: str) -> WorkerResult:
        worker = resolve_worker(worker_name)
        capabilities = _worker_capabilities(worker)
        system_prompt = _build_consult_system_prompt(worker_name, worker)
        dispatch_context = SwarmDispatchContext(
            task.context,
            blackboard=blackboard,
            metadata={
                **task.metadata,
                "worker_name": worker_name,
                "consult_role": capabilities.role or worker_name,
                "consult_expertise": list(capabilities.expertise),
                "consult_mode": True,
            },
            task_id=task.id,
            task_type=task.type.value,
            system_prompt=system_prompt,
        )

        try:
            async with semaphore:
                worker_result = await asyncio.wait_for(
                    dispatch_worker_by_name(worker_name, task.prompt, dispatch_context),
                    timeout=task.timeout,
                )
        except TimeoutError:
            logger.warning(
                "[Consultation] worker '%s' timed out after %.2fs",
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
        except Exception as exc:
            logger.exception(
                "[Consultation] worker '%s' raised unexpectedly",
                worker_name,
            )
            worker_result = WorkerResult(
                worker=worker_name,
                task_id=task.id,
                status="error",
                output="",
                error=str(exc)[:500],
            )

        if not worker_result.task_id:
            worker_result.task_id = task.id

        await blackboard.update(
            "consultation_results",
            lambda current: [
                *(current or []),
                {
                    "worker": worker_result.worker,
                    "status": worker_result.status,
                    "role": capabilities.role or worker_name,
                    "expertise": list(capabilities.expertise),
                    "output": worker_result.output,
                    "error": worker_result.error,
                },
            ],
        )
        return worker_result

    worker_results = await asyncio.gather(
        *(dispatch_one(worker_name) for worker_name in task.workers)
    )
    individual_opinions = [
        result for result in worker_results if result.status == "success"
    ]
    status = _consult_status(worker_results, individual_opinions)
    error = _join_errors(worker_results)
    metadata = {
        **(await _build_blackboard_metadata(blackboard)),
        "consulted_workers": [
            {
                "name": worker_name,
                "role": _worker_capabilities(resolve_worker(worker_name)).role
                or worker_name,
                "expertise": list(
                    _worker_capabilities(resolve_worker(worker_name)).expertise
                ),
            }
            for worker_name in task.workers
        ],
        "individual_opinion_count": len(individual_opinions),
        "max_concurrent": max_concurrent,
    }

    if not individual_opinions:
        return ConsultationExecution(
            worker_results=worker_results,
            individual_opinions=[],
            status="failed",
            error=error or "All consulted workers failed.",
            metadata=metadata,
        )

    if len(individual_opinions) == 1:
        synthesized_output = individual_opinions[0].output
    else:
        synthesized_output = await aggregator.synthesize(
            task,
            individual_opinions,
            system_prompt=_CONSULT_SYNTHESIS_SYSTEM_PROMPT,
            final_instruction=_CONSULT_SYNTHESIS_INSTRUCTION,
        )

    return ConsultationExecution(
        worker_results=worker_results,
        individual_opinions=individual_opinions,
        status=status,
        aggregated_output=synthesized_output,
        synthesized_output=synthesized_output,
        error=None if status == "success" else error,
        metadata=metadata,
    )
