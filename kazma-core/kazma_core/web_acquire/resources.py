"""Process-wide bounded admission shared by event loops and caller threads.

Live browser owners have two reserved acquisition slots. Otherwise a queue of
unowned acquisitions waiting for those browsers could occupy every slot and
prevent a crawler holding a browser from progressing far enough to release it.
These are admission limits, not OS memory/CPU/network isolation.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from dataclasses import dataclass

from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode


@dataclass(eq=False)
class _Waiter:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future
    lease: _Lease | None = None


class _Lease:
    def __init__(self, pool, token):
        self.pool, self.token = pool, token

    def release(self):
        self.pool._release(self.token)

    def quarantine(self):
        with self.pool._lock:
            if self.token in self.pool._active:
                self.pool._quarantined.add(self.token)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.release()


class _CapacityPool:
    """FIFO admission with bounded waiters, timeout and idempotent leases."""

    def __init__(self, name: str, capacity: int, queue_limit: int, wait_seconds: float = 30):
        import math

        if capacity < 1 or queue_limit < 0 or not math.isfinite(wait_seconds) or wait_seconds <= 0:
            raise ValueError("Capacity, queue and wait bounds must be valid")
        self.name, self.capacity, self.queue_limit = name, capacity, queue_limit
        self.wait_seconds = wait_seconds
        self._lock = threading.Lock()
        self._active: set[object] = set()
        self._quarantined: set[object] = set()
        self._queue: deque[_Waiter] = deque()

    def _lease_locked(self):
        token = object()
        self._active.add(token)
        return _Lease(self, token)

    async def acquire(self):
        loop = asyncio.get_running_loop()
        with self._lock:
            if len(self._active) < self.capacity and not self._queue:
                return self._lease_locked()
            if len(self._queue) >= self.queue_limit:
                raise self._busy("queue is full")
            waiter = _Waiter(loop, loop.create_future())
            self._queue.append(waiter)
        try:
            async with asyncio.timeout(self.wait_seconds):
                return await waiter.future
        except TimeoutError as exc:
            self._discard(waiter)
            raise self._busy("admission wait expired") from exc
        except asyncio.CancelledError:
            self._discard(waiter)
            raise

    def _busy(self, why):
        return AcquisitionError(FetchErrorCode.LIMIT,
            f"Local {self.name} capacity exhausted ({why}; active limit {self.capacity}, "
            f"queue limit {self.queue_limit}, wait {self.wait_seconds:g}s). Retry after active work finishes.",
            transient=True)

    def _discard(self, waiter):
        with self._lock:
            if waiter in self._queue:
                self._queue.remove(waiter)
            lease = waiter.lease
        if lease is not None:
            lease.release()

    def _release(self, token):
        waiter = None
        with self._lock:
            if token not in self._active:
                return
            self._active.remove(token)
            self._quarantined.discard(token)
            while self._queue:
                candidate = self._queue.popleft()
                if not candidate.loop.is_closed() and not candidate.future.done():
                    candidate.lease = self._lease_locked()
                    waiter = candidate
                    break
        if waiter is not None:
            try:
                waiter.loop.call_soon_threadsafe(self._deliver, waiter)
            except RuntimeError:
                # The receiving event loop closed after admission. Ownership
                # still belongs here; return the grant rather than leaking it.
                waiter.lease.release()

    def _deliver(self, waiter):
        if waiter.future.done():
            waiter.lease.release()
        else:
            waiter.future.set_result(waiter.lease)

    def snapshot(self):
        with self._lock:
            return {"active": len(self._active), "waiting": len(self._queue),
                    "quarantined": len(self._quarantined),
                    "capacity": self.capacity, "queue_limit": self.queue_limit,
                    "wait_seconds": self.wait_seconds}


_BROWSERS = _CapacityPool("browser jobs", 2, 8)
_ACQUISITIONS = _CapacityPool("acquisitions", 6, 24)
_BROWSER_ACQUISITIONS = _CapacityPool("browser-owner acquisitions", 2, 8)
_DNS = _CapacityPool("DNS validation", 4, 16, wait_seconds=10)
_PARSERS = _CapacityPool("HTML parsing", 2, 8, wait_seconds=10)
_RESEARCH = _CapacityPool("research pipelines", 4, 0)
_RESEARCH_SESSIONS = _CapacityPool("background research sessions", 4, 0)


def browser_capacity() -> _CapacityPool:
    return _BROWSERS


def dns_capacity() -> _CapacityPool:
    return _DNS


def parser_capacity() -> _CapacityPool:
    return _PARSERS


def research_capacity() -> _CapacityPool:
    return _RESEARCH


def research_session_capacity() -> _CapacityPool:
    return _RESEARCH_SESSIONS


def acquisition_capacity(*, owned_browser: bool | None = None) -> _CapacityPool:
    if owned_browser is None:
        from kazma_core.web_acquire.browser import current_browser_job

        owner = current_browser_job.get()
        owned_browser = bool(owner is not None and not owner._closed and owner._capacity_lease is not None)
    return _BROWSER_ACQUISITIONS if owned_browser else _ACQUISITIONS


def resource_snapshot() -> dict:
    return {"scope": "this Python process", "browsers": _BROWSERS.snapshot(),
            "acquisitions": _ACQUISITIONS.snapshot(),
            "dns": _DNS.snapshot(),
            "parsers": _PARSERS.snapshot(),
            "research": _RESEARCH.snapshot(),
            "research_sessions": _RESEARCH_SESSIONS.snapshot(),
            "browser_owner_acquisitions": _BROWSER_ACQUISITIONS.snapshot()}
