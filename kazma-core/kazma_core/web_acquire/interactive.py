"""Native browser compatibility facade on the bounded acquisition owner."""
from __future__ import annotations

import asyncio
import threading

from kazma_core.web_acquire.browser import BrowserJob, current_browser_job
from kazma_core.web_acquire.browser_network import BrowserNetwork
from kazma_core.web_acquire.policy import acquisition_policy, policy_scope
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

_loop, _thread = None, None
_lock = threading.Lock()


def _owner_loop():
    global _loop, _thread
    with _lock:
        if _loop is None or _loop.is_closed():
            from kazma_core.playwright_loop import _new_loop
            _loop = _new_loop()
            _thread = threading.Thread(target=_loop.run_forever, name='kazma-interactive-browser', daemon=True)
            _thread.start()
        return _loop


def _run(coroutine):
    from kazma_core.playwright_loop import run_in_browser_loop
    return asyncio.run(run_in_browser_loop(coroutine, timeout=45, loop=_owner_loop()))


def _scope():
    from kazma_core.workspace.binding import resolve_active_root
    from kazma_core.tenant_context import get_current_tenant_id
    return str(resolve_active_root().resolve()), get_current_tenant_id()


class _Owner:
    def __init__(self):
        self.job, self.page, self.network = BrowserJob(), None, None
        self.policy = acquisition_policy()
        self.scope = _scope()

    async def start(self):
        token = current_browser_job.set(self.job)
        started = False
        try:
            with policy_scope(self.policy):
                await self.job._start_impl()
                self.page = await self.job.context.new_page()
                self.network = BrowserNetwork(self.page, allow_mutations=True)
                await self.network.__aenter__()
                started = True
        finally:
            try:
                if not started:
                    await self.close()
            finally:
                current_browser_job.reset(token)

    async def invoke(self, path, args, kwargs):
        if await asyncio.to_thread(_scope) != self.scope:
            raise AcquisitionError(FetchErrorCode.WORKSPACE, 'Browser belongs to another workspace or tenant; close it before continuing.')
        if acquisition_policy() != self.policy:
            raise AcquisitionError(FetchErrorCode.ROUTING, 'Browser acquisition policy changed; close the current browser before continuing.')
        token = current_browser_job.set(self.job)
        try:
            if self.job._driver:
                self.job._driver.watch(asyncio.current_task())
            target = self.page
            for name in path:
                target = getattr(target, name)
            result = await target(*args, **kwargs)
            await self.network.settle()
            if self.network.failure:
                raise self.network.failure
            if self.network.reasons:
                raise AcquisitionError(FetchErrorCode.UNSUPPORTED_CONTENT, 'Browser resources were refused; inspect acquisition policy and network limits.')
            return result
        finally:
            current_browser_job.reset(token)

    async def close(self):
        if self.job._driver is not None:
            self.job._driver.cleanup_window()
        try:
            if self.page is not None:
                try:
                    await self.page.close()
                finally:
                    self.page = None
            if self.network is not None:
                await self.network.aclose()
                self.network = None
        finally:
            await self.job._close_impl()

    def interrupt(self):
        driver = self.job._driver
        if driver is not None and driver.transport is not None:
            driver.transport.request_stop()


class PageFacade:
    def __init__(self, owner, path=()):
        self.owner, self.path = owner, path

    def __getattr__(self, name):
        if name in {'mouse', 'keyboard'}:
            return PageFacade(self.owner, self.path + (name,))
        def invoke(*args, **kwargs):
            return _run(self.owner.invoke(self.path + (name,), args, kwargs))
        return invoke

    def close(self):
        return _run(self.owner.close())

    def interrupt(self):
        self.owner.interrupt()


def open_page():
    owner = _Owner()
    _run(owner.start())
    return PageFacade(owner)
