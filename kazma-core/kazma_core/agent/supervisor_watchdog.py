"""Supervisor watchdog — heartbeat registry + supervised graph execution envelope.

The final self-healing layer from the long-horizon audit (report §3.2).
Wraps ``graph.ainvoke`` with:

1. **Heartbeats** — graph nodes call :func:`record_heartbeat` each time they
   run, so the watchdog can distinguish "working hard" from "wedged".
2. **Stall detection** — if no heartbeat arrives within
   ``watchdog.stall_threshold_seconds``, the in-flight invoke is cancelled and
   the incident classified as ``STALLED``.
3. **Failure classification** — exceptions are mapped to incident classes
   (``TRANSIENT_LLM``, ``CONTEXT_OVERFLOW``, ``PANIC``) using the existing
   ``LLMError.transient`` / ``LLMError.kind`` taxonomy.
4. **Rollback + reflect + resume** — the turn resumes from the last durable
   LangGraph checkpoint (``ainvoke(None, config)``), with a bounded system
   reflection message injected via ``aupdate_state`` so the model knows a
   recovery happened and should not repeat the failed action.
5. **Escalation** — after ``healing.max_recovery_attempts`` failures the
   exception propagates, preserving the existing honest ``turn_failed``
   behavior (no fabricated answers).

The envelope is OPT-IN: ``KazmaAgent.run()`` uses it only when
``agent.nonstop.enabled`` is true. With the master toggle off, the execution
path is byte-identical to before.

Everything here is best-effort: a watchdog-internal failure never breaks a
turn that would otherwise have succeeded.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

__all__ = [
    "IncidentClass",
    "classify_incident",
    "get_heartbeat",
    "record_heartbeat",
    "reset_heartbeat",
    "supervised_invoke",
]

logger = logging.getLogger(__name__)

# ── Heartbeat registry (process-wide, thread-safe) ─────────────────────

_heartbeats: dict[str, float] = {}
_hb_lock = threading.Lock()
# Bounded: heartbeats older than this are swept on write (dead threads).
_HB_TTL_SECONDS = 3600.0


def record_heartbeat(thread_id: str) -> None:
    """Stamp a heartbeat for *thread_id* (called by graph node wrappers)."""
    if not thread_id:
        return
    now = time.monotonic()
    with _hb_lock:
        _heartbeats[thread_id] = now
        # Opportunistic sweep so long-running processes don't accumulate
        # entries for finished threads.
        if len(_heartbeats) > 512:
            cutoff = now - _HB_TTL_SECONDS
            for tid in [t for t, ts in _heartbeats.items() if ts < cutoff]:
                _heartbeats.pop(tid, None)


def get_heartbeat(thread_id: str) -> float | None:
    """Return the monotonic timestamp of the last heartbeat, or None."""
    with _hb_lock:
        return _heartbeats.get(thread_id)


def reset_heartbeat(thread_id: str) -> None:
    """Drop a thread's heartbeat (turn end / tests)."""
    with _hb_lock:
        _heartbeats.pop(thread_id, None)


# ── Incident classification ────────────────────────────────────────────


class IncidentClass:
    """Machine-readable incident classes for the recovery state machine."""

    STALLED = "stalled"
    TRANSIENT_LLM = "transient_llm"
    CONTEXT_OVERFLOW = "context_overflow"
    PANIC = "panic"


def classify_incident(exc: BaseException | None, *, stalled: bool = False) -> str:
    """Map an invoke failure (or stall) to an incident class."""
    if stalled:
        return IncidentClass.STALLED
    if exc is None:
        return IncidentClass.PANIC
    kind = str(getattr(exc, "kind", "") or "")
    if kind == "context_overflow":
        return IncidentClass.CONTEXT_OVERFLOW
    if bool(getattr(exc, "transient", False)):
        return IncidentClass.TRANSIENT_LLM
    name = type(exc).__name__
    if name in ("TimeoutError", "ConnectError", "ReadError", "RemoteProtocolError"):
        return IncidentClass.TRANSIENT_LLM
    return IncidentClass.PANIC


def _reflection_text(incident: str, attempt: int, max_attempts: int) -> str:
    guidance = {
        IncidentClass.STALLED: "the previous attempt made no progress and was aborted",
        IncidentClass.TRANSIENT_LLM: "the model provider had a transient failure",
        IncidentClass.CONTEXT_OVERFLOW: "the context window overflowed — be more selective and concise",
        IncidentClass.PANIC: "an unexpected internal error interrupted the turn",
    }.get(incident, "the turn was interrupted")
    return (
        f"[KAZMA RECOVERY] System note: {guidance}. The turn was rolled back "
        f"to the last checkpoint and is being resumed automatically "
        f"(recovery attempt {attempt}/{max_attempts}). Continue the task from "
        "the state below — do NOT repeat the exact action that failed; change "
        "strategy (different tool, smaller scope, or answer with what you have)."
    )


