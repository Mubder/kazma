"""Durable swarm execution -- Temporal when configured, in-process otherwise.

The swarm *planner* (DAG, HITL, breakers, phonebook) stays in Kazma. A task
admitted by the engine (its RUNNING row written) runs through one Temporal
workflow per run (``KazmaSwarmRun``, workflow id ``kazma-swarm-<task id>``,
``-g<generation>`` for a resumed run), whose one activity runs the engine's
own body of the run (``SwarmEngine._run_admitted``, or ``_resume`` for an
approved pipeline) in THIS process (Swarm tracker SQ03, SW-E16, qualified on
Temporal's time-skipping test server 2026-10-15). The rules:

* **The work runs only where it was admitted.** The activity is scheduled on
  a task queue only this process polls (``activity_queue``: the workflow
  queue and a tag drawn at import), and runs only for a run registered here
  and still waiting (``_RUNS``). Another Kazma, or this one after a
  restart, never runs it: a queue nobody polls times the activity out
  before it starts, and a run that was abandoned refuses it. The work runs
  in the run's own context (its effect tracker, tenant, call authority), so
  an interrupted run records what it changed exactly as in process.
* **One deadline owner: the engine.** The activity's body applies the
  engine's deadline (``_run_admitted``'s ``wait_for`` for a dispatch, the
  pattern's per-step timeouts) and the watchdog stops it at
  ``task_budget`` + ``WATCHDOG_MARGIN_S``; Temporal's limits are taken from
  that same budget and sit after the engine's own end of the run
  (``activity_timeouts``). A Temporal limit that does fire (the engine's
  machinery failed, or the heartbeat stopped) stops the work the same way a
  stop does and is recorded the same way: ``timeout`` / ``deadline`` for
  its time limit, ``failed`` / ``worker_lost`` for a lost heartbeat,
  ``cancelled`` / ``temporal_cancel`` for a cancel made through Temporal,
  each with ``execution_outcome`` from the run's effect tracker.
* **One retry owner: the engine.** The activity is never retried
  (``maximum_attempts=1``): a worker's own retries run inside it, under the
  engine's rules (§30 G: only while no tool that changes things has run,
  ``retry_safe``). An attempt Temporal lost may have changed things and
  could only run here anyway.
* **Every ending is the engine's.** The body records the ending (stored,
  streamed, in the history); the workflow's result is a short summary for
  Temporal's history (no prompt or output leaves the process). A workflow
  that fails or a result that is lost is settled from this process
  (``_settle``): the body's own ending if it ran -- stopped first, under the
  cause the workflow names, when Temporal ended the run (``_cause_of``) --
  and ``none_invoked`` if it never started here (it is then refused if it
  arrives). A run whose work ended waits at most ``WORKFLOW_CLOSE_WAIT_S``
  for its workflow (``_await_workflow``): a Temporal that went away holds
  no ending hostage.
* **A stop stops the work.** A cancel, the watchdog, a shutdown or a caller
  that stopped waiting cancels the body here (Temporal need not be
  reachable), then asks Temporal to cancel the workflow (best effort).
* **A heartbeat every** ``HEARTBEAT_EVERY_S``: Temporal calls the worker
  lost after ``HEARTBEAT_TIMEOUT_S`` of silence (a process that died). The
  workflow worker keeps no cache, so no sticky queue: a restarted Kazma
  runs that workflow's next task at once and it closes. The activity is
  cancelled with TRY_CANCEL (the SDK cannot evict a workflow that waits on
  a cancel, and every task is evicted with no cache); the run, not the
  workflow, waits for the work to stop.

Default: in-process asyncio (one trusted operator).
Opt-in: ``KAZMA_TEMPORAL_HOST`` (or ``TEMPORAL_ADDRESS``) + the ``durable``
extra (``pip install -e ".[durable]"`` in the Kazma folder). Kill-switch
``KAZMA_TEMPORAL=0``. Required (no fallback): ``KAZMA_TEMPORAL_REQUIRED=1``.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

from kazma_core.install_hint import extra_install_hint

logger = logging.getLogger(__name__)

__all__ = [
    "TASK_QUEUE",
    "activity_queue",
    "activity_timeouts",
    "durable_enabled",
    "durable_host",
    "durable_ready",
    "durable_required",
    "in_durable_activity",
    "run_activity_payload",
    "run_via_durable",
    "start_temporal_worker",
    "stop_temporal_worker",
]

TASK_QUEUE = "kazma-swarm"

#: How often the activity heartbeats (and so how soon a cancel made through
#: Temporal reaches it: the activity worker sends heartbeats no less often).
HEARTBEAT_EVERY_S = 2.0
#: Silence after which Temporal calls the activity's worker lost.
HEARTBEAT_TIMEOUT_S = 20.0
#: How long the activity may wait for this process to take it.
SCHEDULE_TO_START_S = 60.0
#: Temporal's time limit for a run, past the engine's own end of it (the
#: watchdog plus its stop grace): a backstop that fires only when the
#: engine's own machinery did not end the run.
BACKSTOP_AFTER_S = 60.0
#: Temporal needs a limit; a task with no deadline gets this one.
NO_DEADLINE_LIMIT_S = 30 * 24 * 3600.0
#: The workflow's own limit, past the activity's.
WORKFLOW_SLACK_S = 120.0
#: How long a run whose work ended here waits for its workflow to close.
WORKFLOW_CLOSE_WAIT_S = 10.0

#: The failure types the activity raises (never retried: see the module).
NOT_RUN = "KazmaNotRun"

_in_durable_activity: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "kazma_durable_activity", default=False
)
#: Drawn once per process: the activity queue only this process polls.
_BOOT_TAG = uuid.uuid4().hex[:12]


@dataclass
class _DurableRun:
    """A run waiting on its workflow, in the process that admitted it."""

    engine: Any
    task: Any
    started: float
    task_span: Any
    context: contextvars.Context
    workflow_id: str
    resume: tuple[Any, Any, Any] | None = None
    state: str = "waiting"  # waiting | running | done | abandoned
    body: asyncio.Task[Any] | None = None
    result: Any = None
    done: asyncio.Event = field(default_factory=asyncio.Event)


#: The context the activity runs the work in: the run's own (its effect
#: tracker, tenant, call authority), copied where the run waits.
_run_context = contextvars.copy_context

_RUNS: dict[str, _DurableRun] = {}
_WORKERS: dict[str, tuple[Any, asyncio.Task[Any]]] = {}
_CLIENTS: dict[tuple[str, str], tuple[Any, Any]] = {}


def durable_host() -> str:
    return (
        os.environ.get("KAZMA_TEMPORAL_HOST")
        or os.environ.get("TEMPORAL_ADDRESS")
        or ""
    ).strip()


def durable_enabled() -> bool:
    raw = (os.environ.get("KAZMA_TEMPORAL") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    if not durable_host():
        return False
    if raw in ("1", "true", "on", "yes"):
        return True
    # Host set => on (unless kill-switched above)
    return True


def durable_required() -> bool:
    return (os.environ.get("KAZMA_TEMPORAL_REQUIRED") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def in_durable_activity() -> bool:
    return bool(_in_durable_activity.get())


def _sdk_available() -> bool:
    try:
        import temporalio  # noqa: F401

        return True
    except ImportError:
        return False


def _temporal_errors() -> tuple[type[BaseException], ...]:
    """What a call to Temporal raises when it cannot reach the server, or the
    server refuses: the SDK's ``RPCError``, the bridge's ``RuntimeError`` (a
    connect), a socket's ``OSError``, a timeout."""
    from temporalio.service import RPCError

    return (RPCError, RuntimeError, OSError, TimeoutError)


