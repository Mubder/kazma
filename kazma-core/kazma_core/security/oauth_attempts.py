"""Every OAuth sign-in in progress, for every provider: one record, one rule.

GitHub's sign-in kept ONE install-wide state (``connectors.github.oauth_state``)
compared with ``!=``: a second start replaced the first, and a state that
reached another browser finished the sign-in there. The mail and calendar
sign-ins (Gmail, Google Calendar, Microsoft mail and Outlook Calendar) kept
theirs in process memory, unbounded and bound to no browser. Each start was a
GET that wrote state, so a link on any page could start one (security audit
2026-11-10). OIDC's fix (FA-133, ``kazma_core.security.oidc``) is the shape
every provider takes now:

* a state per attempt, stored only as its hash (:func:`_state_id`), with the
  provider and what its callback needs (the redirect URI, an account name,
  the calendar purpose, the page to come back to);
* kept :data:`TTL_S` (ten minutes), at most :data:`MAX_PENDING` at once -- past
  that the one closest to expiry goes first;
* bound to the browser that started it: the start route sets a cookie named
  for the attempt (:func:`binding_cookie_name`) whose value is the state,
  sent to the callback's path only. :func:`take` refuses a state the browser
  does not hold, compared with :func:`hmac.compare_digest`, before the store
  is touched;
* taken once: one compare-and-set (``atomic_update``) takes it out, so a
  second callback with it -- at the same moment or later -- finds nothing.

The HTTP side (the start route's answer, the cookie, the origin a sign-in
must start from) is ``kazma_ui.oauth_sign_in``. Every read and write here is
a settings-store round trip: call from a thread.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "MAX_PENDING",
    "PENDING_KEY",
    "PROVIDERS",
    "REFUSAL_REASONS",
    "TTL_S",
    "AttemptRefused",
    "begin",
    "binding_cookie_name",
    "binding_cookie_path",
    "safe_return_path",
    "take",
]

#: One settings key for every provider's sign-ins in progress: ``{state id:
#: {"provider", "exp", ...what the callback needs}}``, changed only by
#: ``atomic_update`` (a RUNTIME key in ``settings_restore``).
PENDING_KEY = "auth.oauth.pending"
TTL_S = 600
MAX_PENDING = 32

#: The sign-ins that keep an attempt here. A callback names the ones it takes.
PROVIDERS = ("github", "gmail", "google_calendar", "microsoft")

#: Why :func:`take` refused, as a code the pages word.
REFUSAL_REASONS = ("other_browser", "unknown", "expired", "other_provider")


class AttemptRefused(PermissionError):
    """A callback's state is not one this browser started and may finish now."""

    def __init__(self, reason: str) -> None:
        if reason not in REFUSAL_REASONS:
            raise ValueError(f"unknown refusal reason: {reason!r}")
        super().__init__(reason)
        self.reason = reason