async def supervised_invoke(
    graph: Any,
    initial_state: dict[str, Any],
    config: dict[str, Any],
    *,
    nonstop_config: Any,
    turn_timeout: float,
) -> dict[str, Any]:
    """Invoke *graph* under the self-healing envelope.

    Args:
        graph:          Compiled LangGraph with a checkpointer (required for
                        resume; without one the first failure propagates).
        initial_state:  Full input state for the FIRST attempt. Retries resume
                        from the checkpoint via ``ainvoke(None, config)``.
        config:         LangGraph config (must carry ``configurable.thread_id``).
        nonstop_config: :class:`NonStopConfig` snapshot.
        turn_timeout:   Per-attempt wall-clock ceiling (seconds; <=0 disables).

    Returns the terminal graph state, or raises the final exception after the
    recovery budget is exhausted.
    """
    thread_id = str(config.get("configurable", {}).get("thread_id", ""))
    max_attempts = max(1, int(nonstop_config.healing.max_recovery_attempts))
    stall_s = float(nonstop_config.watchdog.stall_threshold_seconds)
    base = float(nonstop_config.healing.backoff_base_seconds)
    coeff = float(nonstop_config.healing.backoff_coefficient)
    cap = float(nonstop_config.healing.backoff_max_seconds)

    state_input: dict[str, Any] | None = initial_state
    last_exc: BaseException | None = None

    for attempt in range(0, max_attempts + 1):  # attempt 0 = original run
        if attempt > 0:
            wait = min(base * (coeff ** (attempt - 1)), cap)
            logger.warning(
                "[Watchdog] Recovery attempt %d/%d for thread=%s in %.1fs",
                attempt,
                max_attempts,
                thread_id,
                wait,
            )
            await asyncio.sleep(wait)

        record_heartbeat(thread_id)
        started = time.monotonic()
        # Poll granularity: fine enough to catch stalls near the threshold,
        # coarse enough to stay cheap on healthy turns.
        poll_s = 5.0 if stall_s <= 0 else max(0.1, min(5.0, stall_s / 2.0))
        invoke_task: asyncio.Task = asyncio.create_task(
            graph.ainvoke(state_input, config),
            name=f"supervised-invoke-{thread_id[:8]}",
        )
        try:
            while True:
                done, _ = await asyncio.wait({invoke_task}, timeout=poll_s)
                if done:
                    # propagate result or exception
                    return invoke_task.result()
                # Wall-clock ceiling per attempt
                if turn_timeout > 0 and (time.monotonic() - started) > turn_timeout:
                    invoke_task.cancel()
                    await _settle(invoke_task)
                    raise TimeoutError(
                        f"turn exceeded wall-clock budget ({turn_timeout:.0f}s)"
                    )
                # Stall check (only meaningful once heartbeats exist)
                hb = get_heartbeat(thread_id)
                if stall_s > 0 and hb is not None and (time.monotonic() - hb) > stall_s:
                    logger.error(
                        "[Watchdog] thread=%s stalled (no heartbeat for %.0fs) — cancelling invoke",
                        thread_id,
                        time.monotonic() - hb,
                    )
                    invoke_task.cancel()
                    await _settle(invoke_task)
                    raise _Stalled()
        except _Stalled as stall_exc:
            last_exc = stall_exc
            incident = IncidentClass.STALLED
        except asyncio.CancelledError:
            raise  # outer cancellation (shutdown) — never swallow
        except BaseException as exc:  # noqa: BLE001 — classification decides
            last_exc = exc
            incident = classify_incident(exc)

        # ── Recovery decision ────────────────────────────────────────
        if attempt >= max_attempts:
            logger.error(
                "[Watchdog] thread=%s exhausted %d recovery attempts (%s) — escalating",
                thread_id,
                max_attempts,
                incident,
            )
            break

        # ── Rollback: resume from last durable checkpoint ────────────
        # Purge any dangling tool-call tail so the resumed state is valid,
        # then inject a bounded reflection note for the model.
        try:
            from kazma_core.agent.graph_builder import sanitize_tool_chains
            from kazma_core.summarizer import _normalize_msg

            msgs: list[dict[str, Any]] = []
            try:
                snap = await graph.aget_state(config)
                vals = getattr(snap, "values", None) if snap is not None else None
                if isinstance(vals, dict):
                    msgs = [_normalize_msg(m) for m in vals.get("messages", [])]
            except Exception:
                logger.debug("[Watchdog] aget_state failed — retrying with fresh input", exc_info=True)
                msgs = []
            if msgs:
                msgs = sanitize_tool_chains(msgs)
                msgs = msgs + [
                    {
                        "role": "system",
                        "content": _reflection_text(incident, attempt + 1, max_attempts),
                    }
                ]
                await graph.aupdate_state(config, {"messages": msgs})
                state_input = None  # resume from the (patched) checkpoint
            else:
                # No checkpoint survived (crash before first superstep) —
                # re-run the original input with the reflection appended.
                retry_state = dict(initial_state)
                retry_msgs = [_normalize_msg(m) for m in retry_state.get("messages", [])]
                retry_msgs.append(
                    {
                        "role": "system",
                        "content": _reflection_text(incident, attempt + 1, max_attempts),
                    }
                )
                retry_state["messages"] = retry_msgs
                state_input = retry_state
        except Exception:
            logger.debug("[Watchdog] rollback/patch failed — resuming from checkpoint as-is", exc_info=True)
            state_input = None

    assert last_exc is not None
    raise last_exc


class _Stalled(Exception):
    """Internal: the invoke was cancelled due to heartbeat stall."""


async def _settle(task: asyncio.Task) -> None:
    """Wait briefly for a cancelled invoke task to finish unwinding.

    Prevents 'task was destroyed but it is pending' warnings and lets
    LangGraph checkpoint whatever superstep was in flight.
    """
    try:
        await asyncio.wait({task}, timeout=5.0)
    except Exception:  # noqa: BLE001
        pass
