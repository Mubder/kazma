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
from collections import deque
from typing import Any

from fastapi import HTTPException, Request, status

__all__ = ["rate_limit"]

_WINDOW_SECONDS = 60.0
# Bound the tracking dict (one deque per bucket+principal). Cleared wholesale
# when exceeded — a fresh window costs one minute of leniency, never a leak.
_MAX_TRACKED_KEYS = 10_000

_windows: dict[tuple[str, str], deque[float]] = {}
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
    try:
        from kazma_ui.auth import SECRET_COOKIE, SESSION_COOKIE

        cookies = (SESSION_COOKIE, SECRET_COOKIE)
    except Exception:
        cookies = ("kazma-session", "kazma-secret")
    for name in cookies:
        v = request.cookies.get(name)
        if v:
            return "cookie:" + hashlib.sha256(v.encode()).hexdigest()[:16]
    authz = request.headers.get("authorization")
    if authz:
        return "token:" + hashlib.sha256(authz.encode()).hexdigest()[:16]
    return "ip:" + (request.client.host if request.client else "unknown")


def _allow(key: tuple[str, str], limit: int) -> tuple[bool, float]:
    now = time.monotonic()
    with _lock:
        if len(_windows) > _MAX_TRACKED_KEYS:
            _windows.clear()
        win = _windows.setdefault(key, deque())
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