def _namespace() -> str:
    return (os.environ.get("KAZMA_TEMPORAL_NAMESPACE") or "default").strip() or "default"


def _workflow_queue() -> str:
    """The task queue of the workflows (any Kazma worker may run their code:
    it is deterministic and changes nothing)."""
    return (os.environ.get("KAZMA_TEMPORAL_QUEUE") or TASK_QUEUE).strip() or TASK_QUEUE


def activity_queue() -> str:
    """The task queue of this process's activities: only this process polls
    it, so the work runs only where it was admitted."""
    return f"{_workflow_queue()}-run-{_BOOT_TAG}"


def worker_running() -> bool:
    """Whether this process's Temporal workers are polling."""
    return all(name in _WORKERS and not _WORKERS[name][1].done() for name in ("workflows", "activities"))


def durable_ready() -> tuple[bool, str]:
    """Whether a task can run through Temporal here now, and why not."""
    if not durable_enabled():
        return False, "Required Temporal is disabled or has no host."
    if not _sdk_available():
        return False, ("Temporal host is set but temporalio is not installed "
                       f"(the durable extra: {extra_install_hint('durable')}).")
    if not worker_running():
        return False, (f"Temporal's worker is not running in this process (host {durable_host()}): "
                       "it could not connect at start, or it stopped.")
    return True, ""


