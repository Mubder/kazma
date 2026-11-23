"""Finite research admission and immutable job scope; not a recovery journal."""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar
from functools import wraps

from kazma_core.ide.workspace_scope import workspace_path_scope
from kazma_core.web_acquire.policy import AcquisitionPolicyError, acquisition_policy, policy_scope
from kazma_core.web_acquire.resources import research_capacity, research_session_capacity
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
from kazma_core.web_acquire.robots import crawl_policy

_DEADLINE_SECONDS = 1800

#: Research runs each space holds now, in this process (Spaces S2-08), and
#: each person of a space (S4-04).
_space_runs: dict[str, int] = {}
_person_runs: dict[tuple[str, str], int] = {}
_space_lock = threading.Lock()
#: Set inside a background session's own work: its pipeline is the run the
#: session already counts, never a second one.
_counted: ContextVar[bool] = ContextVar("kazma_research_space_counted", default=False)


def research_running_in(space_id: str) -> int:
    """How many research runs *space_id* holds now (this process)."""
    with _space_lock:
        return _space_runs.get(space_id, 0)


async def _take_space_share():
    """Count one research run for the bound space and person, or refuse it:
    a space holds at most its share of the install's research slots
    (``spaces.space_limits``), and a person of a shared space at most half
    of the space's (S4-04, ``spaces.fairness``), asked before a slot is
    taken so a space at its share never waits in the install's queue, nor
    holds a place in it. Returns the release, or None while the install
    does not enforce Spaces (or the run is already counted)."""
    from kazma_core.spaces.fairness import shares
    from kazma_core.spaces.identity import IdentityUnavailable
    from kazma_core.spaces.scope import current_scope, spaces_enforced
    from kazma_core.spaces.space_limits import share_refusal
    from kazma_core.web_acquire.crawl_jobs import call

    if not spaces_enforced() or _counted.get():
        return None
    bound = current_scope()
    space = bound.space_id if bound is not None else ""
    person = str(bound.asker or "") if bound is not None else ""
    try:
        cap = await call(shares, "research", space)
    except IdentityUnavailable as exc:
        raise AcquisitionError(FetchErrorCode.UNAVAILABLE,
                               "Accounts cannot be read right now; no research was started.",
                               transient=True) from exc
    with _space_lock:
        held = _space_runs.get(space, 0)
        mine = _person_runs.get((space, person), 0)
        refused = share_refusal("research", held, cap, person=person,
                                person_running=mine) if cap is not None else None
        if refused:
            raise AcquisitionError(FetchErrorCode.LIMIT, refused, transient=True)
        _space_runs[space] = held + 1
        if person:
            _person_runs[(space, person)] = mine + 1
    released = threading.Event()

    def release() -> None:
        if released.is_set():
            return
        released.set()
        with _space_lock:
            left = _space_runs.get(space, 0) - 1
            if left > 0:
                _space_runs[space] = left
            else:
                _space_runs.pop(space, None)
            if person:
                left = _person_runs.get((space, person), 0) - 1
                if left > 0:
                    _person_runs[(space, person)] = left
                else:
                    _person_runs.pop((space, person), None)

    return release


@asynccontextmanager
async def _frozen(root_resolver):
    from kazma_core.spaces.features import work_refusal
    from kazma_core.spaces.identity import IdentityUnavailable
    from kazma_core.spaces.scope import bound_asker
    from kazma_core.web_acquire.crawl_jobs import call

    # Research's admission (Spaces S2-11): every research run -- the tool,
    # the intent engine, the composer, a chat command, the page and a
    # resume -- passes here, before a session row or a plan. A person
    # without Research starts none.
    try:
        refused = await call(work_refusal, "research", bound_asker())
    except IdentityUnavailable as exc:
        raise AcquisitionError(FetchErrorCode.UNAVAILABLE,
                               "Accounts cannot be read right now; no research was started.",
                               transient=True) from exc
    if refused:
        raise AcquisitionError(FetchErrorCode.POLICY, refused)
    try:
        policy = acquisition_policy()
    except AcquisitionPolicyError as exc:
        raise AcquisitionError(FetchErrorCode.POLICY, str(exc)) from exc
    root = await call(root_resolver)
    # Storage is part of the same admission (FA-266): a run refused for
    # retained reports is refused here, before a session row or a plan.
    from kazma_core.web_acquire.artifact_storage import admit

    await admit(root)
    async with workspace_path_scope(root):
        with policy_scope(policy), crawl_policy(purpose='research') as robots:
            robots.enforce()
            yield


def bounded_pipeline(root_resolver):
    """Admit before planning/search/output and freeze scope for the full call."""
    def decorate(function):
        @wraps(function)
        async def run(*args, **kwargs):
            deadline = None
            release_share = None
            try:
                release_share = await _take_space_share()
                async with await research_capacity().acquire():
                    async with _frozen(root_resolver):
                        deadline = asyncio.timeout(_DEADLINE_SECONDS)
                        async with deadline:
                            return await function(*args, **kwargs)
            except TimeoutError:
                if deadline is None or not deadline.expired():
                    raise
                return 'Error: research pipeline deadline expired (30 minutes); inspect partial output before retrying.'
            except AcquisitionError as exc:
                return f'Error: {exc}'
            finally:
                if release_share is not None:
                    release_share()
        return run
    return decorate


def bounded_session(task_for):
    """Reserve before session creation; retain capacity through the actual task."""
    def decorate(function):
        @wraps(function)
        async def run(*args, **kwargs):
            from kazma_core.workspace.binding import resolve_active_root

            # The space's share first (S2-08), then the install's slot; both
            # are held through the session's own task, which counts once.
            release_share = await _take_space_share()
            leased = False
            try:
                lease = await research_session_capacity().acquire()
                leased = True
            finally:
                if not leased and release_share is not None:
                    release_share()
            transferred = False
            counted = _counted.set(release_share is not None or _counted.get())
            try:
                async with _frozen(resolve_active_root):
                    result = await function(*args, **kwargs)
                    task = task_for(result)
                    if task is not None:
                        def release_when_settled(finished):
                            owner = task_for(result)
                            if owner is not None and owner is not finished:
                                owner.add_done_callback(release_when_settled)
                            else:
                                lease.release()
                                if release_share is not None:
                                    release_share()
                        task.add_done_callback(release_when_settled)
                        transferred = True
                    return result
            finally:
                _counted.reset(counted)
                if not transferred:
                    lease.release()
                    if release_share is not None:
                        release_share()
        return run
    return decorate
