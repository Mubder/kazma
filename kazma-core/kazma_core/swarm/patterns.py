"""Orchestration pattern implementations for the swarm engine."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from kazma_core.agent.dispatch_effects import DispatchEffects, track_effects
from kazma_core.swarm.aggregator import ResultAggregator
from kazma_core.swarm.blackboard import BlackboardStore
from kazma_core.swarm.dispatch_helpers import pattern_dispatch_context, step_timeout_result, wait_timeout
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
    #: What the pattern's own model call (the Refiner, a synthesis) cost: the
    #: engine adds it to the task's totals (Swarm audit SW-E15).
    synthesis_tokens: int = 0
    synthesis_cost: float = 0.0


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

__all__ = ["ConditionalConfigurationError", "FanOutConfigurationError", "PatternExecution", "PipelineConfigurationError", "execute_conditional", "execute_fan_out", "execute_pipeline", "resume_pipeline"]

#: What the Refiner reads of the steps' outputs, all steps together, and the
#: most it may write (Swarm audit SW-E15).
_REFINER_INPUT_CHARS = 4000
_REFINER_MAX_TOKENS = 2048
#: Its deadline when the task's step deadline is longer, or none.
_REFINER_MAX_S = 120.0
_REFINER_SYSTEM = ("You are a Refiner. Distill worker outputs into key findings, code, and next "
                   "actions. Output in Markdown.")


def _refiner_client() -> Any | None:
    """The model the Refiner asks: the active one (settings keys live under
    tenant 'default' when no tenant is bound)."""
    from kazma_core.model_registry import get_model_registry
    from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

    registry = get_model_registry()
    if get_current_tenant_id():
        return registry.get_client()
    with tenant_scope("default"):
        return registry.get_client()


def _refiner_input(worker_results: list[WorkerResult]) -> tuple[str, bool]:
    """The steps' outputs as the Refiner reads them: each fenced as data
    under its worker's name (SW-S10), all within ``_REFINER_INPUT_CHARS``.
    Returns the text and whether any output was cut."""
    from kazma_core.safety.prompt_fence import fence_untrusted

    budget, cut, parts = _REFINER_INPUT_CHARS, False, []
    for result in worker_results:
        text = (result.output or "").strip() if result.status == "success" else ""
        if not text:
            continue
        if budget <= 0:
            cut = True
            break
        if len(text) > budget:
            text, cut = text[:budget], True
        budget -= len(text)
        parts.append(f"{result.worker}:\n" + fence_untrusted(text, source=f"swarm_worker:{result.worker}"))
    return ("\n---\n".join(parts) or "No output"), cut


async def _synthesize_refined_output(
    task: SwarmTask,
    worker_results: list[WorkerResult],
    overall_status: str,
    total_ms: float,
) -> Any:
    """The Refiner: one model call that distills a finished pipeline's step
    outputs into a report (``task_model.call_task_model``: ledgered, its
    usage returned, bounded). Raises when it cannot answer; the caller keeps
    the unrefined result. Returns a ``TaskModelAnswer`` (a stand-in may
    return the text alone)."""
    from kazma_core.swarm.task_model import call_task_model

    combined, cut = _refiner_input(worker_results)
    user_prompt = (f"Task: {task.prompt[:300]}\nStatus: {overall_status}\nDuration: {total_ms:.0f}ms\n\n"
                   f"Raw worker outputs{' (cut to the first characters)' if cut else ''}:\n{combined}")
    messages = [{"role": "system", "content": _REFINER_SYSTEM}, {"role": "user", "content": user_prompt}]
    step = wait_timeout(task.timeout)
    return await call_task_model(_refiner_client(), messages, task_id=task.id, label="swarm-refiner",
                                 max_tokens=_REFINER_MAX_TOKENS,
                                 deadline=min(step, _REFINER_MAX_S) if step else _REFINER_MAX_S)


async def _refine(
    task: SwarmTask,
    worker_results: list[WorkerResult],
    status: str,
    total_ms: float,
) -> tuple[str | None, int, float, dict[str, Any]]:
    """The refined report of a finished pipeline, its tokens and cost, and
    what the task's metadata keeps of the call (``refiner``). A Refiner that
    fails leaves the unrefined result: a WARNING and a counted failure, never
    a silent stand-in (it used to return a 'raw report' as if refined)."""
    from kazma_core.swarm.task_model import TaskModelAnswer

    started = _time.perf_counter()
    try:
        answer = await _synthesize_refined_output(task, worker_results, status, total_ms)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("[Refiner] pipeline '%s' keeps its unrefined result: the Refiner failed (%s: %s)",
                       task.id, type(exc).__name__, exc)
        return _refiner_failed("TimeoutError" if isinstance(exc, TimeoutError) else type(exc).__name__)
    if isinstance(answer, str):
        answer = TaskModelAnswer(text=answer)  # a stand-in that returns the text alone
    text = (getattr(answer, "text", "") or "").strip()
    if not text:
        logger.warning("[Refiner] pipeline '%s' keeps its unrefined result: the Refiner answered nothing",
                       task.id)
        return _refiner_failed("EmptyAnswer")
    record = {"status": "refined", **answer.record(),
              "duration_ms": round((_time.perf_counter() - started) * 1000, 1),
              "steps": len(worker_results)}
    return text, answer.tokens, answer.cost_usd, record


def _refiner_failed(error: str) -> tuple[None, int, float, dict[str, Any]]:
    """A failed Refiner, counted (``kazma_swarm_failures_total{site="refiner"}``);
    the caller logged the WARNING. *error* is a name, never the exception's
    text (AGENTS §26F: the page shows it)."""
    from kazma_core.metrics import record_swarm_failure

    record_swarm_failure("refiner")
    return None, 0, 0.0, {"status": "failed", "error": error}


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
        from kazma_core.skills.self_improvement import (
            get_self_improvement,
            mint_soul_commitment,
            self_improvement_enabled,
        )

        if not self_improvement_enabled():
            return
        si = get_self_improvement()
        for res in worker_results:
            if not res.worker:
                continue

            class _WorkerStage:
                """Wraps a single WorkerResult as a stage for analyze()."""
                def __init__(self, r: WorkerResult) -> None:
                    self.role = r.worker
                    self.worker_name = r.worker
                    # Normalize: engine uses success/error; SI expects completed/failed
                    rs = (r.status or "").lower()
                    if rs in ("success", "ok", "completed"):
                        self.status = "completed"
                    elif rs in ("error", "failed", "failure", "timeout", "cancelled"):
                        self.status = "failed" if rs != "timeout" else "timeout"
                    else:
                        self.status = rs or "failed"
                    self.output = r.output
                    self.error = r.error
                    self.duration_ms = (getattr(r, "duration_seconds", 0) or 0) * 1000

            # Analyze this worker against ONLY its own result
            task_status = (status or "").lower()
            if task_status in ("success", "ok"):
                task_status = "completed"
            elif task_status in ("error", "failure"):
                task_status = "failed"
            analysis = await si.analyze(
                worker_name=res.worker,
                task=task.prompt,
                stages=[_WorkerStage(res)],
                status=task_status,
            )
            if analysis.get("action") == "mutate":
                _delta = analysis["delta"]
                _cid = await asyncio.to_thread(
                    mint_soul_commitment, _delta, worker_name=res.worker
                )
                await si.apply_mutation(res.worker, _delta, commitment_id=_cid)
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
    # The pipeline's product: its last step's output, unrefined, as the
    # steps' outputs stay in worker_results.
    aggregated_output = _last_success_output(worker_results)
    metadata = await _build_blackboard_metadata(blackboard)

    # 1. The Refiner's report (Swarm audit SW-E15, decision D8: fixed). It is
    # the result's synthesized_output -- what the Swarm page shows as the
    # synthesis -- and its tokens and cost are the task's. Only a pipeline
    # that succeeded is refined: a report over a failed or partial run would
    # read as an answer to it (AGENTS §3: never synthesize over a broken turn).
    refined_output: str | None = None
    refiner_tokens, refiner_cost = 0, 0.0
    if status == "success":
        refined_output, refiner_tokens, refiner_cost, metadata["refiner"] = await _refine(
            task, worker_results, status, total_ms)

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
        metadata=metadata,
        synthesis_tokens=refiner_tokens,
        synthesis_cost=refiner_cost,
    )


async def _build_pipeline_context_text(
    task: SwarmTask,
    previous_result: WorkerResult | None,
    blackboard: BlackboardStore,
) -> str:
    """Compose the text payload passed to the next pipeline worker."""
    if previous_result is None:
        return task.context

    # The previous worker's text and the blackboard are what workers read
    # and wrote: data for the next worker, never its instructions (Swarm
    # audit SW-S10: handed on raw).
    from kazma_core.safety.prompt_fence import fence_untrusted

    sections = [section for section in [task.context.strip()] if section]
    sections.append(
        f"Previous worker ({previous_result.worker}) output:\n"
        + fence_untrusted(previous_result.output or "", source=f"swarm_worker:{previous_result.worker}")
    )

    blackboard_snapshot = await blackboard.snapshot()
    if blackboard_snapshot:
        sections.append(
            "Shared blackboard snapshot:\n"
            + fence_untrusted(
                json.dumps(
                    blackboard_snapshot,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ),
                source="swarm_blackboard",
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
        dispatch_context = pattern_dispatch_context(
            task,
            blackboard=blackboard,
            extra={
                "pipeline_step": step_index,
                "worker_name": worker_name,
            },
            context_text=context_text,
        )

        step_effects = DispatchEffects()
        try:
            with track_effects(step_effects):
                worker_result = await asyncio.wait_for(
                    dispatch_worker_by_name(
                        worker_name,
                        task.prompt,
                        dispatch_context,
                        fallback_chain=task.fallback_chain or None,
                    ),
                    timeout=wait_timeout(task.timeout),
                )
        except TimeoutError:
            logger.warning(
                "[SwarmPatterns] pipeline worker '%s' timed out after %.2fs",
                worker_name,
                task.timeout,
            )
            worker_result = step_timeout_result(task, worker_name, step_effects, label="Worker")

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
    on_step: Callable[[SwarmTask, int, str], Awaitable[None]] | None = None,
) -> PatternExecution:
    """Resume a paused pipeline from *starting_step* (1-based inclusive).

    *on_step* is awaited with ``"begun"`` before each step's worker runs and
    ``"committed"`` once its result is in (Swarm audit SW-E01: the engine
    journals them, so a run that dies mid-step is never resumed again).

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
        dispatch_context = pattern_dispatch_context(
            task,
            blackboard=blackboard,
            extra={
                "pipeline_step": step_index,
                "worker_name": worker_name,
            },
            context_text=context_text,
        )

        if on_step is not None:
            await on_step(task, step_index, "begun")
        step_effects = DispatchEffects()
        try:
            with track_effects(step_effects):
                worker_result = await asyncio.wait_for(
                    dispatch_worker_by_name(
                        worker_name,
                        task.prompt,
                        dispatch_context,
                        fallback_chain=task.fallback_chain or None,
                    ),
                    timeout=wait_timeout(task.timeout),
                )
        except TimeoutError:
            logger.warning(
                "[SwarmPatterns] resumed pipeline worker '%s' timed out after %.2fs",
                worker_name,
                task.timeout,
            )
            worker_result = step_timeout_result(task, worker_name, step_effects, label="Worker")

        if not worker_result.task_id:
            worker_result.task_id = task.id
        worker_results.append(worker_result)
        if on_step is not None:
            await on_step(task, step_index, "committed")

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
    step_effects = DispatchEffects()
    try:
        with track_effects(step_effects):
            router_result = await asyncio.wait_for(
                dispatch_worker_by_name(
                    router_name,
                    task.prompt,
                    pattern_dispatch_context(task, extra={"worker_name": router_name}),
                    fallback_chain=task.fallback_chain or None,
                ),
                timeout=wait_timeout(task.timeout),
            )
    except TimeoutError:
        logger.warning(
            "[SwarmPatterns] conditional router '%s' timed out after %.2fs",
            router_name,
            task.timeout,
        )
        router_result = step_timeout_result(task, router_name, step_effects, label="Router worker")

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
    step_effects = DispatchEffects()
    try:
        with track_effects(step_effects):
            target_result = await asyncio.wait_for(
                dispatch_worker_by_name(
                    target_worker,
                    task.prompt,
                    pattern_dispatch_context(task, extra={"worker_name": target_worker}),
                ),
                timeout=wait_timeout(task.timeout),
            )
    except TimeoutError:
        logger.warning(
            "[SwarmPatterns] conditional target '%s' timed out after %.2fs",
            target_worker,
            task.timeout,
        )
        target_result = step_timeout_result(task, target_worker, step_effects, label="Worker")

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
    concurrency: BoundedConcurrency | None = None,
) -> PatternExecution:
    """Execute a task across multiple workers with bounded concurrency.

    Prefer passing a shared *concurrency* from
    ``engine.get_bounded_concurrency`` (audit M12). A private semaphore is
    only allocated when the caller omits it (tests / standalone use).
    """
    if not task.workers:
        raise FanOutConfigurationError("Fan-out requires at least one worker.")

    blackboard = BlackboardStore()
    if concurrency is None:
        concurrency = BoundedConcurrency(
            max_concurrent=max(1, min(max_concurrent, len(task.workers)))
        )

    async def dispatch_one(worker_name: str) -> WorkerResult:
        dispatch_context = pattern_dispatch_context(
            task,
            blackboard=blackboard,
            extra={
                "worker_name": worker_name,
                "aggregation": task.aggregation,
            },
        )

        step_effects = DispatchEffects()
        try:
            async with concurrency:
                with track_effects(step_effects):
                    worker_result = await asyncio.wait_for(
                        dispatch_worker_by_name(
                            worker_name,
                            task.prompt,
                            dispatch_context,
                            fallback_chain=task.fallback_chain or None,
                        ),
                        timeout=wait_timeout(task.timeout),
                    )
        except TimeoutError:
            logger.warning(
                "[SwarmPatterns] fan-out worker '%s' timed out after %.2fs",
                worker_name,
                task.timeout,
            )
            worker_result = step_timeout_result(task, worker_name, step_effects, label="Worker")
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
        single_status = _dispatch_like_status(worker_result)
        # Self-improvement hook (single-worker fan-out path)
        await _run_self_improvement(task, worker_results, single_status)
        return PatternExecution(
            worker_results=worker_results,
            status=single_status,
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
        # A synthesis's model call is the task's to account (SW-E15).
        synthesis_tokens=int(getattr(aggregation_result, "synthesis_tokens", 0) or 0),
        synthesis_cost=float(getattr(aggregation_result, "synthesis_cost", 0.0) or 0.0),
    )