def _workflow_id_for(task: Any) -> str:
    """One workflow per run: the task's id, and its generation past the first
    (an approved pipeline's resume is a run of its own)."""
    generation = int(getattr(task, "generation", 0) or 0)
    base = f"kazma-swarm-{task.id}"
    return base if generation <= 1 else f"{base}-g{generation}"


def activity_timeouts(task: Any) -> dict[str, float]:
    """Temporal's limits for a run of *task*, in seconds, from the engine's
    own budget (``engine.task_budget``): the run's time limit sits after the
    watchdog's stop and its grace, so the engine ends the run first."""
    from kazma_core.swarm.engine import _STOP_GRACE_S, WATCHDOG_MARGIN_S, task_budget

    budget = task_budget(task)
    if budget is None:
        start_to_close = NO_DEADLINE_LIMIT_S
    else:
        timeout, steps = budget
        start_to_close = timeout * steps + WATCHDOG_MARGIN_S + _STOP_GRACE_S + BACKSTOP_AFTER_S
    schedule_to_close = SCHEDULE_TO_START_S + start_to_close
    return {
        "start_to_close": start_to_close,
        "schedule_to_start": SCHEDULE_TO_START_S,
        "schedule_to_close": schedule_to_close,
        "heartbeat": HEARTBEAT_TIMEOUT_S,
        "workflow": schedule_to_close + WORKFLOW_SLACK_S,
    }


async def _client() -> Any:
    """One Temporal client per host and namespace, per event loop."""
    from temporalio.client import Client

    loop = asyncio.get_running_loop()
    key = (durable_host(), _namespace())
    cached = _CLIENTS.get(key)
    if cached is not None and cached[0] is loop and not loop.is_closed():
        return cached[1]
    client = await Client.connect(key[0], namespace=key[1])
    _CLIENTS[key] = (loop, client)
    return client


def _not_run(engine: Any, task: Any, started: float, task_span: Any, error: str,
             *, workflow_id: str | None = None) -> Any:
    """A durable dispatch that never ran (nothing was submitted, or it never
    started here and now cannot).

    Called after admission (the task is in flight, its row RUNNING), it is
    finalized like any ending: the row, the admission slot and the task's
    stream are released (Swarm tracker SQ04). A bare result left all three
    held until the watchdog reaped the task as a timeout -- minutes of
    "running" later, with the wrong reason. Before admission it is a plain
    refusal, as the capacity refusal is."""
    from time import perf_counter

    from kazma_core.swarm.task import TaskResult

    metadata: dict[str, Any] = {"durable": "temporal", "execution_outcome": "none_invoked"}
    if workflow_id:
        metadata["workflow_id"] = workflow_id
    if task.id not in getattr(engine, "_active_tasks", {}):
        return TaskResult(task_id=task.id, status="failed", error=error, metadata=metadata)
    if task_span is not None:
        engine._tracing_emitter.end_span(task_span, status="error", status_msg=error)
    return engine._finalize_task(
        task, worker_results=[], status="failed", error=error,
        duration_seconds=perf_counter() - started,
        metadata=metadata,
    )


