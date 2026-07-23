"""Shared OAuth helpers (state CSRF + public base URL)."""

from __future__ import annotations

import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

# state -> {provider, created_at, extra}
_oauth_states: dict[str, dict[str, Any]] = {}
_STATE_TTL = 900


def public_base_url(request_base: str | None = None) -> str:
    """Prefer KAZMA_PUBLIC_URL, else request base, else localhost."""
    env = (os.environ.get("KAZMA_PUBLIC_URL") or "").strip().rstrip("/")
    if env:
        return env
    if request_base:
        return str(request_base).rstrip("/")
    port = (os.environ.get("KAZMA_PORT") or "9090").strip()
    return f"http://127.0.0.1:{port}"


def new_state(provider: str, **extra: Any) -> str:
    # prune expired
    now = time.time()
    dead = [k for k, v in _oauth_states.items() if now - v.get("created_at", 0) > _STATE_TTL]
    for k in dead:
        _oauth_states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = {"provider": provider, "created_at": now, **extra}
    return state


def pop_state(state: str) -> dict[str, Any] | None:
    meta = _oauth_states.pop((state or "").strip(), None)
    if not meta:
        return None
    if time.time() - meta.get("created_at", 0) > _STATE_TTL:
        return None
    return meta


def authorize_redirect(url: str, params: dict[str, str]) -> str:
    return f"{url}?{urlencode(params)}"
