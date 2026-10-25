"""The run supervisor: every 30 seconds, judge every run and tell the owner.

One tick (:func:`tick`):

1. read every kind's runs (``registry.list_runs``, which applies
   ``contract.judge``);
2. for each run in a bad condition -- ``stalled``, ``dead`` (its owner is
   gone) or ``failed`` -- open its incident, or keep the open one current;
3. resolve the open incidents whose run is no longer in that condition (a
   kind that could not be read resolves nothing: an outage is never "fine");
4. apply a runbook's first step where the owner switched one on
   (:mod:`kazma_core.runs.runbooks`), never on unknown effects;
5. send each pending incident once (``notify.deliver``), and one recovery
   note for a stalled or stuck run that moved again.

A verdict never cancels or fails a run by itself. A failure that ended long
before this process started watching is recorded without a message (a
restart is not a reason to page about yesterday).

The loop pages its own failure only after it has been unable to tick for
five minutes, and says when it works again (the acquisition monitor's rule,
2026-10-22: a database restart is not an incident).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from kazma_core.runs.contract import ACTIVE_STATES, Run, RunState, Verdict
from kazma_core.runs.kinds import KINDS

__all__ = ["start", "status", "stop", "tick"]

logger = logging.getLogger(__name__)

TICK_SECONDS = 30.0
#: A failure that ended this long before supervision started is not paged.
BASELINE_GRACE_S = 15 * 60.0
_UNAVAILABLE_AFTER_S = 300.0

_task: asyncio.Task[None] | None = None
_watch_started: float | None = None
_failing_since: float | None = None
_paged_unavailable = False


def _condition_of(run: Run) -> str | None:
    """``stalled``, ``dead``, ``failed`` or None (nothing to tell)."""
    if run.verdict == Verdict.DEAD:
        return "dead"
    if run.verdict == Verdict.STALLED:
        return "stalled"
    if run.verdict == Verdict.FAILED:
        return "failed"
    return None


async def _origin(run: Run) -> dict[str, str]:
    if run.origin:
        return dict(run.origin)
    from kazma_core.runs.registry import adapter_for

    adapter = adapter_for(run.kind)
    finder = getattr(adapter, "origin_for", None)
    if finder is None:
        return {}
    try:
        return await finder(run.native_id)
    except (RuntimeError, OSError, ValueError, KeyError, AttributeError):
        logger.debug("[runs] no chat address for %s", run.run_id, exc_info=True)
        return {}


def _changed(row: dict[str, Any], run: Run) -> bool:
    """Whether a live incident's view of its run is out of date (one write
    per change, not one per pass)."""
    from kazma_core.runs.contract import INCIDENT_ACTIONS

    return (row.get("title") != run.title[:200] or row.get("detail") != run.detail[:500]
            or row.get("run_state") != str(run.state) or row.get("effects") != run.effects
            or row.get("link") != run.link or row.get("actions") != [*run.actions, *INCIDENT_ACTIONS])


def _quiet_reason(run: Run, condition: str, watch_started: float) -> str:
    """Why this incident sends no message, or "" when it should."""
    announced = KINDS[run.kind].announced_by.get(condition) or run.announced_by
    if announced:
        return f"announced by {announced}"
    if condition == "failed" and run.ended_at is not None and run.ended_at < watch_started - BASELINE_GRACE_S:
        return "ended before this start of Kazma"
    return ""


async def tick(*, now: float | None = None) -> dict[str, int]:
    """One pass. Returns counts (opened, resolved, sent, automatic)."""
    global _watch_started
    from kazma_core.runs import notify, runbooks
    from kazma_core.runs.incidents import get_incident_store
    from kazma_core.runs.registry import list_runs

    stamp = time.time() if now is None else now
    if _watch_started is None:
        _watch_started = stamp
    store = get_incident_store()
    listing = await list_runs(now=stamp, mark_handled=False)
    live = await asyncio.to_thread(store.live)
    decided = await asyncio.to_thread(store.decided_keys)
    live_by_key = {(row["run_id"], row["condition"]): row for row in live}
    counts = {"opened": 0, "resolved": 0, "sent": 0, "automatic": 0}
    bad: set[tuple[str, str]] = set()
    runs_by_id = {r.run_id: r for r in listing.runs}

    for run in listing.runs:
        condition = _condition_of(run)
        if condition is None:
            continue
        bad.add((run.run_id, condition))
        # Known already: write only when the run's view changed. A failed run
        # stays listed a day, and one write transaction per pass per run is
        # what the soak caught (2026-10-25).
        known = live_by_key.get((run.run_id, condition))
        if known is not None:
            if known["state"] == "open" and _changed(known, run):
                await asyncio.to_thread(store.refresh, known["id"], run, now=stamp)
            continue
        if (run.run_id, condition, run.version) in decided:
            continue  # the owner already decided about this failure
        quiet = _quiet_reason(run, condition, _watch_started)
        opened = await asyncio.to_thread(
            store.open_incident, run, condition, origin=await _origin(run),
            notify="suppressed" if quiet else "pending", notify_detail=quiet, now=stamp)
        if opened is None:
            continue  # the owner already decided about this failure
        row, created = opened
        if not created:
            if _changed(row, run):
                await asyncio.to_thread(store.refresh, row["id"], run, now=stamp)
            continue
        counts["opened"] += 1
        logger.warning("[runs] incident %s: %s %s (%s)%s", row["id"], run.run_id, condition,
                       run.native_state, f" -- quiet: {quiet}" if quiet else "")
        if await runbooks.apply(row, run):
            counts["automatic"] += 1

    for row in live:
        key = (row["run_id"], row["condition"])
        if row["state"] != "open" or key in bad or row["kind"] in listing.errors:
            continue
        run = runs_by_id.get(row["run_id"])
        if run is None:
            reason = "the run is no longer listed"
        elif run.state in ACTIVE_STATES:
            reason = "the run is making progress again"
        else:
            reason = f"the run is {run.state}"
        if not await asyncio.to_thread(store.resolve, row["id"], reason=reason, now=stamp):
            continue
        counts["resolved"] += 1
        logger.info("[runs] incident %s resolved: %s", row["id"], reason)
        moved_on = run is not None and (run.state in ACTIVE_STATES or run.state == RunState.DONE)
        if (row["condition"] in ("stalled", "dead") and moved_on
                and row.get("notify") in ("delivered", "uncertain")):
            await notify.send_recovery(row, finished=run is not None and run.state == RunState.DONE)

    for row in await asyncio.to_thread(store.pending_notifications):
        outcome = await notify.deliver(row)
        if outcome in ("delivered", "sending"):
            counts["sent"] += 1
    return counts


def _note_pass(error: BaseException | None, *, now: float | None = None) -> str:
    """Record one pass's outcome; page the supervisor's own outage only after
    :data:`_UNAVAILABLE_AFTER_S` without a good pass, and its recovery once.
    Returns what it did: ``ok``, ``failing``, ``paged`` or ``recovered``."""
    global _failing_since, _paged_unavailable
    from kazma_core.observability.ops_alerts import alert

    stamp = time.time() if now is None else now
    if error is None:
        recovered = _paged_unavailable
        if recovered:
            alert("runs.supervisor_recovered", "Run supervision works again",
                  "Kazma checks its running work again.", severity="info", cooldown_s=0)
        _failing_since, _paged_unavailable = None, False
        return "recovered" if recovered else "ok"
    _failing_since = _failing_since or stamp
    if not _paged_unavailable and stamp - _failing_since >= _UNAVAILABLE_AFTER_S:
        _paged_unavailable = True
        alert("runs.supervisor_unavailable", "Run supervision is not working",
              f"Kazma has not checked its running work for {int((stamp - _failing_since) // 60)} min: "
              f"{type(error).__name__}. Stuck or failed tasks are not being reported.",
              severity="error", cooldown_s=0)
        return "paged"
    return "failing"


async def _loop() -> None:
    from kazma_core.runs.incidents import get_incident_store
    from kazma_core.shutdown import is_shutting_down

    try:
        await asyncio.to_thread(get_incident_store().recover)
    except (OSError, RuntimeError) as exc:
        logger.warning("[runs] incident recovery at start failed: %s", exc)
    logger.info("[runs] run supervisor started (every %ds)", int(TICK_SECONDS))
    while not is_shutting_down():
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- the loop must outlive any one bad pass
            logger.warning("[runs] supervisor pass failed: %s", exc, exc_info=True)
            _note_pass(exc)
        else:
            _note_pass(None)
        await asyncio.sleep(TICK_SECONDS)


def start() -> None:
    """Start the loop (app startup). Not under pytest (``background_schedulers_enabled``)."""
    global _task
    from kazma_core.memory.worker_bootstrap import background_schedulers_enabled

    if _task is not None and not _task.done():
        return
    if not background_schedulers_enabled():
        logger.info("[runs] run supervisor not started (background schedulers are off)")
        return
    from kazma_core.background import spawn_background

    _task = spawn_background(_loop(), name="run-supervisor", never_completes=True)


async def stop() -> None:
    """Stop the loop (app shutdown), bounded."""
    global _task
    task, _task = _task, None
    if task is None:
        return
    from kazma_core.bounded_cancel import cancel_and_wait

    await cancel_and_wait(task, what="run supervisor", timeout=5.0)


def status() -> dict[str, Any]:
    """For readiness and the Runs view: is the supervisor watching?"""
    return {"running": _task is not None and not _task.done(), "watching_since": _watch_started,
            "failing_since": _failing_since}
