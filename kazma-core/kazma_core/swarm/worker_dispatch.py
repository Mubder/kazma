"""Per-worker reliability dispatch path — extracted from SwarmEngine (S5).

``dispatch_worker`` is the full circuit-breaker / retry / timeout / validation
path previously inlined as ``SwarmEngine._dispatch_worker``.
"""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import TYPE_CHECKING, Any

from kazma_core.swarm.blackboard import SwarmDispatchContext
from kazma_core.swarm.handoff import HandoffOffer, HandoffRequest, handoff_offer
from kazma_core.swarm.reliability import CircuitBreakerOpenError
from kazma_core.swarm.task import WorkerResult
from kazma_core.swarm.worker import SwarmWorker

__all__ = ["dispatch_worker"]

if TYPE_CHECKING:
    from kazma_core.swarm.engine import SwarmEngine

logger = logging.getLogger(__name__)

# Strong references for fire-and-forget memory-index tasks (deep-audit
# 2026-08-19, finding #17): the loop holds only weak refs to tasks, so an
# unreferenced one can be GC'd mid-write and silently drop the index —
# same bug class already fixed for alert tasks in engine.py.
_MEMORY_INDEX_TASKS: set = set()


async def _index_worker_l4_memory(
    *,
    worker_name: str,
    prompt: str,
    output: str,
    task_id: str = "",
) -> None:
    """Best-effort memory index for a completed swarm worker.

    Index a compact prompt+output snippet as a V2 ``swarm_result`` episode
    under the worker name so subsequent recall for that worker has content.
    """
    name = (worker_name or "default").strip() or "default"
    body = (output or "").strip()
    head = (prompt or "").strip()
    if not body and not head:
        return
    # Keep snippets short — V2 episode text caps content below.
    snippet = ""
    if head:
        snippet += f"Task: {head[:600]}\n"
    if body:
        snippet += f"Result: {body[:2000]}"
    snippet = snippet.strip()
    if len(snippet) < 12:
        return
    meta = {
        "worker": name,
        "source": "swarm_worker",
        "task_id": task_id or "",
        "type": "swarm_result",
    }
    # V2-native write: store the snippet as a V2 episode (source="swarm_result").
    # No worker→produced belief is written — nothing reads it and it flooded
    # the Beliefs UI; per-worker recall is served by the episode's worker
    # metadata + embedding instead of a worker_vectors_<name> table.
    # to_thread: store_swarm_result → embedder.encode → SentenceTransformer
    # cold-start (~12s first-use) BLOCKS the event loop if run inline.
    try:
        import asyncio as _aio

        from kazma_core.memory.swarm_bridge import store_swarm_result

        await _aio.to_thread(store_swarm_result, name, task_id or "", snippet, meta)
    except Exception:
        logger.debug("[SwarmEngine] V2 swarm_result store failed for %s", name, exc_info=True)


def _end_claim(engine: SwarmEngine, worker: SwarmWorker) -> None:
    """A dispatch its breaker refused never began: the autoscaler's claim on
    the instance ends here, so the next task gets it (and its breaker) rather
    than a new instance that would dodge the breaker (``AutoScaler._take``)."""
    if engine._autoscaler is not None:
        engine._autoscaler.record_activity(worker.name)


def _handoff_offer_for(
    engine: SwarmEngine, worker: SwarmWorker, visited: dict[str, int] | None, depth: int,
) -> HandoffOffer | None:
    """The engine's handoff offer for this dispatch of *worker*, or None (an
    engine double without one)."""
    make = getattr(engine, "_handoff_offer", None)
    offer = make(worker, visited, depth) if callable(make) else None
    return offer if isinstance(offer, HandoffOffer) else None


