"""Who made a change from the web: ``web:<role>:<session label>``, from the
session.

The actor of a change is read from the request's session cookie, never from a
label the client sends (the approve route's rule, also the Memory page's).
Scheduled audit SCH-S12: cron jobs recorded no actor at all. The session is
named by ``web_sessions.session_label`` (the start of its hash), never by the
cookie's own text, which is the sign-in credential.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import Request

__all__ = ["actor_for_session", "web_actor"]


def actor_for_session(session_id: str, payload: Mapping[str, Any] | None, *,
                      default_role: str = "viewer") -> str:
    """``web:<role>:<label>`` for the session *session_id* (the cookie's
    value), whose stored record is *payload*. The one builder of a web
    session's actor: the Memory page's trail, the approve route, Time Travel's
    audit and every route that calls ``web_actor``."""
    from kazma_core.security.web_sessions import session_label

    role = str((payload or {}).get("role") or default_role)
    return f"web:{role}:{session_label(session_id)}"


def web_actor(request: Request) -> str:
    """The signed-in caller, as recorded on a change. Blocking (a session
    read): async routes call it through ``asyncio.to_thread``."""
    from kazma_core.security.web_sessions import SESSION_COOKIE, get_session_payload

    sid = request.cookies.get(SESSION_COOKIE) or ""
    if not sid:
        return "web:token" if request.headers.get("authorization") else "web:operator"
    try:
        payload = get_session_payload(sid) or {}
    except Exception:  # noqa: BLE001 — an unreadable session still names the change
        payload = {}
    return actor_for_session(sid, payload)
