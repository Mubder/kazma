"""Retain search worker ownership through fallback and repeated cancellation."""
from contextvars import ContextVar
import time

from kazma_core.web_acquire import resources
from kazma_core.web_acquire.process_worker import OwnedWorkers
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

_OWNERS = OwnedWorkers('search', lambda: resources.acquisition_capacity(owned_browser=False))
_deadline = ContextVar('search_deadline', default=None)


def remaining():
    deadline = _deadline.get()
    seconds = 30 if deadline is None else min(30, deadline - time.monotonic())
    if seconds <= 0:
        raise AcquisitionError(FetchErrorCode.DEADLINE, 'Search exhausted its 90-second backend deadline.', transient=True)
    return seconds


async def run_search(function, query, count):
    from kazma_core.web_acquire.search_sdk import search_cancel
    from kazma_core.web_acquire.policy import acquisition_policy, policy_scope
    from kazma_core.proxy.config import proxy_config_scope
    from contextlib import nullcontext
    from kazma_core.web_acquire.accounting import remote_scope

    policy = acquisition_policy()

    def run(owner):
        cancel = search_cancel.set(owner.cancelled)
        deadline = _deadline.set(time.monotonic() + 90)
        try:
            with policy_scope(policy), remote_scope(), (proxy_config_scope() if policy.permits('proxy') else nullcontext()):
                return function(query, count)
        finally:
            _deadline.reset(deadline)
            search_cancel.reset(cancel)
    return await _OWNERS.call(run)