async def _run_here(engine: Any, task: Any, started: float, task_span: Any,
                    resume: tuple[Any, Any, Any] | None) -> Any:
    """The run in process, as with no Temporal at all."""
    if resume is not None:
        return await engine._resume(task, *resume)
    return await engine._run_admitted(task, started, task_span)


def _mark(result: Any, workflow_id: str) -> Any:
    """The engine's own record of the run, marked with its workflow."""
    if result is not None and hasattr(result, "metadata"):
        result.metadata = {**(result.metadata or {}), "durable": "temporal", "workflow_id": workflow_id}
    return result


async def run_via_durable(
    engine: Any,
    task: Any,
    started: float,
    task_span: Any,
    *,
    resume: tuple[Any, Any, Any] | None = None,
) -> Any:
    """Run an admitted task through a Temporal workflow and settle its ending.

    *resume* is ``(next_step, worker_results, blackboard_data)`` for an
    approved pipeline. Falling back to the in-process run is allowed only
    before anything was submitted, and only when Temporal is not required.
    """
    if in_durable_activity():
        return await _run_here(engine, task, started, task_span, resume)
    ready, problem = durable_ready()
    if not ready:
        if durable_required():
            logger.error("[durable] %s The task is not run.", problem)
            return _not_run(engine, task, started, task_span, problem)
        if durable_enabled():
            logger.warning("[durable] %s Falling back to in-process.", problem)
        return await _run_here(engine, task, started, task_span, resume)

    workflow_id = _workflow_id_for(task)
    entry = _DurableRun(
        engine=engine, task=task, started=started, task_span=task_span,
        context=_run_context(), workflow_id=workflow_id, resume=resume,
    )
    current = _RUNS.get(task.id)
    if current is not None and current.state in ("waiting", "running"):
        # The engine runs one run per task id (_run_owned): a second one here
        # is a defect, and its work must not start beside the first.
        logger.warning("[durable] task '%s' already has a durable run waiting here; not run again", task.id)
        return _not_run(engine, task, started, task_span,
                        "This task already has a run waiting on Temporal; it was not run again.")
    _RUNS[task.id] = entry
    handle = None
    try:
        try:
            client = await _client()
        except _temporal_errors() as exc:
            entry.state = "abandoned"
            if durable_required():
                logger.error("[durable] Temporal failed (required): %s", exc)
                return _not_run(engine, task, started, task_span, f"Temporal durable dispatch failed: {exc}")
            logger.warning("[durable] Temporal failed (%s) -- in-process fallback", exc)
            return await _run_here(engine, task, started, task_span, resume)
        handle = await _start(client, entry)
        lost = await _await_workflow(handle, entry)
        return await _settle(entry, lost)
    except asyncio.CancelledError:
        return await _stopped(entry, handle)
    finally:
        if _RUNS.get(task.id) is entry:
            _RUNS.pop(task.id, None)


async def _start(client: Any, entry: _DurableRun) -> Any:
    """Start the run's workflow; a lost acknowledgement follows the workflow
    by its id (never a second run: the id is refused once used)."""
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from kazma_core.swarm.durable_temporal import KazmaSwarmRun

    task = entry.task
    timeouts = activity_timeouts(task)
    task.metadata = dict(task.metadata or {})
    task.metadata.update(durable="temporal", workflow_id=entry.workflow_id)
    payload = {
        "task_id": task.id,
        "generation": int(getattr(task, "generation", 0) or 0),
        "type": getattr(getattr(task, "type", None), "value", str(getattr(task, "type", ""))),
        "resume": entry.resume is not None,
        "activity_queue": activity_queue(),
        "timeouts": timeouts,
    }
    from datetime import timedelta

    try:
        return await client.start_workflow(
            KazmaSwarmRun.run,
            payload,
            id=entry.workflow_id,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            task_queue=_workflow_queue(),
            execution_timeout=timedelta(seconds=timeouts["workflow"]),
        )
    except WorkflowAlreadyStartedError:
        return client.get_workflow_handle(entry.workflow_id)
    except _temporal_errors():
        logger.warning("[durable] start of workflow %s not acknowledged; following it by its id",
                       entry.workflow_id, exc_info=True)
        return client.get_workflow_handle(entry.workflow_id)


