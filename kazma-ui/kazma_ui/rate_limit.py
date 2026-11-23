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
- Once Spaces is enforced (S4-04, ``kazma_core.spaces.fairness``): keyed by
  the PERSON the tenant middleware bound (``kazma_asker``), so a person's
  two browsers and an API token share one window, and every request also
  counts in its SPACE's window (``kazma_space``), which holds
  ``SPACE_WINDOW_FACTOR`` people's worth: a shared space's members together
  cannot take more than that of a bucket, and one space never crowds out the
  paid endpoints another uses. Both windows admit the request, or neither
  counts it.
"""

from __future__ import annotations

import asyncio
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
    from kazma_core.security.boot_guard import env_flag

    if env_flag("KAZMA_DEMO_MODE"):
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
    allowed, retry_after, _full = _allow_all([(key, limit)])
    return allowed, retry_after


def _window(key: tuple[str, str], now: float) -> deque[float]:
    """*key*'s window, pruned to the last minute (under ``_lock``)."""
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
    return win


def _allow_all(pairs: list[tuple[tuple[str, str], int]]) -> tuple[bool, float, tuple[str, str] | None]:
    """Every ``(key, limit)`` window admits one more request, and each counts
    it; or none does: ``(False, the wait until it admits one, the full
    window's key)``."""
    now = time.monotonic()
    with _lock:
        wins: list[deque[float]] = []
        for key, limit in pairs:
            # Defensive: a limit <= 0 would IndexError on win[0] below (latent,
            # found while hardening tests — _per_minute rejects <1).
            if limit <= 0:
                continue
            win = _window(key, now)
            if len(win) >= limit:
                return False, max(0.0, _WINDOW_SECONDS - (now - win[0])), key
            wins.append(win)
        for win in wins:
            win.append(now)
        return True, 0.0, None


def _person_and_space(request: Request) -> tuple[str, str]:
    """``(person, space)`` the tenant middleware bound for this request, once
    Spaces is enforced (S4-04); ``("", "")`` otherwise. In memory."""
    from kazma_core.spaces.scope import spaces_enforced

    if not spaces_enforced():
        return "", ""
    return str(request.scope.get("kazma_asker") or ""), str(request.scope.get("kazma_space") or "")


def _address(request: Request) -> str:
    """The proxy-aware client address alone, for a route with no caller
    identity to key by: a varied cookie would otherwise buy a fresh bucket."""
    try:
        from kazma_ui.auth import client_address

        return "ip:" + (client_address(request) or "unknown")
    except Exception:
        return "ip:" + (request.client.host if request.client else "unknown")


def rate_limit(bucket: str, default_per_minute: int, *, per_address: bool = False) -> Any:
    """FastAPI dependency factory — 429 with Retry-After when exhausted.

    ``per_address`` keys the window by the client address only: for a route
    reached before sign-in, where a cookie is whatever the caller sends.
    """

    async def _check(request: Request) -> None:
        # The secret's lookup is a settings read when none is cached (open
        # mode): off the loop too (audit 2026-10-26 AUD-111).
        if not await asyncio.to_thread(_enabled):
            return
        marker = (bucket, default_per_minute)
        checked = getattr(request.state, "_kazma_rate_limit_checks", None)
        if checked is not None and marker in checked:
            return
        # A settings read: off the loop (AUD-007; the docstring of _principal
        # says why a store lookup here must not block).
        limit = await asyncio.to_thread(_per_minute, bucket, default_per_minute)
        person, space = ("", "") if per_address else _person_and_space(request)
        if person:
            # A person's window, wherever they sign in from, and their space's
            # (Spaces S4-04): both admit it, or neither counts it.
            from kazma_core.spaces.fairness import SPACE_WINDOW_FACTOR

            pairs = [((bucket, f"person:{person}"), limit)]
            if space:
                pairs.append(((bucket, f"space:{space}"), limit * SPACE_WINDOW_FACTOR))
            allowed, retry_after, full = _allow_all(pairs)
            if not allowed and full is not None and full[1].startswith("space:"):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(f"This space reached its rate limit for {bucket} "
                            f"({limit * SPACE_WINDOW_FACTOR}/min, its people together). Please retry shortly."),
                    headers={"Retry-After": str(max(1, int(retry_after) + 1))},
                )
        else:
            caller = _address(request) if per_address else _principal(request)
            allowed, retry_after = _allow((bucket, caller), limit)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit reached for {bucket} ({limit}/min). "
                    "Please retry shortly."
                ),
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )
        if checked is None:
            checked = request.state._kazma_rate_limit_checks = set()
        checked.add(marker)

    setattr(_check, "_kazma_rate_limit", True)
    return _check
