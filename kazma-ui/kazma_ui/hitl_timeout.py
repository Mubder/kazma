"""HITL approval-timeout watchdog.

The graph-level HITL gate (``tool_worker_node`` → ``interrupt()``) parks the
graph until someone resumes it via ``POST /api/approve/{thread_id}`` (or the
WS ``approve_tool`` message). Unlike the swarm bus gate
(``swarm/safety.py``, 60s), the graph gate previously had **no timeout** —
a missed/dismissed approval deadlocked the turn forever (audit §2.3).

This watchdog closes that gap: it periodically scans for threads paused on a
``hitl_approval`` interrupt, tracks when each was first seen, and — once
``safety.hitl.approval_timeout_seconds`` elapses — auto-resumes the graph
with ``{"approved": False, "reason": "approval timeout"}`` when
``safety.hitl.auto_deny_on_timeout`` is enabled (the default, fail-closed).
When auto-deny is disabled the watchdog only logs, preserving the manual
"wait forever" behaviour for operators who want it.

Design notes:
  * Age is tracked in-memory (first-seen monotonic). On process restart the
    timer restarts for still-pending approvals — a pending approval never
    survives *two* watchdog windows unnoticed, which is acceptable and keeps
    the implementation free of checkpoint-schema coupling.
  * Config is re-read live via ``get_hitl_config()`` so Settings changes take
    effect without a restart (mirrors get_proxy_provider).
  * The watchdog never raises; a scan failure is logged and retried next tick.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from kazma_core.shutdown import is_shutting_down

__all__ = ["start_hitl_timeout_watchdog", "stop_hitl_timeout_watchdog"]

logger = logging.getLogger(__name__)

_SCAN_INTERVAL_SECONDS = 15.0

_watchdog_task: asyncio.Task | None = None
# thread_id → monotonic timestamp when the pending approval was first seen
_first_seen: dict[str, float] = {}


async def _auto_deny(graph: Any, thread_id: str, timeout_s: float) -> None:
    """Resume an expired interrupt with a deny decision."""
    from langgraph.types import Command

    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    # Phase 3: check if the pending interrupt is semantic (clarify/confirm) —
    # those need {tcid: "cancel"}, not {approved: false}.
    from kazma_core.safety.commitment.resume import build_resume_command, read_pending_interrupt

    _intr_payload = await read_pending_interrupt(graph, config)
    _resume_cmd = build_resume_command(
        _intr_payload, approved=False, scope="once",
        reason=(
            f"HITL approval timed out after {int(timeout_s)}s. The operator "
            "did not answer and is probably away from the device. "
            "This is NOT a refusal of this approach. "
            "Retrying the same action with different arguments, a different "
            "tool, or a different path only sends another approval request "
            "to someone who is not there. "
            "Stop now, do not attempt a workaround, and reply saying what "
            "you were about to do and what you need approved."
        ),
        extra={"timed_out": True} if not (_intr_payload and _intr_payload.get("kind") in ("semantic_clarify", "semantic_confirm")) else None,
    )
    if _resume_cmd is None:
        # Nothing pending (already resolved) — nothing to auto-deny.
        return
    logger.warning(
        "[HITL-WD] Approval for thread=%s expired after %.0fs — auto-denying",
        thread_id,
        timeout_s,
    )
    try:
        # Don't auto-deny if a manual resume (SSE/WS approve) is already in
        # flight for this thread — the watchdog's ainvoke(deny) would race
        # the just-clicked approve and could clobber it. The SSE/WS paths
        # register their resume via register_turn/is_turn_running (audit).
        from kazma_ui.active_turns import is_turn_running

        if is_turn_running(thread_id):
            logger.info(
                "[HITL-WD] thread=%s has a turn in flight — skipping auto-deny "
                "(manual resume racing)", thread_id,
            )
            return
    except Exception:
        pass
    try:
        await graph.ainvoke(_resume_cmd, config)
    except Exception:
        logger.exception("[HITL-WD] auto-deny resume failed for thread=%s", thread_id)


async def _watchdog_loop(
    graph_getter: Callable[[], Any],
    checkpointer_getter: Callable[[], Any],
) -> None:
    from kazma_core.safety.hitl import get_hitl_config
    from kazma_ui.hitl_approval import _get_pending_approvals

    logger.info("[HITL-WD] Approval-timeout watchdog started")
    while not is_shutting_down():
        try:
            await asyncio.sleep(_SCAN_INTERVAL_SECONDS)
            if is_shutting_down():
                break

            cfg = get_hitl_config()
            if not cfg.get("enabled"):
                continue
            timeout_s = float(cfg.get("approval_timeout_seconds", 300) or 0)
            auto_deny = bool(cfg.get("auto_deny_on_timeout", True))
            if timeout_s <= 0:
                continue  # timeout disabled

            graph = graph_getter()
            checkpointer = checkpointer_getter()
            if graph is None or checkpointer is None:
                continue

            pending = await _get_pending_approvals(graph, checkpointer)
            now = time.monotonic()
            current_ids = {str(p.get("thread_id")) for p in pending if p.get("thread_id")}

            # Drop threads that resolved (approved/denied/expired) since last tick
            for tid in list(_first_seen):
                if tid not in current_ids:
                    _first_seen.pop(tid, None)

            for item in pending:
                tid = str(item.get("thread_id") or "")
                if not tid:
                    continue
                first = _first_seen.setdefault(tid, now)
                age = now - first
                if age < timeout_s:
                    continue
                # Expired
                _first_seen.pop(tid, None)
                if auto_deny:
                    await _auto_deny(graph, tid, timeout_s)
                else:
                    logger.warning(
                        "[HITL-WD] Approval for thread=%s is %.0fs past the %.0fs "
                        "timeout (auto_deny_on_timeout=false — waiting for a human)",
                        tid,
                        age,
                        timeout_s,
                    )
                    # Re-track so we log at most once per window
                    _first_seen[tid] = now
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[HITL-WD] scan failed — retrying next tick")


def start_hitl_timeout_watchdog(
    graph_getter: Callable[[], Any],
    checkpointer_getter: Callable[[], Any],
) -> asyncio.Task | None:
    """Start the approval-timeout watchdog (idempotent).

    Args:
        graph_getter: callable returning the live compiled graph (or None).
        checkpointer_getter: callable returning the live checkpointer (or None).

    Returns the created task, or the existing one if already running.
    """
    global _watchdog_task
    if _watchdog_task is not None and not _watchdog_task.done():
        return _watchdog_task
    _first_seen.clear()
    _watchdog_task = asyncio.create_task(
        _watchdog_loop(graph_getter, checkpointer_getter),
        name="hitl-timeout-watchdog",
    )
    return _watchdog_task


async def stop_hitl_timeout_watchdog() -> None:
    """Cancel the watchdog task (called during app shutdown)."""
    global _watchdog_task
    task = _watchdog_task
    _watchdog_task = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _first_seen.clear()