async def _await_workflow(handle: Any, entry: _DurableRun) -> BaseException | None:
    """Wait for the run's workflow to close, or for its work to end here.

    The work's own ending is the record: once it is in, the workflow gets
    ``WORKFLOW_CLOSE_WAIT_S`` to close and is not waited on past that (a
    Temporal that went away holds no ending hostage). Returns the
    workflow's failure (or a lost result), None when it closed cleanly or
    was not waited on."""
    result = asyncio.ensure_future(handle.result())
    ended_here = asyncio.ensure_future(entry.done.wait())
    try:
        await asyncio.wait({result, ended_here}, return_when=asyncio.FIRST_COMPLETED)
        if not result.done():
            await asyncio.wait({result}, timeout=WORKFLOW_CLOSE_WAIT_S)
        if not result.done():
            # Temporal's history lacks the run's ending (the task store has it).
            logger.warning("[durable] workflow %s has not closed %.0fs after its run ended here; the run's "
                           "ending is recorded here", entry.workflow_id, WORKFLOW_CLOSE_WAIT_S)
            from kazma_core.metrics import record_swarm_failure

            record_swarm_failure("durable_workflow")
            return None
        return result.exception()
    finally:
        for waiting in (result, ended_here):
            if not waiting.done():
                waiting.cancel()


async def _settle(entry: _DurableRun, lost: BaseException | None) -> Any:
    """The run's ending, once its workflow has ended (or Temporal was lost),
    from what happened in this process."""
    engine, task = entry.engine, entry.task
    if entry.state == "waiting":
        # It never started here; from now on it is refused if it arrives.
        entry.state = "abandoned"
        why = _describe(lost) if lost is not None else "its workflow ended without running it"
        logger.warning("[durable] task '%s' was not run: %s", task.id, why)
        return _not_run(engine, task, entry.started, entry.task_span,
                        f"Temporal did not run this task ({why}); nothing ran here, and it will not run.",
                        workflow_id=entry.workflow_id)
    if entry.state == "running":
        cause = _cause_of(lost)
        if cause is not None and entry.body is not None:
            # Temporal ended the run (its limit, a lost heartbeat, a cancel or
            # terminate made through it): stop the work here, under that cause.
            logger.warning("[durable] workflow %s ended (%s) while its work still runs here: stopping "
                           "the work (%s)", entry.workflow_id, _describe(lost), cause[2])
            engine._interrupts.setdefault(task.id, cause)
            entry.body.cancel()
            if not await _wait_done(entry, _stop_grace()):
                engine._record_unstoppable(task.id)
                if getattr(task, "result", None) is not None:
                    return _mark(task.result, entry.workflow_id)
        else:
            if lost is not None:
                logger.warning("[durable] workflow %s: %s, while its work still runs here: waiting for the "
                               "work's own ending", entry.workflow_id, _describe(lost))
            await asyncio.shield(entry.done.wait())  # the run's own stop still stops it (_stopped)
    if lost is not None:
        logger.info("[durable] workflow %s: %s; the run's ending was recorded here",
                    entry.workflow_id, _describe(lost))
    if entry.result is not None:
        return _mark(entry.result, entry.workflow_id)
    # The body raised past every handler: the ending is recorded here.
    from time import perf_counter

    return engine._finalize_task(task, worker_results=[], status="failed",
                                 error="The durable run ended without an ending; see the log.",
                                 duration_seconds=perf_counter() - entry.started,
                                 metadata={"durable": "temporal", "workflow_id": entry.workflow_id})


