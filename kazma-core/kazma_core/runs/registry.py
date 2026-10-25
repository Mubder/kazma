"""The run registry: every kind's runs, read from their own stores.

A read model (``contract``): nothing here writes a kind's state. Each kind's
adapter (``kinds.KINDS``) reads its store off the event loop and returns
:class:`~kazma_core.runs.contract.Run` records; the registry adds the
supervisor's verdict, so every surface -- the Dashboard, ``/runs``, the
agent's ``list_runs`` tool, the supervisor itself -- sees one answer.

A kind whose store cannot be read is reported as such (``errors``), never as
"nothing running": the other kinds are still listed.

An action goes through :func:`perform`, which asks the adapter for the run as
it is NOW and refuses an action the run no longer offers. The adapter's
``act`` calls the kind's own control -- the function its page already uses.
"""

from __future__ import annotations

import asyncio
import importlib
import dataclasses
import logging
import sqlite3
import time
from typing import Any, Protocol

from kazma_core.runs.contract import ActionResult, Run, judge, with_verdict
from kazma_core.runs.kinds import KINDS

__all__ = [
    "Adapter",
    "adapter_for",
    "get_run",
    "list_runs",
    "perform",
]

logger = logging.getLogger(__name__)

#: Finished runs are listed for this long (failures stay findable a day).
FINISHED_WINDOW_S = 24 * 3600.0
#: One adapter's read may take this long before its kind is reported unreadable.
ADAPTER_DEADLINE_S = 10.0


class Adapter(Protocol):
    """What a kind supplies. Both methods are async; store reads run in threads."""

    #: Every native status word -> the RunState it shows as (as text).
    STATE_MAP: dict[str, str]

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]: ...

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult: ...


class _RunListing:
    """The runs, and the kinds that could not be read (``kind -> reason``)."""

    def __init__(self, runs: list[Run], errors: dict[str, str]) -> None:
        self.runs = runs
        self.errors = errors

    def to_dict(self) -> dict[str, Any]:
        return {"runs": [r.to_dict() for r in self.runs], "errors": dict(self.errors)}


_ADAPTERS: dict[str, Any] = {}


def adapter_for(kind: str) -> Any:
    """The adapter object of *kind* (resolved once, from ``kinds.KINDS``)."""
    cached = _ADAPTERS.get(kind)
    if cached is not None:
        return cached
    module_name, _, attr = KINDS[kind].adapter.partition(":")
    adapter = getattr(importlib.import_module(module_name), attr)
    _ADAPTERS[kind] = adapter
    return adapter


def _split_run_id(run_id: str) -> tuple[str, str]:
    """``("swarm_task", "abc")`` from ``"swarm_task:abc"``; ValueError otherwise."""
    kind, sep, native = str(run_id or "").partition(":")
    if not sep or kind not in KINDS or not native:
        raise ValueError(f"not a run id: {run_id!r}")
    return kind, native


async def _kind_runs(kind: str, now: float, window_s: float) -> list[Run]:
    adapter = adapter_for(kind)
    runs = await asyncio.wait_for(adapter.list_runs(now, window_s=window_s), timeout=ADAPTER_DEADLINE_S)
    spec = KINDS[kind]
    return [with_verdict(run, judge(run, spec, now)) for run in runs]


async def list_runs(
    *, kinds: tuple[str, ...] | None = None, now: float | None = None,
    window_s: float = FINISHED_WINDOW_S, mark_handled: bool = True,
) -> _RunListing:
    """Every run of *kinds* (all by default): the active ones and those that
    finished within *window_s*, newest first, each judged.

    With *mark_handled*, a run the owner already decided about (an incident
    about it, at its current version, acted on, dismissed or closed) is
    marked ``handled``: it no longer needs attention. The supervisor reads
    the decisions itself and passes False."""
    stamp = time.time() if now is None else now
    wanted = [k for k in (kinds or tuple(KINDS)) if k in KINDS]
    results = await asyncio.gather(*(_kind_runs(k, stamp, window_s) for k in wanted),
                                   return_exceptions=True)
    runs: list[Run] = []
    errors: dict[str, str] = {}
    for kind, result in zip(wanted, results, strict=True):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            reason = "timed out" if isinstance(result, TimeoutError) else type(result).__name__
            logger.warning("[runs] %s runs could not be read: %s", kind, result,
                           exc_info=not isinstance(result, TimeoutError))
            errors[kind] = reason
            continue
        runs.extend(result)
    runs.sort(key=lambda r: (r.ended_at is None, r.progress_at or r.started_at or 0.0), reverse=True)
    if mark_handled and runs:
        runs = await _mark_handled(runs)
    return _RunListing(runs, errors)


def _decided_keys() -> set[tuple[str, str, str]]:
    from kazma_core.runs.incidents import get_incident_store

    return get_incident_store().decided_keys()


async def _mark_handled(runs: list[Run]) -> list[Run]:
    """*runs*, each decided about at its current version marked ``handled``."""
    try:
        decided = await asyncio.to_thread(_decided_keys)
    except (sqlite3.Error, OSError) as exc:
        # Unread decisions leave every failure listed: never the other way.
        logger.warning("[runs] decisions could not be read; every failure stays listed: %s", exc)
        return runs
    done = {(run_id, version) for run_id, _condition, version in decided}
    return [dataclasses.replace(r, handled=True) if (r.run_id, r.version) in done else r for r in runs]


async def get_run(run_id: str, *, now: float | None = None) -> Run | None:
    """The run *run_id* as it is now, or None when its kind no longer lists it."""
    kind, _native = _split_run_id(run_id)
    listing = await list_runs(kinds=(kind,), now=now)
    if kind in listing.errors:
        raise RuntimeError(f"{kind} runs could not be read: {listing.errors[kind]}")
    return next((r for r in listing.runs if r.run_id == run_id), None)


async def perform(run_id: str, action: str, *, actor: str) -> ActionResult:
    """Do *action* to *run_id* through its kind's own control.

    Refused, nothing done, when the run is gone or no longer offers the
    action (it finished, recovered, or changed state since it was shown).
    """
    try:
        kind, native = _split_run_id(run_id)
    except ValueError:
        return ActionResult(False, "unknown_run", "No such run.")
    if KINDS[kind].read_only:
        return ActionResult(False, "read_only", "This kind of run takes no actions here.")
    run = await get_run(run_id)
    if run is None:
        return ActionResult(False, "gone", "The run is no longer listed: it finished or was removed.")
    if action not in run.actions:
        return ActionResult(False, "not_offered",
                            f"The run is {run.state} now and does not offer {action}.")
    result = await adapter_for(kind).act(native, action, actor=actor)
    logger.info("[runs] action run=%s action=%s actor=%s ok=%s code=%s",
                run_id, action, actor or "-", result.ok, result.code)
    return result
