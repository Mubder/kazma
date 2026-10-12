"""Finite research admission and immutable job scope; not a recovery journal."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from functools import wraps

from kazma_core.ide.workspace_scope import workspace_path_scope
from kazma_core.web_acquire.policy import AcquisitionPolicyError, acquisition_policy, policy_scope
from kazma_core.web_acquire.resources import research_capacity, research_session_capacity
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode
from kazma_core.web_acquire.robots import crawl_policy

_DEADLINE_SECONDS = 1800


@asynccontextmanager
async def _frozen(root_resolver):
    from kazma_core.web_acquire.crawl_jobs import call

    try:
        policy = acquisition_policy()
    except AcquisitionPolicyError as exc:
        raise AcquisitionError(FetchErrorCode.POLICY, str(exc)) from exc
    root = await call(root_resolver)
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
            try:
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
        return run
    return decorate


def bounded_session(task_for):
    """Reserve before session creation; retain capacity through the actual task."""
    def decorate(function):
        @wraps(function)
        async def run(*args, **kwargs):
            from kazma_core.workspace.binding import resolve_active_root

            lease = await research_session_capacity().acquire()
            transferred = False
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
                        task.add_done_callback(release_when_settled)
                        transferred = True
                    return result
            finally:
                if not transferred:
                    lease.release()
        return run
    return decorate