async def _wait_done(entry: _DurableRun, timeout: float) -> bool:
    """Whether the run's work ended here (its ending recorded) within *timeout*."""
    if entry.done.is_set():
        return True
    try:
        await asyncio.wait_for(asyncio.shield(entry.done.wait()), timeout)
    except TimeoutError:
        return False
    return True


def _cause_of(lost: BaseException | None) -> tuple[str, str, str] | None:
    """The interruption a closed workflow names, as the engine records one;
    None when the workflow did not close (Temporal went away: the work goes
    on here)."""
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ActivityError, TerminatedError, TimeoutType
    from temporalio.exceptions import CancelledError as TemporalCancelled
    from temporalio.exceptions import TimeoutError as TemporalTimeout

    if not isinstance(lost, WorkflowFailureError):
        return None
    cause = lost.cause
    if isinstance(cause, ActivityError):
        cause = cause.cause
    if isinstance(cause, TemporalTimeout):
        if cause.type == TimeoutType.HEARTBEAT:
            return ("failed", "Temporal stopped hearing from this run (its heartbeat timed out) and ended it.",
                    "worker_lost")
        return ("timeout", "Task timed out: Temporal's time limit for the run passed.", "deadline")
    if isinstance(cause, (TemporalCancelled, TerminatedError)):
        return ("cancelled", "Cancelled through Temporal.", "temporal_cancel")
    return ("failed", f"Temporal ended the run: {_describe(lost)}", "temporal_failed")


async def _stopped(entry: _DurableRun, handle: Any) -> Any:
    """The run was stopped (a cancel, the watchdog, a shutdown, a caller that
    stopped waiting): stop the work here first -- Temporal need not be
    reachable -- then ask Temporal to cancel the workflow."""
    engine, task = entry.engine, entry.task
    if entry.state == "waiting":
        entry.state = "abandoned"
    elif entry.state == "running" and entry.body is not None:
        entry.body.cancel()
        try:
            await _wait_done(entry, _stop_grace())
        except asyncio.CancelledError:
            pass  # stopped again: settled all the same, from what is known
    if handle is not None:
        from kazma_core.background import spawn_background

        spawn_background(_cancel_workflow(handle, entry.workflow_id),
                         name=f"swarm-durable-cancel:{task.id}")
    if entry.result is not None:
        return _mark(entry.result, entry.workflow_id)
    if entry.state == "running":
        # The work did not stop within the grace: its ending is recorded now,
        # its outcome unknown (as for an in-process run that would not stop).
        engine._record_unstoppable(task.id)
        if getattr(task, "result", None) is not None:
            return _mark(task.result, entry.workflow_id)
    raise asyncio.CancelledError


def _stop_grace() -> float:
    from kazma_core.swarm.engine import _STOP_GRACE_S

    return _STOP_GRACE_S


async def _cancel_workflow(handle: Any, workflow_id: str) -> None:
    try:
        await asyncio.wait_for(handle.cancel(), timeout=10.0)
    except _temporal_errors() as exc:  # best effort: the work here has already stopped
        logger.info("[durable] could not ask Temporal to cancel workflow %s: %s", workflow_id, exc)


def _describe(exc: BaseException | None) -> str:
    """A failure as Temporal reported it, causes included, in one line."""
    parts: list[str] = []
    seen = 0
    while exc is not None and seen < 4:
        text = str(exc) or type(exc).__name__
        parts.append(f"{type(exc).__name__}: {text}" if text != type(exc).__name__ else text)
        exc = getattr(exc, "cause", None) or exc.__cause__
        seen += 1
    return " <- ".join(parts)[:300]


# ── the activity ─────────────────────────────────────────────────────────


