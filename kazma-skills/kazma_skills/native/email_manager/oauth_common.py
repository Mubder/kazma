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


def address_from_id_token(id_token: Any) -> str:
    """The account address an OpenID ``id_token`` names, or ``""``.

    Read for display and for "send to myself" only. The token arrives from
    the provider's own token endpoint over TLS in the same exchange as the
    access token, so its signature is not what vouches for it here, and it
    decides no access. ``email`` first, then ``preferred_username`` (a
    Microsoft personal account's sign-in address) and ``upn``; a value that
    is not an address is ignored.
    """
    import base64
    import json

    parts = str(id_token or "").split(".")
    if len(parts) < 2 or not parts[1]:
        return ""
    body = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
    except (ValueError, UnicodeError):  # binascii.Error and JSONDecodeError are ValueErrors
        return ""
    if not isinstance(claims, dict):
        return ""
    for key in ("email", "preferred_username", "upn"):
        value = str(claims.get(key) or "").strip()
        if value.count("@") == 1 and " " not in value and "." in value.split("@", 1)[1]:
            return value.lower()
    return ""


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


def peek_state(state: str) -> dict[str, Any] | None:
    """Read OAuth state without consuming it (callback dispatch)."""
    meta = _oauth_states.get((state or "").strip())
    if not meta:
        return None
    if time.time() - meta.get("created_at", 0) > _STATE_TTL:
        return None
    return dict(meta)


def pop_state(state: str) -> dict[str, Any] | None:
    meta = _oauth_states.pop((state or "").strip(), None)
    if not meta:
        return None
    if time.time() - meta.get("created_at", 0) > _STATE_TTL:
        return None
    return meta


def authorize_redirect(url: str, params: dict[str, str]) -> str:
    return f"{url}?{urlencode(params)}"
