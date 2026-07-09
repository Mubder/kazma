"""Per-worker reliability dispatch path — extracted from SwarmEngine (S5).

``dispatch_worker`` is the full circuit-breaker / retry / timeout / validation
path previously inlined as ``SwarmEngine._dispatch_worker``.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING, Any

from kazma_core.swarm.blackboard import SwarmDispatchContext
from kazma_core.swarm.handoff import HandoffRequest
from kazma_core.swarm.reliability import CircuitBreakerOpenError
from kazma_core.swarm.task import WorkerResult
from kazma_core.swarm.worker import SwarmWorker

if TYPE_CHECKING:
    from kazma_core.swarm.engine import SwarmEngine

logger = logging.getLogger(__name__)


async def dispatch_worker(
    engine: "SwarmEngine",
    worker: SwarmWorker,
    prompt: str,
    context: str | SwarmDispatchContext,
    *,
    timeout: float | None = None,
    validation_schema: dict[str, Any] | None = None,
    trace_id: str | None = None,
    _visited: dict[str, int] | None = None,
    _depth: int = 0,
) -> list[WorkerResult]:
    """Dispatch a worker and return all results (including handoff chain).

    Returns a list of :class:`WorkerResult` objects.  In the normal
    case (no handoff) the list has a single element.  When a handoff
    occurs the list contains the source worker's result followed by
    every result from the target chain.
    """
    breaker = engine.get_circuit_breaker(worker.name)
    retry_policy = engine.get_retry_policy(worker.name)
    timeout_guard = engine.get_timeout_guard(worker.name)
    output_validator = engine.get_output_validator(worker.name, validation_schema)

    # Emit a dispatch span if tracing is active and a trace_id is available.
    dispatch_span = None
    if trace_id:
        dispatch_span = engine._tracing_emitter.start_dispatch_span(
            trace_id, worker.name
        )

    # Circuit breaker pre-check: reject immediately if open.
    try:
        breaker.check_or_raise(worker.name)
    except CircuitBreakerOpenError as exc:
        logger.warning("[SwarmEngine] %s", exc)
        if dispatch_span:
            engine._tracing_emitter.record_exception(dispatch_span, exc)
            engine._tracing_emitter.end_span(dispatch_span, status="error", status_msg=str(exc))
        result = WorkerResult(
            worker=worker.name,
            task_id="",
            status="error",
            output="",
            error=str(exc),
        )
        engine._metrics_collector.record_worker_result(result)
        return [result]

    # Execute with retry policy.
    started = perf_counter()

    # Emit worker_started (for SSE / observers)
    worker_name = worker.name if hasattr(worker, "name") else str(worker)
    task_id_for_sse = ""
    if isinstance(context, SwarmDispatchContext):
        task_id_for_sse = context.task_id or ""
    engine._emit_sse(
        task_id_for_sse,
        "worker_started",
        {"worker": worker_name, "step": 0},
    )

    # Mutable container for handoff state captured inside _attempt.
    captured_handoff: dict[str, Any] = {}

    async def _attempt() -> dict[str, Any]:
        worker.mark_dispatched(prompt)
        # Record activity for auto-scaler reaping
        if engine._autoscaler is not None:
            engine._autoscaler.record_activity(worker.name)
        try:
            raw_result = await timeout_guard.execute(
                lambda: worker.dispatch(prompt, context=context),
                timeout=timeout,
                worker_name=worker.name,
            )
        except HandoffRequest as handoff_req:
            # Capture the handoff request for the outer handler.
            captured_handoff["request"] = handoff_req
            # Return success so the retry loop exits immediately.
            return {
                "worker": worker.name,
                "task_id": "",
                "status": "success",
                "output": "",
                "error": None,
            }
        except Exception as exc:
            logger.exception(
                "[SwarmEngine] dispatch failed for worker '%s'", worker.name
            )
            raw_result = {
                "worker": worker.name,
                "task_id": "",
                "status": "error",
                "output": "",
                "error": str(exc)[:500],
            }

        # Validate output on success.
        if raw_result.get("status") == "success" and output_validator is not None:
            validation_error = output_validator.validate(
                raw_result.get("output", "")
            )
            if validation_error is not None:
                raw_result["status"] = "error"
                raw_result["error"] = (
                    f"Output validation failed: {validation_error}"
                )

        return raw_result

    raw_result = await retry_policy.execute_with_retry(
        _attempt, worker_name=worker.name
    )

    # Handle handoff if one was captured during _attempt.
    if captured_handoff.get("request") is not None:
        handoff_req: HandoffRequest = captured_handoff["request"]
        # End the dispatch span before handoff.
        if dispatch_span:
            engine._tracing_emitter.end_span(dispatch_span, status="ok")
        return await engine._handle_handoff(
            handoff_req=handoff_req,
            source_worker=worker,
            prompt=prompt,
            context=context,
            timeout=timeout,
            validation_schema=validation_schema,
            started=started,
            breaker=breaker,
            trace_id=trace_id,
            _visited=_visited,
            _depth=_depth + 1,
        )

    worker_result = WorkerResult.from_dict(raw_result)
    if worker_result.duration_seconds <= 0:
        worker_result.duration_seconds = perf_counter() - started

    # End the dispatch span.
    if dispatch_span:
        span_status = "ok" if worker_result.status == "success" else "error"
        if worker_result.error:
            dispatch_span.set_attribute("error.message", worker_result.error[:200])
        engine._tracing_emitter.end_span(dispatch_span, status=span_status)

    # Update circuit breaker based on outcome.
    if worker_result.status == "success":
        breaker.record_success()
    else:
        breaker.record_failure()

    worker.mark_completed(worker_result.status)

    # Emit worker_completed for observers (SSE etc.)
    output_preview = ""
    task_id_for_sse = ""
    if isinstance(context, SwarmDispatchContext):
        task_id_for_sse = context.task_id or ""
    if worker_result.output:
        output_preview = str(worker_result.output)[:200]
    engine._emit_sse(
        task_id_for_sse,
        "worker_completed",
        {
            "worker": worker.name if hasattr(worker, "name") else str(worker),
            "status": worker_result.status,
            "output_preview": output_preview,
        },
    )
    return [worker_result]