async def run_activity_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """The Temporal activity: the registered run's own body, here.

    Refused (never retried) unless the run that admitted the task is waiting
    in this process under this workflow."""
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    task_id = str(payload.get("task_id") or "")
    info = activity.info()
    entry = _RUNS.get(task_id)
    reason = ""
    if entry is None:
        reason = "the run that admitted it is not in this process"
    elif entry.workflow_id != info.workflow_id:
        reason = f"it belongs to workflow {entry.workflow_id}, not {info.workflow_id}"
    elif entry.state != "waiting":
        reason = f"its run is {entry.state}"
    if reason or entry is None:
        logger.warning("[durable] activity for task '%s' (workflow %s) not run: %s",
                       task_id, info.workflow_id, reason)
        raise ApplicationError(f"Not run: {reason}.", type=NOT_RUN, non_retryable=True)
    entry.state = "running"
    loop = asyncio.get_running_loop()
    body = loop.create_task(_run_body(entry), name=f"swarm-durable:{task_id}", context=entry.context.copy())
    entry.body = body
    body.add_done_callback(lambda finished, e=entry: _body_done(e, finished))
    beat = loop.create_task(_heartbeat(entry), name=f"swarm-durable-heartbeat:{task_id}")
    try:
        try:
            result = await asyncio.shield(body)
        except asyncio.CancelledError:
            if not body.done():
                # Temporal stopped the activity, not Kazma. A cancel asked of
                # the activity, or a worker shutdown, is named here; one the
                # activity cannot read (it timed out, or its workflow closed:
                # the server no longer knows it) is named by the run, which
                # hears the workflow's outcome and stops the work (_settle).
                # Either way the work stops and its run records the ending.
                if not _note_temporal_stop(entry) and not await _wait_done(
                        entry, _stop_grace() + WORKFLOW_CLOSE_WAIT_S):
                    entry.engine._interrupts.setdefault(
                        entry.task.id, ("cancelled", "Stopped through Temporal.", "temporal_cancel"))
                if not body.done():
                    body.cancel()
                    await asyncio.wait({body}, timeout=_stop_grace())
            raise
        return _summary(task_id, result)
    finally:
        beat.cancel()


async def _run_body(entry: _DurableRun) -> Any:
    _in_durable_activity.set(True)  # this task's own context (a copy of the run's)
    return await _run_here(entry.engine, entry.task, entry.started, entry.task_span, entry.resume)


def _body_done(entry: _DurableRun, body: asyncio.Task[Any]) -> None:
    entry.state = "done"
    if not body.cancelled() and body.exception() is None:
        entry.result = body.result()
    elif not body.cancelled():
        logger.warning("[durable] the run of task '%s' raised past its handlers", entry.task.id,
                       exc_info=body.exception())
    entry.done.set()


async def _heartbeat(entry: _DurableRun) -> None:
    """Heartbeat with what the run has changed so far (Temporal's history
    shows it; the engine's record is the run's own)."""
    from temporalio import activity

    while True:
        tracker = entry.engine._effects.get(entry.task.id) if hasattr(entry.engine, "_effects") else None
        activity.heartbeat({
            "invoked": bool(getattr(tracker, "invoked", False)),
            "tools": list(getattr(tracker, "tools", None) or [])[:8],
        })
        await asyncio.sleep(HEARTBEAT_EVERY_S)


def _note_temporal_stop(entry: _DurableRun) -> bool:
    """Name the run's interruption when the activity can read it: a cancel
    asked of it, or the worker shutting down (a stop Kazma made itself has
    named its cause already and wins). False when it cannot (it timed out,
    or its workflow closed: the run names it from the workflow's outcome)."""
    from temporalio import activity

    details = getattr(activity, "cancellation_details", lambda: None)()  # temporalio >= 1.11
    if details is not None and details.worker_shutdown:
        cause = ("cancelled", "Stopped: Kazma shut down.", "shutdown")
    elif details is not None and details.cancel_requested:
        cause = ("cancelled", "Cancelled through Temporal.", "temporal_cancel")
    else:
        logger.info("[durable] the activity of task '%s' is gone from Temporal (%s): its run names why",
                    entry.task.id, details)
        return False
    logger.warning("[durable] Temporal stopped the run of task '%s' (%s): %s", entry.task.id, cause[2], details)
    entry.engine._interrupts.setdefault(entry.task.id, cause)
    return True