async def dispatch_worker(
    engine: SwarmEngine,
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
    # Normalize "no deadline" inputs BEFORE they reach TimeoutGuard
    # (same semantics as dispatch_helpers.wait_timeout): the guard's
    # execute() raises ValueError for timeout <= 0 and treats None as
    # its own 300s default, so the raw value cannot be forwarded — a
    # single {"timeout": 0} dispatch used to convert the ValueError into
    # an error result AND a breaker failure for every worker, tripping
    # every breaker OPEN. Clamp non-positive timeouts to the large
    # sentinel; unparseable values fall back to the guard's default.
    from kazma_core.swarm.dispatch_helpers import NO_DEADLINE_SENTINEL

    try:
        _t = float(timeout)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        timeout = None
    else:
        if _t <= 0:
            timeout = NO_DEADLINE_SENTINEL

    reliability = getattr(engine, "_reliability", None)
    breaker = (reliability.breaker_for_dispatch(worker.name)
               if reliability is not None and hasattr(reliability, "breaker_for_dispatch")
               else engine.get_circuit_breaker(worker.name))
    retry_policy = engine.get_retry_policy(worker.name)
    timeout_guard = engine.get_timeout_guard(worker.name)
    output_validator = engine.get_output_validator(worker.name, validation_schema)

    # Emit a dispatch span if tracing is active and a trace_id is available.
    dispatch_span = None
    if trace_id:
        dispatch_span = engine._tracing_emitter.start_dispatch_span(
            trace_id, worker.name
        )

    admission = None  # the breaker's admission of this dispatch (SW-E11)
    # Circuit breaker pre-check: reject immediately if open.
    try:
        # allow_probe sets _probe_in_flight in half-open; the finally below
        # releases it via `recorded` (not a separate probe_held flag — that
        # var was assigned 3x but never read, dead code) (audit finding).
        # The shared breaker reads the settings store: in a thread (SW-E07).
        admission = await asyncio.to_thread(breaker.check_or_raise, worker.name)
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
        # Counted once, by _finalize_task with the task's other results
        # (Swarm audit SW-E19: recorded here too, one refusal was two
        # failures in the worker's metrics).
        _end_claim(engine, worker)
        return [result]

    # Execute with retry policy.
    started = perf_counter()
    recorded = False

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
    from kazma_core.agent.dispatch_effects import DispatchEffects, track_dispatch_effects

    effects = DispatchEffects()

    # Whether _attempt ran worker.mark_dispatched (which sets busy=True).
    # busy is ONLY reset by mark_completed(); on the normal result path
    # that happens below, and the BaseException handler below releases it
    # for cancellation (cancel_task / pattern-level wait_for timeouts) —
    # without that, a cancelled dispatch left the worker object busy for
    # the process lifetime.
    dispatched = False

    # Phase 3: extract per-task workspace_id so we can scope the dispatch.
    # Phase 5: also extract the commitment scope-token (§3.11 swarm privilege cap).
    _ws_id = None
    _scope_token = None
    if isinstance(context, SwarmDispatchContext):
        _ws_id = context.metadata.get("workspace_id")
        from kazma_core.safety.commitment.scope import ScopeToken

        _scope_token = ScopeToken.from_metadata(context.metadata.get("commitment_scope"))

    # Phase 5 (§3.11): no explicit scope → assign the default worker scope when
    # enforcement is on (capped at HIGH; denies soul/identity/config). This makes
    # the scope-token enforcement go live the moment the operator enables
    # swarm_scope_enforce, without any per-dispatch wiring.
    if _scope_token is None:
        from kazma_core.safety.commitment.scope import default_worker_scope

        _scope_token = default_worker_scope(_ws_id)

    # Who this dispatch may hand off to, with the engine's cycle guard for
    # this point of the chain (Swarm audit SW-E13): the worker's handoff tool.
    _offer = _handoff_offer_for(engine, worker, _visited, _depth)

    try:
        async def _attempt() -> dict[str, Any]:
            nonlocal dispatched
            # One dispatch is one in-flight count, however many attempts its
            # retry policy makes: mark_completed runs once (SW-E12).
            if not dispatched:
                worker.mark_dispatched(prompt)
            dispatched = True
            # Record activity for auto-scaler reaping
            if engine._autoscaler is not None:
                engine._autoscaler.record_activity(worker.name)

            async def _do_dispatch():
                """Run the worker dispatch, honoring per-task workspace + commitment scope."""
                from kazma_core.ide.workspace_scope import workspace_scope
                from kazma_core.safety.commitment.scope import swarm_scope
                from kazma_core.tenant_context import tenant_scope

                tid = None
                if isinstance(context, SwarmDispatchContext):
                    raw = (context.metadata or {}).get("tenant_id")
                    if raw:
                        tid = str(raw)
                # Settings-saved API keys live under tenant 'default'. A
                # worker with no ContextVar reads every vault:// pointer as
                # missing and the registry substitutes a different provider
                # (audit 2026-09-17). Install metadata tenant, else default.
                with tenant_scope(tid or "default"):
                    # Both managers no-op when their token is None, so always wrap.
                    async with workspace_scope(_ws_id), swarm_scope(_scope_token):
                        with track_dispatch_effects(effects), handoff_offer(_offer):
                            return await worker.dispatch(prompt, context=context)

            try:
                raw_result = await timeout_guard.execute(
                    _do_dispatch,
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
                from kazma_core.swarm.reliability import _is_retryable_exception

                raw_result = {
                    "worker": worker.name,
                    "task_id": "",
                    "status": "error",
                    "output": "",
                    "error": str(exc)[:500],
                    # Permanent failures (401, an invalid key) are not retried
                    # (SW-E08: the classifier said so and they were anyway).
                    "retryable": _is_retryable_exception(exc),
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

            if effects.invoked and raw_result.get("status") != "success":
                raw_result["retry_safe"] = False
                raw_result["error"] = (
                    str(raw_result.get("error") or "Worker did not complete")
                    + " Whole-worker retry withheld because a mutating tool was invoked"
                    + (f" ({', '.join(effects.tools)})" if effects.tools else "")
                    + "; inspect its effects."
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
            # Do NOT record_success here — that double-counted the source
            # breaker when _handle_handoff also record_* based on the target
            # outcome (audit residual). Sole accounting lives in _handle_handoff.
            results = await engine._handle_handoff(
                handoff_req=handoff_req,
                source_worker=worker,
                prompt=prompt,
                context=context,
                timeout=timeout,
                validation_schema=validation_schema,
                started=started,
                breaker=breaker,
                admission=admission,
                trace_id=trace_id,
                _visited=_visited,
                _depth=_depth + 1,
            )
            recorded = True  # _handle_handoff always record_* or release_probe
            # The caller chooses the last handoff result for task-level
            # fallback. Keep earlier effects on every failed result, including
            # a missing target and failures deeper in a multi-hop chain.
            if effects.invoked or any(not result.retry_safe for result in results):
                for result in results:
                    if result.status != "success":
                        result.retry_safe = False
                        result.error = (
                            str(result.error or "Handoff did not complete")
                            + " Handoff-chain fallback withheld because a mutating tool was invoked; inspect its effects."
                        )
            return results

        worker_result = WorkerResult.from_dict(raw_result)
        if worker_result.duration_seconds <= 0:
            worker_result.duration_seconds = perf_counter() - started

        # End the dispatch span.
        if dispatch_span:
            span_status = "ok" if worker_result.status == "success" else "error"
            if worker_result.error:
                dispatch_span.set_attribute("error.message", worker_result.error[:200])
            engine._tracing_emitter.end_span(dispatch_span, status=span_status)

        # Update circuit breaker based on outcome -- and the shared copy, a
        # settings write -- in a thread (SW-E07).
        def _record_outcome(success: bool = worker_result.status == "success") -> None:
            if success:
                breaker.record_success(admission)
            else:
                breaker.record_failure(admission)
            try:
                if hasattr(engine, "_reliability") and hasattr(
                    engine._reliability, "note_breaker_outcome"
                ):
                    engine._reliability.note_breaker_outcome(worker.name)
                else:
                    breaker.persist_shared(worker.name)
            except Exception:
                logger.warning("[SwarmEngine] could not share the breaker of '%s'", worker.name,
                               exc_info=True)

        await asyncio.to_thread(_record_outcome)
        recorded = True

        worker.mark_completed(worker_result.status)
        dispatched = False  # busy released on the normal result path
        # Stamp activity at completion so a long task does not look idle
        # the moment busy drops (M-14). reap_idle also skips busy workers.
        if engine._autoscaler is not None:
            engine._autoscaler.record_activity(worker.name)

        # Index successful worker output as a V2 swarm_result episode under
        # this worker's name so swarm memory is not only "default".
        # Fire-and-forget: the memory index (embedder encode → potential
        # SentenceTransformer cold-start) must NOT delay the dispatch return
        # or the pipeline's per-step timeout. The task runs in the background.
        if worker_result.status == "success" and (worker_result.output or prompt):
            try:
                import asyncio as _aio_create

                _idx_task = _aio_create.create_task(
                    _index_worker_l4_memory(
                        worker_name=getattr(worker, "name", "") or "default",
                        prompt=prompt or "",
                        output=str(worker_result.output or ""),
                        task_id=getattr(context, "task_id", "") if context is not None else "",
                    )
                )
                _MEMORY_INDEX_TASKS.add(_idx_task)
                _idx_task.add_done_callback(_MEMORY_INDEX_TASKS.discard)
            except Exception:
                logger.debug(
                    "[SwarmEngine] worker memory index spawn failed for %s",
                    getattr(worker, "name", "?"),
                    exc_info=True,
                )

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
    except BaseException as exc:
        if not recorded:
            # Unwinding (often a cancellation): not awaited here; the
            # breaker's lock orders it with any other outcome (SW-E07).
            from kazma_core.background import spawn_background

            # A stop the user asked for (or a shutdown) says nothing about the
            # worker: no breaker failure (SW-E11). A deadline does.
            cause = engine._interrupt_cause(getattr(context, "task_id", None) or "") \
                if hasattr(engine, "_interrupt_cause") else None
            if isinstance(exc, asyncio.CancelledError) and cause in (
                    "cancelled_by_user", "caller_stopped", "shutdown"):
                spawn_background(asyncio.to_thread(breaker.release_probe),
                                 name=f"swarm-breaker-release:{worker.name}")
            else:
                spawn_background(asyncio.to_thread(breaker.record_failure, admission),
                                 name=f"swarm-breaker-failure:{worker.name}")
            recorded = True
        # Cancellation (cancel_task / a pattern-level wait_for timeout)
        # unwinds before the normal path's mark_completed() — release the
        # busy flag here or the worker object stays busy for the process
        # lifetime. The breaker failure above is kept (current policy:
        # a cancelled dispatch still counts); only the busy flag is
        # released. mark_completed is idempotent w.r.t. busy (False→False)
        # so a late cancel after the normal path completed is harmless.
        if dispatched:
            try:
                worker.mark_completed(
                    "cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "error"
                )
                if engine._autoscaler is not None:
                    engine._autoscaler.record_activity(worker.name)
            except Exception:
                # The worker stays "busy" to the autoscaler: say so (SW-E14).
                logger.warning(
                    "[SwarmEngine] mark_completed on abort failed for worker '%s'",
                    worker.name,
                    exc_info=True,
                )
        raise
    finally:
        # Audit H8: never leave half-open stuck if cancel/timeout skipped record_*.
        if not recorded and hasattr(breaker, "release_probe"):
            from kazma_core.background import spawn_background

            spawn_background(asyncio.to_thread(breaker.release_probe),
                             name=f"swarm-breaker-release:{worker.name}")
