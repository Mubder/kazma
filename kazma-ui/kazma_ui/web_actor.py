"""Who made a change from the web: ``web:<role>:<session8>``, from the session.

The actor of a change is read from the request's session cookie, never from a
label the client sends (the approve route's rule, also the Memory page's).
Scheduled audit SCH-S12: cron jobs recorded no actor at all.
"""

from __future__ import annotations

from fastapi import Request

__all__ = ["web_actor"]


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
    role = str(payload.get("role") or "viewer")
    return f"web:{role}:{str(payload.get('session_id') or sid)[:8]}"