def _summary(task_id: str, result: Any) -> dict[str, Any]:
    """What Temporal's history keeps of the run: its ending, never its
    prompt or output (the task store holds the result)."""
    metadata = dict(getattr(result, "metadata", None) or {})
    return {
        "task_id": task_id,
        "status": getattr(result, "status", None),
        "error": (getattr(result, "error", None) or "")[:500],
        "execution_outcome": metadata.get("execution_outcome"),
        "interrupted_by": metadata.get("interrupted_by"),
    }


async def run_legacy_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """The activity of a workflow an older build started (``KazmaSwarmTask``):
    its run is gone (any upgrade is a restart, and the next start recorded
    its row), so it is refused, never run."""
    from temporalio.exceptions import ApplicationError

    task_id = str((payload or {}).get("task_id") or "")
    logger.warning("[durable] activity of task '%s' from an older build's workflow not run", task_id)
    raise ApplicationError("Not run: started by an older build; its run is gone.", type=NOT_RUN,
                           non_retryable=True)


# ── the workers ──────────────────────────────────────────────────────────


async def start_temporal_worker() -> None:
    """Boot this process's Temporal workers. Fail-open -- never blocks
    Kazma's boot; a dispatch then falls back, or is refused when Temporal is
    required (``durable_ready``)."""
    if not durable_enabled():
        return
    if not _sdk_available():
        logger.info(
            "[durable] Temporal host set but temporalio missing -- "
            "in-process swarm only (the durable extra: %s)",
            extra_install_hint("durable"),
        )
        return
    if worker_running():
        return
    try:
        from datetime import timedelta

        from temporalio.worker import Worker

        from kazma_core.background import spawn_background
        from kazma_core.swarm.durable_temporal import (
            KazmaSwarmRun,
            KazmaSwarmTask,
            kazma_swarm_dispatch,
            kazma_swarm_run,
        )

        client = await _client()
        # No workflow cache, so no sticky queue: a workflow's next task goes
        # to the shared workflow queue, where a restarted Kazma takes it at
        # once. With a cache it went to the dead process's sticky queue, and
        # the run's lost worker was settled only once the server gave up on
        # that queue (never, on the test server: measured 2026-10-15). The
        # workflow is one activity: replaying it costs nothing.
        flows = Worker(client, task_queue=_workflow_queue(), workflows=[KazmaSwarmRun, KazmaSwarmTask],
                       activities=[kazma_swarm_dispatch], max_cached_workflows=0)
        acts = Worker(client, task_queue=activity_queue(), activities=[kazma_swarm_run],
                      max_heartbeat_throttle_interval=timedelta(seconds=HEARTBEAT_EVERY_S),
                      default_heartbeat_throttle_interval=timedelta(seconds=HEARTBEAT_EVERY_S))
        _WORKERS["workflows"] = (flows, spawn_background(flows.run(), name="kazma-temporal-workflows",
                                                         never_completes=True))
        _WORKERS["activities"] = (acts, spawn_background(acts.run(), name="kazma-temporal-activities",
                                                         never_completes=True))
        logger.info("[durable] Temporal workers started queues=%s,%s ns=%s",
                    _workflow_queue(), activity_queue(), _namespace())
    except Exception:
        logger.warning("[durable] Temporal worker start failed", exc_info=True)


async def stop_temporal_worker(timeout: float = 15.0) -> None:
    """Stop this process's Temporal workers (after the engine stopped its
    runs: a run still going is then cancelled as a shutdown)."""
    workers = list(_WORKERS.items())
    _WORKERS.clear()
    for name, (worker, task) in workers:
        try:
            await asyncio.wait_for(worker.shutdown(), timeout=timeout)
        except _temporal_errors() as exc:
            logger.warning("[durable] Temporal %s worker did not stop cleanly: %s", name, exc)
            task.cancel()
