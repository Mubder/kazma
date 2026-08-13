"""Inner task-type routing for SwarmEngine.dispatch (S5 extract).

``dispatch_inner`` handles auto-routing, pattern execution (pipeline /
fan-out / consult / conditional), and single-worker dispatch + fallbacks.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING, Any

from kazma_core.routing_engine import NoCapableWorkersError
from kazma_core.swarm.blackboard import BlackboardStore
from kazma_core.swarm.consultation import (
    ConsultationConfigurationError,
    execute_consult,
)
from kazma_core.swarm.patterns import (
    ConditionalConfigurationError,
    FanOutConfigurationError,
    PipelineConfigurationError,
    execute_conditional,
    execute_fan_out,
    execute_pipeline,
)
from kazma_core.swarm.task import SwarmTask, TaskResult, TaskType

__all__ = ["dispatch_inner"]

if TYPE_CHECKING:
    from kazma_core.swarm.engine import SwarmEngine

logger = logging.getLogger(__name__)


async def dispatch_inner(
    engine: "SwarmEngine",
    task: SwarmTask,
    started: float,
    task_span: Any,
) -> TaskResult:
    """Inner dispatch logic, wrapped by dispatch() for catch-all safety."""

    # Auto-routing: resolve workers=["auto"] via Polymorphic Routing Engine.
    if list(task.workers) == ["auto"]:
        try:
            routed = await engine._routing_engine.route(
                task,
                engine._build_available_workers_list(),
            )
            task.workers = routed
        except NoCapableWorkersError as exc:
            # Try auto-scaling before giving up
            scaler = engine.get_autoscaler()
            spawned = None
            if scaler is not None:
                spawned = scaler.maybe_scale(task.prompt)
            if spawned is not None:
                task.workers = [spawned.name]
                logger.info("[SwarmEngine] Auto-scaled worker '%s' for task", spawned.name)
                # Fall through to normal dispatch with the new worker
            else:
                engine._tracing_emitter.record_exception(task_span, exc)
                engine._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
                return engine._finalize_task(
                    task,
                    worker_results=[],
                    status="failed",
                    error=str(exc),
                    duration_seconds=perf_counter() - started,
                )

    if task.type == TaskType.PIPELINE:
        try:
            pattern_result = await execute_pipeline(
                task,
                dispatch_worker_by_name=engine._dispatch_worker_by_name,
            )
        except PipelineConfigurationError as exc:
            engine._tracing_emitter.record_exception(task_span, exc)
            engine._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
            return engine._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error=str(exc),
                duration_seconds=perf_counter() - started,
            )

        # HITL checkpoint: pipeline paused at a checkpoint step.
        if pattern_result.status == "paused":
            engine._tracing_emitter.end_span(task_span, status="ok")
            return engine._handle_pipeline_checkpoint(
                task=task,
                pattern_result=pattern_result,
                started=started,
            )

        engine._tracing_emitter.end_span(task_span, status="ok")
        return engine._finalize_task(
            task,
            worker_results=pattern_result.worker_results,
            status=pattern_result.status,
            aggregated_output=pattern_result.aggregated_output,
            error=pattern_result.error,
            duration_seconds=perf_counter() - started,
            metadata=pattern_result.metadata,
        )

    if task.type == TaskType.FAN_OUT:
        try:
            _mc = engine._resolve_max_concurrent(task)
            pattern_result = await execute_fan_out(
                task,
                dispatch_worker_by_name=engine._dispatch_worker_by_name,
                aggregator=engine._result_aggregator,
                max_concurrent=_mc,
                concurrency=engine.get_bounded_concurrency(_mc),
            )
        except FanOutConfigurationError as exc:
            engine._tracing_emitter.record_exception(task_span, exc)
            engine._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
            return engine._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error=str(exc),
                duration_seconds=perf_counter() - started,
            )
        except ValueError as exc:
            engine._tracing_emitter.record_exception(task_span, exc)
            engine._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
            return engine._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error=str(exc),
                duration_seconds=perf_counter() - started,
            )

        # Emit aggregate span if an aggregation strategy was used.
        aggregation = task.aggregation or "collect"
        if aggregation != "collect":
            agg_span = engine._tracing_emitter.start_aggregate_span(
                task_span.trace_id, aggregation
            )
            agg_span.set_attribute("swarm.aggregation.count", len(pattern_result.worker_results))
            engine._tracing_emitter.end_span(agg_span, status="ok")

        engine._tracing_emitter.end_span(task_span, status="ok")
        return engine._finalize_task(
            task,
            worker_results=pattern_result.worker_results,
            status=pattern_result.status,
            aggregated_output=pattern_result.aggregated_output,
            synthesized_output=pattern_result.synthesized_output,
            error=pattern_result.error,
            duration_seconds=perf_counter() - started,
            metadata=pattern_result.metadata,
        )

    if task.type == TaskType.CONSULT:
        try:
            _mc = engine._resolve_max_concurrent(task)
            consult_result = await execute_consult(
                task,
                resolve_worker=engine.get_worker,
                dispatch_worker_by_name=engine._dispatch_worker_by_name,
                aggregator=engine._result_aggregator,
                max_concurrent=_mc,
                concurrency=engine.get_bounded_concurrency(_mc),
            )
        except ConsultationConfigurationError as exc:
            engine._tracing_emitter.record_exception(task_span, exc)
            engine._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
            return engine._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error=str(exc),
                duration_seconds=perf_counter() - started,
            )

        # Emit synthesize span when synthesis was performed.
        if consult_result.synthesized_output:
            synth_span = engine._tracing_emitter.start_synthesize_span(
                task_span.trace_id,
            )
            synth_span.set_attribute(
                "swarm.synthesize.opinion_count",
                len(consult_result.individual_opinions),
            )
            engine._tracing_emitter.end_span(synth_span, status="ok")

        engine._tracing_emitter.end_span(task_span, status="ok")
        return engine._finalize_task(
            task,
            worker_results=consult_result.worker_results,
            individual_opinions=consult_result.individual_opinions,
            status=consult_result.status,
            aggregated_output=consult_result.aggregated_output,
            synthesized_output=consult_result.synthesized_output,
            error=consult_result.error,
            duration_seconds=perf_counter() - started,
            metadata=consult_result.metadata,
        )

    if task.type == TaskType.CONDITIONAL:
        try:
            pattern_result = await execute_conditional(
                task,
                dispatch_worker_by_name=engine._dispatch_worker_by_name,
            )
        except ConditionalConfigurationError as exc:
            engine._tracing_emitter.record_exception(task_span, exc)
            engine._tracing_emitter.end_span(task_span, status="error", status_msg=str(exc))
            return engine._finalize_task(
                task,
                worker_results=[],
                status="failed",
                error=str(exc),
                duration_seconds=perf_counter() - started,
            )

        engine._tracing_emitter.end_span(task_span, status="ok")
        return engine._finalize_task(
            task,
            worker_results=pattern_result.worker_results,
            status=pattern_result.status,
            aggregated_output=pattern_result.aggregated_output,
            error=pattern_result.error,
            duration_seconds=perf_counter() - started,
            metadata=pattern_result.metadata,
        )

    if not task.workers:
        engine._tracing_emitter.end_span(task_span, status="error", status_msg="Dispatch requires at least one worker.")
        return engine._finalize_task(
            task,
            worker_results=[],
            status="failed",
            error="Dispatch requires at least one worker.",
            duration_seconds=perf_counter() - started,
        )

    worker_name = task.workers[0]
    worker = engine.get_worker(worker_name)
    if worker is None:
        # Fallback 1: try spawning from a template by name (autoscaler).
        try:
            scaler = engine.get_autoscaler()
            if scaler is not None:
                spawned = scaler.spawn_by_name(worker_name)
                if spawned is not None:
                    # spawn_by_name creates "name-pool-N" instances; redirect
                    # the task to the spawned instance name.
                    task.workers = [spawned.name]
                    worker = spawned
                    logger.info("[SwarmEngine] Auto-spawned '%s' for named dispatch",
                                spawned.name)
        except Exception:
            logger.debug("[SwarmEngine] autoscaler spawn-by-name failed", exc_info=True)

    if worker is None:
        # Fallback 2: create a default worker from the active model profile.
        try:
            from kazma_core.swarm.config import WorkerConfig, WorkerCapabilities
            from kazma_core.model_registry import get_model_registry

            reg = get_model_registry()
            profile = reg.get_active_profile()
            default_cfg = WorkerConfig(
                name=worker_name,
                type="in_process",
                model=profile.get("model", ""),
                provider=profile.get("provider", ""),
                role=worker_name,
                capabilities=WorkerCapabilities(expertise=[worker_name]),
            )
            worker = engine.add_worker(default_cfg)
            logger.info("[SwarmEngine] Auto-created default worker '%s' (model=%s)",
                        worker_name, profile.get("model", "?"))
        except Exception as exc:
            logger.warning("[SwarmEngine] Could not auto-create worker '%s': %s",
                           worker_name, exc)

    if worker is None:
        msg = f"Worker '{worker_name}' not found and could not be auto-created."
        engine._tracing_emitter.end_span(task_span, status="error", status_msg=msg)
        return engine._finalize_task(
            task,
            worker_results=[],
            status="failed",
            error=msg,
            duration_seconds=perf_counter() - started,
        )

    # Build a blackboard-wrapped dispatch context so that single-dispatch
    # tasks get the same SwarmDispatchContext treatment (system prompt,
    # blackboard, metadata) as broadcast/fanout/pipeline.
    dispatch_blackboard = BlackboardStore()
    dispatch_context = engine._build_dispatch_context(
        task, blackboard=dispatch_blackboard
    )

    # Dispatch the primary worker (returns all results including handoffs).
    all_worker_results = await engine._dispatch_worker(
        worker,
        task.prompt,
        dispatch_context,
        timeout=task.timeout,
        validation_schema=task.validation_schema,
        trace_id=task_span.trace_id,
    )
    worker_result = all_worker_results[-1]

    # Execute fallback chain if the primary failed and a chain is configured.
    # NOTE: this top-level call does NOT thread _visited/_depth (unlike the
    # mid-chain call in engine._dispatch_worker_by_name, which does — M13).
    # That is correct BY CONSTRUCTION: at top level no handoffs have occurred
    # yet, so the hop budget starts fresh. Do not "helpfully" thread state
    # from here without also propagating it from the top-level dispatch —
    # otherwise A→B→fallback→A could reset visit counts (audit finding).
    if worker_result.status != "success" and task.fallback_chain:
        fallback_result, fallback_all = await engine._execute_fallback_chain(
            worker_result,
            task.fallback_chain,
            prompt=task.prompt,
            context=dispatch_context,
            timeout=task.timeout,
            validation_schema=task.validation_schema,
        )
        all_worker_results = fallback_all
        worker_result = fallback_result

    # Determine overall status: if fallbacks ran, the final result's
    # status determines the outcome (intermediate failures are expected).
    if len(all_worker_results) > 1 and task.fallback_chain:
        result_status = (
            "success" if worker_result.status == "success" else "failed"
        )
    else:
        result_status = engine._overall_status(all_worker_results)
    aggregated_output = worker_result.output if worker_result.status == "success" else None

    span_status = "ok" if result_status in ("success", "partial") else "error"
    engine._tracing_emitter.end_span(task_span, status=span_status)

    return engine._finalize_task(
        task,
        worker_results=all_worker_results,
        status=result_status,
        aggregated_output=aggregated_output,
        error=worker_result.error if result_status != "success" else None,
        duration_seconds=perf_counter() - started,
        metadata=dict(task.metadata) if task.metadata else None,
    )
