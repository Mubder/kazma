"""Shared OAuth helpers (public base URL, authorize URL, id_token address).

A sign-in's state is ``kazma_core.security.oauth_attempts`` (one attempt per
sign-in, bound to the browser that started it, taken once at the callback).
It lived here in process memory until 2026-11-11: unbounded, bound to no
browser, and lost at every reload.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode


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
    from kazma_core.server_port import local_url

    return local_url()


def authorize_redirect(url: str, params: dict[str, str]) -> str:
    return f"{url}?{urlencode(params)}"
