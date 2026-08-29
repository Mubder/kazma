"""Per-principal sliding-window rate limiting for expensive API endpoints.

Protects paid LLM / crawl / fan-out endpoints against a single leaked
session cookie or API token looping them unbounded (audit M14).

Behaviour:
- Keyed per principal: session cookie value (hashed) > Authorization
  header (hashed) > client IP.
- Active ONLY when authentication is enabled (KAZMA_SECRET set) and not in
  demo mode — open local/dev instances and the test suite skip it
  entirely; the threat model is a leaked credential on an authenticated
  deployment, which is where unbounded paid API spend actually hurts.
- In-process sliding window (per replica — each replica limits
  independently). ConfigStore key ``api.rate_limit.<bucket>_per_minute``
  overrides the default live; env ``KAZMA_RATE_LIMIT_ENABLED=0`` is a
  global kill switch.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import OrderedDict, deque
from typing import Any

from fastapi import HTTPException, Request, status

__all__ = ["rate_limit"]

_WINDOW_SECONDS = 60.0
# Bound the tracking dict (one deque per bucket+principal). Evicted
# least-recently-touched first (audit F-13): this used to `.clear()` the whole
# map at the cap, so anyone able to mint 10k distinct keys could reset every
# other principal's window — including their own.
_MAX_TRACKED_KEYS = 10_000

_windows: OrderedDict[tuple[str, str], deque[float]] = OrderedDict()
_lock = threading.Lock()


def _enabled() -> bool:
    if os.environ.get("KAZMA_RATE_LIMIT_ENABLED", "").lower() in ("0", "false", "no"):
        return False
    if os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes"):
        return False
    try:
        from kazma_ui.auth import get_kazma_secret

        return bool(get_kazma_secret())
    except Exception:
        return False


def _per_minute(bucket: str, default: int) -> int:
    try:
        from kazma_core.config_store import get_config_store

        v = get_config_store().get(f"api.rate_limit.{bucket}_per_minute")
        if isinstance(v, (int, float)) and int(v) > 0:
            return int(v)
    except Exception:
        pass
    return default


def _principal(request: Request) -> str:
    """Stable identity for one caller.

    Cookie/token identity is combined with the client address rather than used
    alone (audit F-13): keying on the raw cookie string let a caller claim a
    fresh bucket per request just by varying the value. Pairing it with the
    address caps how far that gets them, and the address is proxy-aware (audit
    F-01/F-12) so a reverse proxy no longer collapses every caller into one
    shared bucket.

    Deliberately does NOT validate the session here — this runs as a FastAPI
    dependency on the event loop, and a ConfigStore lookup per request would
    reintroduce the blocking-I/O problem fixed in F-06. Bucket *isolation* is
    what matters here, not authenticity; the auth middleware has already
    decided whether the caller may reach the endpoint at all.
    """
    try:
        from kazma_ui.auth import client_address

        addr = client_address(request) or "unknown"
    except Exception:
        addr = (request.client.host if request.client else "unknown")

    try:
        from kazma_ui.auth import SECRET_COOKIE, SESSION_COOKIE

        cookies = (SESSION_COOKIE, SECRET_COOKIE)
    except Exception:
        cookies = ("kazma-session", "kazma-secret")
    for name in cookies:
        v = request.cookies.get(name)
        if v:
            return f"cookie:{hashlib.sha256(v.encode()).hexdigest()[:16]}@{addr}"
    authz = request.headers.get("authorization")
    if authz:
        return f"token:{hashlib.sha256(authz.encode()).hexdigest()[:16]}@{addr}"
    return "ip:" + addr


def _allow(key: tuple[str, str], limit: int) -> tuple[bool, float]:
    # Defensive: a limit <= 0 would IndexError on win[0] below (latent, found
    # while hardening tests — _per_minute rejects <1 so prod can't reach it).
    if limit <= 0:
        return True, 0.0
    now = time.monotonic()
    with _lock:
        win = _windows.get(key)
        if win is None:
            win = _windows[key] = deque()
        _windows.move_to_end(key)  # most-recently touched last
        # Evict the least-recently-touched entries only — never live windows
        # belonging to other principals (audit F-13).
        while len(_windows) > _MAX_TRACKED_KEYS:
            stale_key, _ = _windows.popitem(last=False)
            if stale_key == key:  # pathological cap of 0/1; keep our own
                _windows[key] = win
                break
        while win and now - win[0] >= _WINDOW_SECONDS:
            win.popleft()
        if len(win) >= limit:
            return False, max(0.0, _WINDOW_SECONDS - (now - win[0]))
        win.append(now)
        return True, 0.0


def rate_limit(bucket: str, default_per_minute: int) -> Any:
    """FastAPI dependency factory — 429 with Retry-After when exhausted."""

    async def _check(request: Request) -> None:
        if not _enabled():
            return
        limit = _per_minute(bucket, default_per_minute)
        allowed, retry_after = _allow((bucket, _principal(request)), limit)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit reached for {bucket} ({limit}/min). "
                    "Please retry shortly."
                ),
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )

    return _check