def _state_id(state: str) -> str:
    """An attempt's settings id: the state's hash, so the state itself is
    kept nowhere but in the browser that started it."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()[:32]


def binding_cookie_name(state: str) -> str:
    """The cookie that binds an attempt to the browser that started it.

    One per attempt (named by its state), so two sign-ins started in one
    browser both finish. Its value is the state.
    """
    return f"kazma-oauth-{_state_id(state)[:16]}"


def binding_cookie_path(callback_url: str) -> str:
    """The binding cookie goes to the callback only: the path the provider
    sends the browser back to."""
    try:
        return urlsplit(callback_url).path or "/"
    except ValueError:
        return "/"


def _live(curr: Any, now: float) -> dict[str, dict[str, Any]]:
    """The attempts in *curr* that have not expired."""
    if not isinstance(curr, dict):
        return {}
    live: dict[str, dict[str, Any]] = {}
    for sid, entry in curr.items():
        try:
            if isinstance(entry, dict) and float(entry.get("exp") or 0) > now:
                live[str(sid)] = dict(entry)
        except (TypeError, ValueError):
            continue
    return live


def begin(provider: str, **data: str) -> str:
    """Record a sign-in in progress and return its state.

    *data* is what the callback needs back (strings, nothing secret: the
    redirect URI, an account name, a purpose, a page path). A settings
    write: call from a thread.
    """
    if provider not in PROVIDERS:
        raise ValueError(f"unknown OAuth provider: {provider!r}")
    clean = {str(k): str(v) for k, v in data.items() if v is not None}
    if {"provider", "exp"} & set(clean):
        raise ValueError("'provider' and 'exp' are the attempt's own fields")
    state = secrets.token_urlsafe(24)
    from kazma_core.config_store import get_config_store

    def _add(curr: Any) -> dict[str, dict[str, Any]]:
        now = time.time()
        pending = _live(curr, now)
        while len(pending) >= MAX_PENDING:
            pending.pop(min(pending, key=lambda k: float(pending[k].get("exp") or 0)))
        pending[_state_id(state)] = {"provider": provider, "exp": now + TTL_S, **clean}
        return pending

    get_config_store().atomic_update(PENDING_KEY, _add, "auth")
    return state


def take(state: str, *, bound_state: str, providers: Iterable[str]) -> dict[str, Any]:
    """Take a callback's attempt out of the store, once; raise
    :class:`AttemptRefused` when it may not finish.

    *bound_state* is the browser's binding cookie (:func:`binding_cookie_name`):
    a state another browser started is refused, compared in constant time,
    before anything is read. Then one compare-and-set takes the attempt out,
    whatever happens next -- a used, unknown or expired state finds nothing to
    finish, and an attempt of a provider *providers* does not name is refused
    (and spent). A settings write: call from a thread.
    """
    state = (state or "").strip()
    if not state or not bound_state or not hmac.compare_digest(
        bound_state.encode("utf-8"), state.encode("utf-8")
    ):
        raise AttemptRefused("other_browser")
    from kazma_core.config_store import get_config_store

    sid = _state_id(state)
    taken: dict[str, Any] = {}

    def _take(curr: Any) -> dict[str, dict[str, Any]]:
        now = time.time()
        pending = curr if isinstance(curr, dict) else {}
        entry = pending.get(sid)
        taken.clear()
        if isinstance(entry, dict):
            taken.update(entry)
        rest = _live(pending, now)
        rest.pop(sid, None)
        return rest

    get_config_store().atomic_update(PENDING_KEY, _take, "auth")
    if not taken:
        raise AttemptRefused("unknown")
    try:
        if time.time() > float(taken.get("exp") or 0):
            raise AttemptRefused("expired")
    except (TypeError, ValueError):
        raise AttemptRefused("expired") from None
    if taken.get("provider") not in set(providers):
        raise AttemptRefused("other_provider")
    taken.pop("exp", None)
    return taken


#: Characters a browser reads as a separator, or drops, inside a URL: a
#: return path holding one can name another site ("/\\evil.example").
_UNSAFE_PATH_CHARS = frozenset(chr(c) for c in range(0x20)) | {"\x7f", "\\"}


def safe_return_path(raw: Any, default: str = "/") -> str:
    """A path on this server to come back to after a sign-in, or *default*.

    The server-side twin of ``static/js/safe_next.js`` (``kazmaSafeNext``):
    a path that starts with one ``/``, holds no backslash or control
    character, names no scheme or host, and keeps its query (never its
    fragment). Anything else is *default*.
    """
    text = str(raw or "").strip()
    if not text.startswith("/") or text.startswith("//"):
        return default
    if any(ch in _UNSAFE_PATH_CHARS for ch in text):
        return default
    try:
        parts = urlsplit(text)
    except ValueError:
        return default
    if parts.scheme or parts.netloc or not parts.path.startswith("/") or parts.path.startswith("//"):
        return default
    return parts.path + (f"?{parts.query}" if parts.query else "")
