"""Bounded, default-on crawl robots policy; content extraction is never a checker.

Rules are public-origin metadata, read as RFC 9309 says (2026-10-31, from
the recorded corpus): a 4xx answer -- 401 and 403 included -- means no rules
apply ("unavailable"; the page request then decides, and a site that refuses
KazmaBot refuses the page too, which the crawl reports as such); a redirect
is followed, up to five, across hosts (http://python.org/robots.txt moves to
https://www.python.org/robots.txt), each hop checked public by the fetch; the
rules found apply to the origin asked about. A failed fetch (network, 5xx,
an oversized or HTML answer) denies crawling. Kazma refused 401/403 and
cross-host redirects before, so a site behind either could never be crawled.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from contextlib import contextmanager, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from kazma_core.web_acquire.origins import _origin, origin_delay
from kazma_core.web_acquire.policy import acquisition_policy
from kazma_core.web_acquire.resources import _CapacityPool
from kazma_core.web_acquire.result import AcquisitionError, FetchErrorCode

#: Who is asking, with where to read about it (Wikimedia refuses a bot that
#: names no contact; live corpus 2026-10-31).
USER_AGENT = 'KazmaBot/1.0 (+https://github.com/Mubder/kazma; local research crawler)'
_AUTOMATED = {'crawl', 'kb', 'kb_discovery', 'research', 'research_save'}
_UNRESERVED = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')
_HEX = frozenset('0123456789abcdefABCDEF')
_loading: ContextVar[bool] = ContextVar('robots_loading', default=False)


def _normalized(value: str, *, pattern: bool = False) -> str:
    out, index = [], 0
    while index < len(value):
        char = value[index]
        if char == '%' and index + 2 < len(value) and set(value[index + 1:index + 3]) <= _HEX:
            number = int(value[index + 1:index + 3], 16)
            out.append(chr(number) if chr(number) in _UNRESERVED else f'%{number:02X}')
            index += 3
            continue
        special = char in '*$' and (not pattern or (char == '$' and index != len(value) - 1))
        if ord(char) > 126 or ord(char) < 33 or char == '%' or special:
            out.extend(f'%{byte:02X}' for byte in char.encode('utf-8'))
        else:
            out.append(char)
        index += 1
    return ''.join(out)


def _matches(pattern: str, path: str) -> bool:
    """Literal-segment glob matching; no backtracking regular expressions."""
    anchored = pattern.endswith('$')
    pieces = (pattern[:-1] if anchored else pattern).split('*')
    if not path.startswith(pieces[0]):
        return False
    cursor = len(pieces[0])
    if len(pieces) == 1:
        return not anchored or cursor == len(path)
    for index, piece in enumerate(pieces[1:], 1):
        if anchored and index == len(pieces) - 1:
            return path.endswith(piece) and len(path) - len(piece) >= cursor
        found = path.find(piece, cursor)
        if found < 0:
            return False
        cursor = found + len(piece)
    return True


@dataclass(frozen=True)
class _Rules:
    status: str
    http_status: int | None = None
    rules: tuple[tuple[str, bool], ...] = ()
    delay: float = 0.0
    error: str = ''
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    cost: int = 512

    def allows(self, url):
        if self.status in ('unavailable', 'denied'):
            return False
        parsed = urlsplit(url)
        path = _normalized((parsed.path or '/') + ('?' + parsed.query if parsed.query else ''))
        if len(path) > 32768:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Crawl URL exceeds the robots matching limit.')
        best, allowed = -1, True
        for pattern, permit in self.rules:
            if _matches(pattern, path):
                weight = len(pattern.rstrip('$').replace('*', ''))
                if weight > best or weight == best and permit:
                    best, allowed = weight, permit
        return allowed


def _parse(text: str, status: int = 200) -> _Rules:
    agents, rules, delay, groups = [], [], 0.0, []
    records = False
    count = 0
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if ':' not in line:
            continue
        name, value = (part.strip() for part in line.split(':', 1))
        name = name.lower()
        if name == 'user-agent':
            if records:
                groups.append((agents, rules, delay))
                agents, rules, delay, records = [], [], 0.0, False
            if value == '*' or re.fullmatch(r'[A-Za-z_-]+', value):
                agents.append(value.lower())
        elif name in ('allow', 'disallow') and agents:
            records = True
            if not value or not value.startswith('/') or any(ord(c) < 33 for c in value):
                continue
            count += 1
            if count > 8192 or len(value.encode('utf-8')) > 8192:
                raise AcquisitionError(FetchErrorCode.ROBOTS_UNAVAILABLE, 'Robots rule limits exceeded; crawl was refused.')
            rules.append((_normalized(value, pattern=True), name == 'allow'))
        elif name == 'crawl-delay' and agents:
            # Optional extension; never round a requested delay down.
            if re.fullmatch(r'\d+(?:\.\d+)?', value, flags=re.ASCII) and len(value) < 32:
                delay = max(delay, float(value))
    groups.append((agents, rules, delay))
    chosen = [group for group in groups if 'kazmabot' in group[0]]
    if not chosen:
        chosen = [group for group in groups if '*' in group[0]]
    merged = tuple(dict.fromkeys(rule for group in chosen for rule in group[1]))
    cost = 512 + sum(len(pattern) * 2 + 160 for pattern, permit in merged)
    if cost > 2 * 1024 * 1024:
        raise AcquisitionError(FetchErrorCode.ROBOTS_UNAVAILABLE, 'Robots policy memory limit exceeded; crawl was refused.')
    return _Rules('available', status, merged, max((group[2] for group in chosen), default=0), cost=cost)


@dataclass
class _Context:
    enabled: bool | None = None
    receipts: dict = field(default_factory=dict)
    origins: set = field(default_factory=set)
    lock: object = field(default_factory=threading.RLock, repr=False)

    def enforce(self):
        if self.enabled is None:
            value = os.environ.get('KAZMA_CRAWL_RESPECT_ROBOTS', '1').strip()
            if value not in ('0', '1'):
                raise AcquisitionError(FetchErrorCode.POLICY, 'KAZMA_CRAWL_RESPECT_ROBOTS must be 0 or 1.')
            self.enabled = value == '1'
        return self.enabled


active_robots: ContextVar[_Context | None] = ContextVar('active_robots', default=None)


@contextmanager
def crawl_policy(*, respect: bool | None = None, purpose: str | None = None, job=None):
    """A job freezes its setting; child acquisitions inherit it."""
    if purpose is not None and (active_robots.get() is not None or purpose not in _AUTOMATED):
        yield active_robots.get()
        return
    if respect is not None and type(respect) is not bool:
        raise AcquisitionError(FetchErrorCode.POLICY, 'respect_robots must be a boolean or null.')
    context = job if job is not None else _Context(respect)
    token = active_robots.set(context)
    try:
        yield context
    finally:
        active_robots.reset(token)


@asynccontextmanager
async def robots_for_job(purpose):
    with crawl_policy(purpose=purpose):
        yield


@dataclass
class _Pending:
    future: Future = field(default_factory=Future)
    waiters: int = 0


class _RobotsManager:
    def __init__(self, *, durable: bool = False):
        self.durable = durable
        self._lock = threading.RLock()
        self._cache = OrderedDict()
        self._pending = {}
        self._bytes = 0
        # Dedicated bounded metadata slots avoid nested acquisition deadlock.
        self._pool = _CapacityPool('robots metadata', 2, 8)

    async def _fetch(self, url, proxy):
        from kazma_core.web_acquire.http import fetch_public

        # Any host a redirect names (RFC 9309 2.3.1.2); every hop is checked
        # public by the fetch itself.
        return await fetch_public(url, max_bytes=500 * 1024, timeout=15, max_redirects=5,
            scope=lambda target: True, headers={'User-Agent': USER_AGENT},
            capacity_pool=self._pool, proxy_url=proxy)

    async def _load(self, url, proxy):
        token = _loading.set(True)
        try:
            result = await self._fetch(url, proxy)
            status = result.response.status_code
            if 400 <= status < 500 and status != 429:
                return _Rules('missing', status)  # RFC 9309: unavailable, no rules apply
            if status not in (200, 204) or result.truncated:
                return _Rules('unavailable', status, error='Robots response failed or exceeded 500KiB; crawl was refused.')
            body = result.response.content
            if len(body) > 500 * 1024 or b'\x00' in body or 'html' in result.response.headers.get('content-type', '').lower():
                return _Rules('unavailable', status, error='Robots response is not a bounded plain-text policy.')
            text = body.decode('utf-8-sig')
            if text.lstrip().startswith('<'):
                return _Rules('unavailable', status, error='Robots returned an HTML/XML page; crawl was refused.')
            return _parse(text, status)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Network/vault/parser errors must neither grant permission nor echo secrets.
            return _Rules('unavailable', error='Robots check failed; crawling was refused. Repair connectivity, policy or proxy settings and retry.')
        finally:
            _loading.reset(token)

    async def get(self, url, proxy):
        key = (_origin(url), acquisition_policy().cache_namespace,
               hashlib.sha256((proxy or '').encode()).digest())
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if not self.durable and cached is not None and cached[0] > now:
                self._cache.move_to_end(key)
                return cached[1], True
            if key not in self._pending:
                if len(self._pending) >= 8:
                    raise AcquisitionError(FetchErrorCode.LIMIT, 'Robots lookup queue is full; crawl was refused.', transient=True)
                self._pending[key] = _Pending()
                owner = True
            else:
                owner = False
            pending = self._pending[key]
            if not owner:
                if pending.waiters >= 32:
                    raise AcquisitionError(FetchErrorCode.LIMIT, 'Robots lookup waiter limit reached.', transient=True)
                pending.waiters += 1
        if not owner:
            try:
                async with asyncio.timeout(20):
                    return await asyncio.shield(asyncio.wrap_future(pending.future)), True
            except TimeoutError:
                raise AcquisitionError(FetchErrorCode.ROBOTS_UNAVAILABLE, 'Robots lookup deadline exceeded; crawl was refused.', transient=True) from None
            finally:
                with self._lock:
                    pending.waiters -= 1
        from kazma_core.web_acquire import robots_state
        from kazma_core.web_acquire.crawl_jobs import call

        try:
            if self.durable:
                saved, claim = await call(robots_state.begin, robots_state.identity(key))
                if claim is None:
                    pending.future.set_result(saved)
                    return saved, True
            value = await self._load(url, proxy)
            if self.durable:
                await call(robots_state.commit, robots_state.identity(key), claim, value)
            ttl = 30 if value.status == 'unavailable' else 600 if value.status == 'denied' else 3600
            with self._lock:
                if key in self._cache:
                    self._bytes -= self._cache.pop(key)[1].cost
                while self._cache and (len(self._cache) >= 128 or self._bytes + value.cost > 16 * 1024 * 1024):
                    self._bytes -= self._cache.popitem(last=False)[1][1].cost
                self._cache[key] = (time.monotonic() + ttl, value)
                self._bytes += value.cost
            pending.future.set_result(value)
            return value, False
        finally:
            if not pending.future.done():
                pending.future.set_result(_Rules('unavailable', error='Owning robots check was interrupted; crawl was refused.'))
            with self._lock:
                self._pending.pop(key, None)


_MANAGER = _RobotsManager(durable=True)


async def require_robots(url: str):
    context = active_robots.get()
    if context is None or not context.enforce() or _loading.get():
        return
    key = _origin(url)
    host = f'[{key[1]}]' if ':' in key[1] else key[1]
    origin = f'{key[0]}://{host}:{key[2]}'
    if urlsplit(url).path == '/robots.txt':
        return  # the protocol's own control resource is implicitly allowed
    with context.lock:
        if origin not in context.origins and len(context.origins) >= 64:
            raise AcquisitionError(FetchErrorCode.LIMIT, 'Crawl robots origin limit exceeded (64); dispatch was refused.')
        context.origins.add(origin)
    from kazma_core.proxy.client import get_active_proxy_url

    proxy = await asyncio.to_thread(get_active_proxy_url)
    policy, cached = await _MANAGER.get(origin + '/robots.txt', proxy)
    from kazma_core.web_acquire.trace import active_trace

    with context.lock:
        requests = context.receipts.get(origin, {}).get('metadata_requests', 0) + int(not cached)
        context.receipts[origin] = {'metadata_requests': requests, 'origin': origin, 'status': policy.status, 'http_status': policy.http_status,
            'checked_at': policy.checked_at, 'cached': cached, 'backend': 'httpx', 'proxy_used': bool(proxy),
            'usage': None, 'credits': None}
        trace = active_trace.get()
        if trace is not None:
            checks = trace.robots_checks
            own_requests = checks.get(origin, {}).get('metadata_requests', 0) + int(not cached)
            checks[origin] = {**context.receipts[origin], 'metadata_requests': own_requests}
    if policy.status == 'unavailable':
        raise AcquisitionError(FetchErrorCode.ROBOTS_UNAVAILABLE, policy.error, transient=True)
    if not policy.allows(url):
        raise AcquisitionError(FetchErrorCode.ROBOTS, policy.error or 'URL disallowed by robots.txt for KazmaBot; no content request was dispatched.')
    if policy.delay:
        origin_delay.set(max(origin_delay.get(), policy.delay))


def robots_evidence():
    context = active_robots.get()
    if context is None:
        return {'mode': 'not_requested', 'checks': []}
    from kazma_core.web_acquire.trace import active_trace

    with context.lock:
        trace = active_trace.get()
        checks = trace.robots_checks if trace is not None else context.receipts
        return {'mode': 'not_checked' if context.enabled is None else 'enforced' if context.enabled else 'disabled_by_operator',
                'checks': [dict(value) for value in checks.values()]}
