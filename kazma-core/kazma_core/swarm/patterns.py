"""Orchestration pattern implementations for the swarm engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from kazma_core.swarm.aggregator import ResultAggregator
from kazma_core.swarm.blackboard import BlackboardStore, SwarmDispatchContext
from kazma_core.swarm.reliability import BoundedConcurrency
from kazma_core.swarm.task import SwarmTask, WorkerResult

logger = logging.getLogger(__name__)

DispatchWorkerByName = Callable[..., Awaitable[WorkerResult]]


class PipelineConfigurationError(ValueError):
    """Raised when a pipeline task is missing required configuration."""


class FanOutConfigurationError(ValueError):
    """Raised when a fan-out task is missing required configuration."""


class ConditionalConfigurationError(ValueError):
    """Raised when a conditional task is missing required configuration."""


@dataclass
class PatternExecution:
    """Normalized result returned by an orchestration pattern."""

    worker_results: list[WorkerResult]
    status: str
    aggregated_output: str | None = None
    synthesized_output: str | None = None
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


def _dispatch_like_status(worker_result: WorkerResult) -> str:
    """Map a single worker result to the status used by dispatch."""
    if worker_result.status == "success":
        return "success"
    if worker_result.status == "timeout":
        return "timeout"
    return "failed"


def _fan_out_status(worker_results: list[WorkerResult]) -> str:
    """Map parallel worker outcomes to an overall fan-out status."""
    if not worker_results:
        return "failed"
    if all(result.status == "success" for result in worker_results):
        return "success"
    if any(result.status == "success" for result in worker_results):
        return "partial"
    return "failed"


def _join_errors(worker_results: list[WorkerResult]) -> str | None:
    """Join non-empty worker errors into a single message."""
    error_messages = [result.error for result in worker_results if result.error]
    if not error_messages:
        return None
    return "; ".join(error_messages)


import time as _time

async def _synthesize_refined_output(
    task: str,
    worker_results: list[WorkerResult],
    overall_status: str,
    total_ms: float,
) -> str:
    """Synthesize pipeline outputs via LLM Refiner."""
    all_outputs = [r.output for r in worker_results if r.status == "success" and r.output]
    combined = "\n---\n".join(o.strip() for o in all_outputs if o.strip()) or "No output"

    user_prompt = f"Task: {task[:300]}\nStatus: {overall_status}\nDuration: {total_ms:.0f}ms\n\nRaw worker outputs:\n{combined[:4000]}"

    try:
        from kazma_core.model_registry import get_model_registry
        registry = get_model_registry()
        provider = registry.get_client()
        if provider is not None:
            messages = [
                {"role": "system", "content": "You are a Refiner. Distill worker outputs into key findings, code, and next actions. Output in Markdown."},
                {"role": "user", "content": user_prompt},
            ]
            response = await provider.chat(messages)
            return response.content
    except Exception as exc:
        logger.warning("[Refiner] LLM call failed, using raw output: %s", exc)

    lines: list[str] = []
    lines.append("## Swarm Report (raw)")
    lines.append("")
    lines.append(f"**Task:** {task[:200]}")
    lines.append(f"**Status:** {overall_status}")
    lines.append(f"**Duration:** {total_ms:.0f}ms")
    lines.append("")
    lines.append(combined[:2000])
    return "\n".join(lines)


async def _run_self_improvement(
    task: SwarmTask,
    worker_results: list[WorkerResult],
    status: str,
) -> None:
    """Run the self-improvement hook for all workers in a completed task.

    This is called from ``_finalize_pipeline`` (pipelines), and also from
    ``execute_fan_out`` and ``execute_conditional`` post-completion.

    Each worker is analyzed against ONLY its own result (not all stages),
    avoiding redundant LLM calls and misattribution.
    """
    try:
        from kazma_core.skills.self_improvement import get_self_improvement
        si = get_self_improvement()
        for res in worker_results:
            if not res.worker:
                continue

            class _WorkerStage:
                """Wraps a single WorkerResult as a stage for analyze()."""
                def __init__(self, r: WorkerResult) -> None:
                    self.role = r.worker
                    self.worker_name = r.worker
                    self.status = "completed" if r.status == "success" else r.status
                    self.output = r.output
                    self.error = r.error
                    self.duration_ms = getattr(r, "duration_ms", 0) or 0

            # Analyze this worker against ONLY its own result
            analysis = await si.analyze(
                worker_name=res.worker,
                task=task.prompt,
                stages=[_WorkerStage(res)],
                status="completed" if status == "success" else status,
            )
            if analysis.get("action") == "mutate":
                await si.apply_mutation(res.worker, analysis["delta"])
    except Exception as exc:
        logger.warning("[SwarmPatterns] Self-improvement hook failed: %s", exc)


async def _finalize_pipeline(
    task: SwarmTask,
    worker_results: list[WorkerResult],
    status: str,
    blackboard: BlackboardStore,
    started_ms: float,
) -> PatternExecution:
    total_ms = (_time.perf_counter() * 1000) - started_ms
    aggregated_output = _last_success_output(worker_results)
    
    # 1. Synthesize Output
    refined_output = await _synthesize_refined_output(task.prompt, worker_results, status, total_ms)
    
    # 2. Self-Improvement Hook
    await _run_self_improvement(task, worker_results, status)
        
    # 3. Pipeline Logger Hook
    try:
        from kazma_core.swarm.memory.pipeline_logger import get_pipeline_logger
        plog = get_pipeline_logger()
        cid = task.id
        for res in worker_results:
            st = "completed" if res.status == "success" else res.status
            plog.log_output(cid, res.worker, res.worker, res.output or "")
            plog.log_step(cid, res.worker, res.worker, "info", "stage_" + st,
                          f"Stage {res.worker}: {st}")
        plog.log_step(cid, "orchestrator", "pipeline", "info", "pipeline_complete",
                      f"Pipeline {cid}: {status} in {total_ms:.0f}ms",
                      {"stages": len(worker_results), "completed": len([r for r in worker_results if r.status == "success"])})
    except Exception as exc:
        logger.debug("[SwarmPatterns] Logging hook failed: %s", exc)
        
    # 4. Final Execution State
    return PatternExecution(
        worker_results=worker_results,
        status=status,
        aggregated_output=aggregated_output,
        synthesized_output=refined_output,
        error=worker_results[-1].error if status != "success" else None,
        metadata=await _build_blackboard_metadata(blackboard),
    )


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
    """Execute a task through workers sequentially, sharing one blackboard.

    If the task's ``metadata.hitl_checkpoints`` contains the current step
    index (1-based), the pipeline pauses after that step completes and
    returns a ``PatternExecution`` with ``status="paused"`` and checkpoint
    metadata.  The engine is responsible for storing the paused state and
    resuming later via :func:`resume_pipeline`.
    """
    if not task.workers:
        raise PipelineConfigurationError("Pipeline requires at least one worker.")

    started_ms = _time.perf_counter() * 1000
    blackboard = BlackboardStore()
    hitl_checkpoints: set[int] = set(task.metadata.get("hitl_checkpoints", []))

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
                dispatch_worker_by_name(
                    worker_name,
                    task.prompt,
                    dispatch_context,
                    fallback_chain=task.fallback_chain or None,
                ),
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
            return await _finalize_pipeline(
                task=task,
                worker_results=worker_results,
                status=_failure_status(worker_results, worker_result),
                blackboard=blackboard,
                started_ms=started_ms,
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

        # HITL checkpoint: pause if this step is a checkpoint.
        if step_index in hitl_checkpoints:
            output_preview = (
                worker_result.output[:500] if worker_result.output else ""
            )
            blackboard_snapshot = await blackboard.snapshot()
            logger.info(
                "[SwarmPatterns] HITL checkpoint reached at step %d (worker '%s')",
                step_index,
                worker_name,
            )
            return PatternExecution(
                worker_results=worker_results,
                status="paused",
                aggregated_output=worker_result.output,
                metadata={
                    **(await _build_blackboard_metadata(blackboard)),
                    "checkpoint": {
                        "step": step_index,
                        "worker": worker_name,
                        "output_preview": output_preview,
                        "needs_approval": True,
                        "task_id": task.id,
                    },
                    "paused_step": step_index,
                    "paused_blackboard": blackboard_snapshot,
                },
            )

    return await _finalize_pipeline(
        task=task,
        worker_results=worker_results,
        status="success",
        blackboard=blackboard,
        started_ms=started_ms,
    )


async def resume_pipeline(
    task: SwarmTask,
    *,
    starting_step: int,
    worker_results: list[WorkerResult],
    blackboard_data: dict[str, Any],
    dispatch_worker_by_name: DispatchWorkerByName,
) -> PatternExecution:
    """Resume a paused pipeline from *starting_step* (1-based inclusive).

    Rebuilds the blackboard from *blackboard_data* and continues executing
    remaining workers.  Returns the final ``PatternExecution``.
    """
    started_ms = _time.perf_counter() * 1000
    blackboard = BlackboardStore.from_snapshot(blackboard_data)
    hitl_checkpoints: set[int] = set(task.metadata.get("hitl_checkpoints", []))

    # Rebuild previous_result from the last completed worker.
    previous_result = worker_results[-1] if worker_results else None

    remaining_workers = task.workers[starting_step - 1:]
    for offset, worker_name in enumerate(remaining_workers):
        step_index = starting_step + offset
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
                dispatch_worker_by_name(
                    worker_name,
                    task.prompt,
                    dispatch_context,
                    fallback_chain=task.fallback_chain or None,
                ),
                timeout=task.timeout,
            )
        except TimeoutError:
            logger.warning(
                "[SwarmPatterns] resumed pipeline worker '%s' timed out after %.2fs",
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
            return await _finalize_pipeline(
                task=task,
                worker_results=worker_results,
                status=_failure_status(worker_results, worker_result),
                blackboard=blackboard,
                started_ms=started_ms,
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

        # HITL checkpoint: pause again if this step is also a checkpoint.
        if step_index in hitl_checkpoints:
            output_preview = (
                worker_result.output[:500] if worker_result.output else ""
            )
            blackboard_snapshot = await blackboard.snapshot()
            logger.info(
                "[SwarmPatterns] HITL checkpoint reached at step %d (worker '%s')",
                step_index,
                worker_name,
            )
            return PatternExecution(
                worker_results=worker_results,
                status="paused",
                aggregated_output=worker_result.output,
                metadata={
                    **(await _build_blackboard_metadata(blackboard)),
                    "checkpoint": {
                        "step": step_index,
                        "worker": worker_name,
                        "output_preview": output_preview,
                        "needs_approval": True,
                        "task_id": task.id,
                    },
                    "paused_step": step_index,
                    "paused_blackboard": blackboard_snapshot,
                },
            )

    return await _finalize_pipeline(
        task=task,
        worker_results=worker_results,
        status="success",
        blackboard=blackboard,
        started_ms=started_ms,
    )


def _extract_route_decision(router_output: str) -> str:
    """Normalize router output into a route key.

    Strips whitespace and lowercases the output so that route matching is
    tolerant of minor formatting differences in the router's response.
    """
    return router_output.strip().lower()


async def execute_conditional(
    task: SwarmTask,
    *,
    dispatch_worker_by_name: DispatchWorkerByName,
) -> PatternExecution:
    """Execute a conditional routing pattern.

    1. Dispatch to the router worker (first in task.workers).
    2. Parse the router's output to determine the route key.
    3. Look up the route key in ``task.metadata["routes"]`` to find the target worker.
    4. If no match and a ``task.metadata["default"]`` worker exists, dispatch there.
    5. If no match and no default, return a clear failure.
    6. Record ``metadata.route_taken`` in the result.
    """
    if not task.workers:
        raise ConditionalConfigurationError(
            "Conditional requires at least one worker."
        )

    routes: dict[str, str] = task.metadata.get("routes", {})
    if not routes:
        raise ConditionalConfigurationError(
            "Conditional requires a 'routes' mapping in task metadata."
        )

    default_worker: str | None = task.metadata.get("default")
    router_name = task.workers[0]
    worker_results: list[WorkerResult] = []

    # Step 1: Execute the router worker.
    try:
        router_result = await asyncio.wait_for(
            dispatch_worker_by_name(
                router_name,
                task.prompt,
                task.context,
                fallback_chain=task.fallback_chain or None,
            ),
            timeout=task.timeout,
        )
    except TimeoutError:
        logger.warning(
            "[SwarmPatterns] conditional router '%s' timed out after %.2fs",
            router_name,
            task.timeout,
        )
        router_result = WorkerResult(
            worker=router_name,
            task_id=task.id,
            status="timeout",
            output="",
            error=f"Router worker '{router_name}' timed out after {task.timeout:g}s.",
        )

    if not router_result.task_id:
        router_result.task_id = task.id
    worker_results.append(router_result)

    # Step 2: If the router itself failed, halt immediately.
    if router_result.status != "success":
        return PatternExecution(
            worker_results=worker_results,
            status="timeout" if router_result.status == "timeout" else "failed",
            error=router_result.error or f"Router worker '{router_name}' failed.",
            metadata={"route_taken": None},
        )

    # Step 3: Parse the route decision from the router's output.
    route_decision = _extract_route_decision(router_result.output)
    target_worker: str | None = routes.get(route_decision)
    route_taken = route_decision

    # Step 4: Handle unmatched route.
    if target_worker is None:
        if default_worker is not None:
            target_worker = default_worker
            route_taken = "default"
        else:
            logger.warning(
                "[SwarmPatterns] no route matched for decision '%s'; "
                "available routes: %s",
                route_decision,
                list(routes.keys()),
            )
            return PatternExecution(
                worker_results=worker_results,
                status="failed",
                error=(
                    f"No route matched for decision '{route_decision}'. "
                    f"Available routes: {list(routes.keys())}"
                ),
                metadata={"route_taken": None},
            )

    # Step 5: Dispatch to the target worker.
    try:
        target_result = await asyncio.wait_for(
            dispatch_worker_by_name(target_worker, task.prompt, task.context),
            timeout=task.timeout,
        )
    except TimeoutError:
        logger.warning(
            "[SwarmPatterns] conditional target '%s' timed out after %.2fs",
            target_worker,
            task.timeout,
        )
        target_result = WorkerResult(
            worker=target_worker,
            task_id=task.id,
            status="timeout",
            output="",
            error=f"Worker '{target_worker}' timed out after {task.timeout:g}s.",
        )

    if not target_result.task_id:
        target_result.task_id = task.id
    worker_results.append(target_result)

    # Step 6: Build final result.
    status = _dispatch_like_status(target_result)
    # Self-improvement hook (conditional path)
    await _run_self_improvement(task, worker_results, status)
    return PatternExecution(
        worker_results=worker_results,
        status=status,
        aggregated_output=(
            target_result.output if target_result.status == "success" else None
        ),
        error=(
            None
            if target_result.status == "success"
            else target_result.error or f"Worker '{target_worker}' failed."
        ),
        metadata={"route_taken": route_taken},
    )


async def execute_fan_out(
    task: SwarmTask,
    *,
    dispatch_worker_by_name: DispatchWorkerByName,
    aggregator: ResultAggregator,
    max_concurrent: int,
) -> PatternExecution:
    """Execute a task across multiple workers with bounded concurrency."""
    if not task.workers:
        raise FanOutConfigurationError("Fan-out requires at least one worker.")

    blackboard = BlackboardStore()
    concurrency = BoundedConcurrency(max_concurrent=max(1, min(max_concurrent, len(task.workers))))

    async def dispatch_one(worker_name: str) -> WorkerResult:
        dispatch_context = SwarmDispatchContext(
            task.context,
            blackboard=blackboard,
            metadata={
                **task.metadata,
                "worker_name": worker_name,
                "aggregation": task.aggregation,
            },
            task_id=task.id,
            task_type=task.type.value,
        )

        try:
            async with concurrency:
                worker_result = await asyncio.wait_for(
                    dispatch_worker_by_name(
                        worker_name,
                        task.prompt,
                        dispatch_context,
                        fallback_chain=task.fallback_chain or None,
                    ),
                    timeout=task.timeout,
                )
        except TimeoutError:
            logger.warning(
                "[SwarmPatterns] fan-out worker '%s' timed out after %.2fs",
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
                "[SwarmPatterns] fan-out worker '%s' raised unexpectedly",
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
            "fan_out_outputs",
            lambda current: [
                *(current or []),
                {
                    "worker": worker_result.worker,
                    "status": worker_result.status,
                    "output": worker_result.output,
                },
            ],
        )
        return worker_result

    worker_results = await asyncio.gather(
        *(dispatch_one(worker_name) for worker_name in task.workers)
    )

    if len(worker_results) == 1:
        worker_result = worker_results[0]
        return PatternExecution(
            worker_results=worker_results,
            status=_dispatch_like_status(worker_result),
            aggregated_output=(
                worker_result.output if worker_result.status == "success" else None
            ),
            error=(
                None
                if worker_result.status == "success"
                else worker_result.error or f"Worker '{worker_result.worker}' failed."
            ),
            metadata={
                **(await _build_blackboard_metadata(blackboard)),
                "aggregation_strategy": task.aggregation,
                "max_concurrent": max_concurrent,
            },
        )

    aggregation_result = await aggregator.aggregate(task, worker_results)
    # Self-improvement hook (fan-out path)
    fan_status = _fan_out_status(worker_results)
    await _run_self_improvement(task, worker_results, fan_status)
    return PatternExecution(
        worker_results=worker_results,
        status=fan_status,
        aggregated_output=aggregation_result.aggregated_output,
        synthesized_output=aggregation_result.synthesized_output,
        error=_join_errors(worker_results),
        metadata={
            **(await _build_blackboard_metadata(blackboard)),
            **aggregation_result.metadata,
            "max_concurrent": max_concurrent,
        },
    )
