"""The run contract: one shape for every unit of work Kazma runs.

A *run* is a unit of work that outlives a single request: a chat turn, a swarm
task, a scheduled job's fire, a document job, a research session, a crawl, an X
publication operation, a memory queue task. Each kind keeps its own durable
state and its own controls; this module is the one shape the run registry
projects them into (``docs/plans/RUN_SUPERVISION_PLAN.md``).

The registry is a READ MODEL. A kind's store stays the truth about its runs,
and nothing here writes a kind's state: an action calls the kind's own
control (the same one its page uses).

Liveness is judged by progress, not by heartbeats (:func:`judge`):

* ``dead`` -- the owner is gone (no task, lease lapsed, process gone);
* ``stalled`` -- the owner is alive and nothing meaningful changed within the
  kind's budget;
* ``slow`` -- progressing, past the kind's expected duration;
* ``failed`` -- the kind's own store says the run ended in failure.

A verdict never cancels or fails a run by itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any

__all__ = [
    "ACTIONS",
    "ActionResult",
    "KindSpec",
    "Run",
    "RunState",
    "Verdict",
    "judge",
    "with_verdict",
]


class RunState(StrEnum):
    """Where a run stands, in words common to every kind."""

    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    STALLED = "stalled"
    SLOW = "slow"
    FAILED = "failed"
    DONE = "done"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


#: States in which a run is still someone's work.
ACTIVE_STATES = frozenset({RunState.QUEUED, RunState.RUNNING, RunState.WAITING_HUMAN,
                           RunState.STALLED, RunState.SLOW})


class Verdict(StrEnum):
    """What the supervisor makes of a run (:func:`judge`)."""

    OK = "ok"
    SLOW = "slow"
    STALLED = "stalled"
    DEAD = "dead"
    FAILED = "failed"


#: The actions an adapter may offer. ``retry_checked`` is Retry for a run whose
#: effects are unknown: the operator says they checked first (the swarm's
#: ``acknowledge_unknown``, SW-E08). ``close`` ends a chat turn left open with
#: nothing running it.
ACTIONS = ("cancel", "retry", "retry_checked", "close")

#: Actions an incident offers beside the run's own (never an adapter's).
INCIDENT_ACTIONS = ("dismiss", "diagnose")


@dataclass(frozen=True)
class Run:
    """One run, as every surface shows it."""

    run_id: str
    kind: str
    native_id: str
    title: str
    state: RunState
    native_state: str
    started_at: float | None = None
    #: When the run last made meaningful progress (epoch seconds).
    progress_at: float | None = None
    progress_note: str = ""
    #: Whether the owning task, lease or process is alive. None: not known.
    owner_alive: bool | None = None
    deadline_at: float | None = None
    ended_at: float | None = None
    #: "none" (nothing outside Kazma was touched), "known" or "unknown".
    effects: str = "none"
    #: The store's version or update stamp, for compare-and-set actions.
    version: str = ""
    actions: tuple[str, ...] = ()
    #: The page that shows this run.
    link: str = ""
    #: A short failure reason from the kind's store (never a stack trace).
    detail: str = ""
    #: Where the run started: {"platform", "target", "thread_id"} or {}.
    origin: dict[str, str] = field(default_factory=dict)
    #: Set when something else already tells the owner about THIS run (an
    #: agent's own crawl reports to the agent): no message, as a kind's
    #: ``announced_by`` does for a whole condition.
    announced_by: str = ""
    #: What retries of this work share (a retried swarm task names its
    #: original; a document job its document). Automatic steps are counted
    #: per lineage, so a retry that fails again is not retried again by
    #: itself. Empty: the run id.
    lineage: str = ""
    verdict: Verdict | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = str(self.state)
        data["verdict"] = str(self.verdict) if self.verdict else None
        data["actions"] = list(self.actions)
        # The delivery address stays on the server (SCH-S10's rule): the
        # page shows the platform only.
        data["origin"] = {"platform": self.origin.get("platform", "")} if self.origin else {}
        return data


@dataclass(frozen=True)
class ActionResult:
    """What an action did. ``code`` is stable; ``message`` is for people."""

    ok: bool
    code: str
    message: str = ""


@dataclass(frozen=True)
class KindSpec:
    """One kind of run: where it lives, how it is judged, who announces what."""

    kind: str
    #: ``module:attribute`` of the adapter object (``list_runs`` / ``act``).
    adapter: str
    label: str
    label_ar: str
    #: What holds the truth about these runs.
    source: str
    #: The tables (product SQL) holding this kind's status, for the inventory gate.
    tables: tuple[str, ...] = ()
    #: No meaningful progress for this long while alive -> stalled. None: never.
    stall_after_s: float | None = None
    #: Past this long since the start while progressing -> slow. None: never.
    slow_after_s: float | None = None
    #: The owner gone for this long -> dead.
    dead_after_s: float = 120.0
    #: Conditions another mechanism already announces, with what announces
    #: them: the supervisor records those incidents but sends no message
    #: (one message per run and condition).
    announced_by: dict[str, str] = field(default_factory=dict)
    #: Shown and paged, but offers no buttons beyond Details.
    read_only: bool = False


def judge(run: Run, spec: KindSpec, now: float) -> Verdict:
    """The supervisor's verdict on *run*. Pure: same input, same answer."""
    if run.state == RunState.FAILED:
        return Verdict.FAILED
    if run.state not in (RunState.RUNNING, RunState.QUEUED, RunState.STALLED, RunState.SLOW):
        # Waiting on a human, finished, cancelled or unknown: never stalled.
        return Verdict.OK
    last = run.progress_at or run.started_at
    if run.owner_alive is False:
        if last is None or now - last >= spec.dead_after_s:
            return Verdict.DEAD
        return Verdict.OK
    if run.state == RunState.QUEUED:
        return Verdict.OK
    if spec.stall_after_s is not None and last is not None and now - last >= spec.stall_after_s:
        return Verdict.STALLED
    if (spec.slow_after_s is not None and run.started_at is not None
            and now - run.started_at >= spec.slow_after_s):
        return Verdict.SLOW
    return Verdict.OK


def with_verdict(run: Run, verdict: Verdict) -> Run:
    """*run* as shown: a running run judged stalled, dead or slow says so."""
    state = run.state
    if verdict in (Verdict.STALLED, Verdict.DEAD):
        state = RunState.STALLED
    elif verdict == Verdict.SLOW:
        state = RunState.SLOW
    return replace(run, state=state, verdict=verdict)
