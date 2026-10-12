"""Bounded, process-wide HTTP origin scheduling across jobs and event loops.

HTTP Retry-After feedback is shared even when clients/proxies change. Refuse
dispatch during server cooldown rather than shortening it to fit a call's
deadline. This in-memory scheduler is not durable crawl recovery.
"""
from __future__ import annotations

import asyncio
import math
import ipaddress
import threading
import time
from collections import OrderedDict
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

from kazma_core.web_acquire.resources import _CapacityPool
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

origin_delay: ContextVar[float] = ContextVar('origin_delay', default=0.0)


@asynccontextmanager
async def polite_requests(delay: float = 0.3):
    """Propagate a crawl's requested spacing through redirects and rendering."""
    if not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
        raise AcquisitionError(FetchErrorCode.POLICY, 'HTTP origin delay must be finite and non-negative.')
    token = origin_delay.set(max(origin_delay.get(), delay))
    try:
        yield
    finally:
        origin_delay.reset(token)


def _origin(url: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError
        host = parsed.hostname.rstrip('.').encode('idna').decode('ascii').lower()
        if ':' in host:
            host = ipaddress.IPv6Address(host).compressed
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        return parsed.scheme, host, port if port is not None else (443 if parsed.scheme == 'https' else 80)
    except ValueError:
        raise AcquisitionError(FetchErrorCode.INVALID_URL, 'Invalid HTTP origin; acquisition was refused.') from None


def _retry_after(value: str | None, now: float) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    value = value.strip()
    if len(value) > 256:
        return math.inf if value.isascii() and value.isdecimal() else None
    if value.isascii() and value.isdecimal():
        # Huge but valid delays refuse requests; never clamp them to a shorter wait.
        return float(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:  # obsolete asctime HTTP-date is interpreted as UTC
            date = date.replace(tzinfo=UTC)
        return max(0.0, date.timestamp() - now)
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass
class _OriginState:
    pool: _CapacityPool = field(default_factory=lambda: _CapacityPool('HTTP origin', 2, 32))
    users: int = 0
    next_start: float = 0.0
    not_before: float = 0.0
    interval: float = 0.0
    interval_until: float = 0.0
    failures: int = 0


class _OriginScheduler:
    """Two live requests and 32 waiters per origin; bounded origin table."""

    def __init__(self, max_origins: int = 512, *, durable: bool = False):
        if max_origins < 1:
            raise ValueError('max_origins must be positive')
        self.durable = durable
        self.max_origins = max_origins
        self._lock = threading.RLock()
        self._states: OrderedDict[tuple[str, str, int], _OriginState] = OrderedDict()

    def _state(self, key):
        now = time.monotonic()
        if key not in self._states:
            if len(self._states) >= self.max_origins:
                for candidate, state in list(self._states.items()):
                    if not state.users and max(state.next_start, state.not_before, state.interval_until) <= now:
                        del self._states[candidate]
                        break
                else:
                    raise AcquisitionError(FetchErrorCode.LIMIT,
                        'HTTP origin table is full of active or cooling origins. Retry after existing work settles.', transient=True)
            self._states[key] = _OriginState()
        self._states.move_to_end(key)
        return self._states[key]

    @asynccontextmanager
    async def request(self, url: str):
        key = _origin(url)
        with self._lock:
            state = self._state(key)
            state.users += 1
        lease, durable_token = None, None
        from kazma_core.web_acquire import origin_state
        from kazma_core.web_acquire.crawl_jobs import call
        try:
            try:
                async with asyncio.timeout(30):
                    if self.durable:
                        durable_token = await call(origin_state.queue, key, on_cancel=lambda token: origin_state.release(key, token))
                    lease = await state.pool.acquire()
                    while self.durable:
                        wait = await call(origin_state.start, key, durable_token, origin_delay.get())
                        if not wait:
                            break
                        await asyncio.sleep(wait)
                    while not self.durable:
                        with self._lock:
                            now = time.monotonic()
                            cooldown = state.not_before - now
                            if cooldown > 0:
                                raise AcquisitionError(FetchErrorCode.THROTTLED,
                                    ('HTTP origin cooldown active beyond runtime scheduling range. ' if not math.isfinite(cooldown) else
                                     f'HTTP origin cooldown active; retry in at least {math.ceil(cooldown)} seconds. ')
                                    + 'No request or alternate recovery backend was dispatched.', transient=True)
                            interval = origin_delay.get()
                            if not math.isfinite(interval) or interval < 0:
                                raise AcquisitionError(FetchErrorCode.POLICY, 'HTTP origin delay must be finite and non-negative.')
                            if interval:
                                state.interval = max(interval, state.interval if state.interval_until > now else 0)
                                state.interval_until = now + 60
                            interval = max(interval, state.interval if state.interval_until > now else 0)
                            wait = state.next_start - now
                            if wait <= 0:
                                state.next_start = now + interval
                                break
                        await asyncio.sleep(wait)
            except TimeoutError:
                raise AcquisitionError(FetchErrorCode.LIMIT,
                    'HTTP origin admission or spacing deadline exceeded (30s); dispatch was refused.', transient=True) from None
            yield
        finally:
            try:
                if durable_token is not None:
                    from kazma_core.playwright_loop import finish_owned
                    await finish_owned(call(origin_state.release, key, durable_token))
            finally:
                if lease is not None:
                    lease.release()
                with self._lock:
                    state.users -= 1

    @contextmanager
    def request_sync(self, url, *, cancel_event=None):
        """Fixed search workers retain the same durable origin lease."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise AcquisitionError(FetchErrorCode.ROUTING, 'Synchronous search admission cannot run on an event loop.')
        if not self.durable:
            yield  # isolated component-test scheduler; production enables durability
            return
        from kazma_core.web_acquire import origin_state
        key, token = _origin(url), None
        deadline = time.monotonic() + 30
        interval = origin_delay.get()
        if not math.isfinite(interval) or interval < 0:
            raise AcquisitionError(FetchErrorCode.POLICY, 'HTTP origin delay must be finite and non-negative.')
        try:
            token = origin_state.queue(key)
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError('Search admission cancelled')
                if time.monotonic() >= deadline:
                    raise AcquisitionError(FetchErrorCode.LIMIT, 'Durable search origin admission exceeded 30 seconds.', transient=True)
                wait = origin_state.start(key, token, interval)
                if not wait:
                    break
                if cancel_event is not None:
                    cancel_event.wait(min(wait, .05))
                else:
                    time.sleep(min(wait, .05))
            yield
        finally:
            if token is not None:
                origin_state.release(key, token)

    def observe(self, url, status, headers):
        if type(status) is not int:
            return
        delayed = status in (429, 503) or 300 <= status < 400
        delay = _retry_after(headers.get('retry-after'), time.time()) if delayed else None
        if self.durable:
            from kazma_core.web_acquire import origin_state
            destination = None
            if 300 <= status < 400 and headers.get('location'):
                try:
                    destination = _origin(urljoin(url, headers['location']))
                except AcquisitionError:
                    pass
            origin_state.observe(_origin(url), status, delay, destination)
        with self._lock:
            state = self._state(_origin(url))
            if status in (429, 503):
                state.failures = min(state.failures + 1, 6)
                if delay is None:
                    delay = min(60, 2 ** state.failures)
            elif 200 <= status < 300:
                state.failures = 0
            if delay is not None:
                deadline = time.monotonic() + delay
                state.not_before = max(state.not_before, deadline)
                # Retry-After on a redirect also applies to its next request,
                # including a different origin. Validate again before dispatch.
                location = headers.get('location') if 300 <= status < 400 else None
                if location:
                    try:
                        target = _origin(urljoin(url, location))
                    except AcquisitionError:
                        return  # normal redirect validation reports malformed targets
                    dest = self._state(target)
                    dest.not_before = max(dest.not_before, deadline)

    @asynccontextmanager
    async def request_many(self, *urls):
        """Reserve hosted service and source origins in a stable order."""
        unique = {_origin(url): url for url in urls}
        async with AsyncExitStack() as stack:
            for key in sorted(unique):
                await stack.enter_async_context(self.request(unique[key]))
            yield

    def snapshot(self):
        with self._lock:
            now = time.monotonic()
            return {'scope': 'durable cross-process origin leases' if self.durable else 'this Python process', 'origins': len(self._states), 'origin_limit': self.max_origins,
                    'cooling': sum(state.not_before > now for state in self._states.values()),
                    'active': sum(state.pool.snapshot()['active'] for state in self._states.values()),
                    'waiting': sum(state.pool.snapshot()['waiting'] for state in self._states.values()),
                    'per_origin_active_limit': 2, 'per_origin_queue_limit': 32, 'wait_seconds': 30}


_SCHEDULER = _OriginScheduler(durable=True)


def origin_scheduler() -> _OriginScheduler:
    return _SCHEDULER
