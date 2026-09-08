"""Google OAuth 2.0 for Calendar API — reuses the Gmail OAuth web client.

Redirect URI is the Gmail callback (``/api/email/oauth/gmail/callback``)
so operators do not have to add a second URI in Cloud Console. The
callback dispatches on OAuth ``state.provider == google_calendar``.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from kazma_skills.native.calendar.credentials import (
    persist_google_tokens,
    scopes_include_google_calendar,
)
from kazma_skills.native.email_manager.oauth_common import (
    authorize_redirect,
    new_state,
    pop_state,
)

logger = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
CALENDAR_SCOPES = " ".join(
    [
        CALENDAR_SCOPE,
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]
)
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

SCOPE_FIX_HINT = (
    "Google Calendar token is missing calendar scopes, or the Calendar API "
    "is not enabled. In Google Cloud Console → APIs & Services: enable "
    "Google Calendar API, add scope …/auth/calendar, add yourself as a "
    "Test user. Then Settings → Email → Connect Calendar (or Connect with "
    "Google — Calendar is included on that consent too)."
)


def _client_id() -> str:
    from kazma_skills.native.email_manager.oauth_gmail import _client_id as _gid

    return _gid()


def _client_secret() -> str:
    from kazma_skills.native.email_manager.oauth_gmail import _client_secret as _gsec

    return _gsec()


def _redirect_uri(request_base: str | None = None) -> str:
    from kazma_skills.native.email_manager.oauth_gmail import gmail_redirect_uri

    return gmail_redirect_uri(request_base)


async def probe_calendar_api(client: httpx.AsyncClient, access: str) -> tuple[bool, str]:
    """Soft probe: (ok, reason). Never raises."""
    try:
        r = await client.get(
            f"{CALENDAR_API}/users/me/calendarList",
            params={"maxResults": "1"},
            headers={"Authorization": f"Bearer {access}"},
        )
        if r.status_code == 200:
            return True, "ok"
        reason = ""
        try:
            data = r.json() or {}
            err = data.get("error") or {}
            if isinstance(err, dict):
                reason = str(err.get("status") or err.get("message") or "")
                errors = err.get("errors") or []
                if errors and isinstance(errors[0], dict):
                    reason = str(errors[0].get("reason") or reason)
        except Exception:
            pass
        return False, reason or f"HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)


def start_google_calendar_oauth(request_base: str | None = None) -> dict[str, Any]:
    """Return Google authorize URL for Calendar-only consent."""
    from kazma_skills.native.email_manager.oauth_gmail import _client_shape_error

    cid = _client_id()
    secret = _client_secret()
    if not cid:
        return {
            "ok": False,
            "code": "missing_client_id",
            "error": (
                "Google OAuth Client ID is not set. In Settings → Email, "
                "paste the Google Cloud OAuth Web client ID and secret "
                "(same client as Gmail), Save, then Connect Calendar."
            ),
        }
    if not secret:
        return {
            "ok": False,
            "code": "missing_client_secret",
            "error": (
                "Google OAuth Client secret is not set. Re-save Client ID + "
                "secret in Settings → Email, then Connect Calendar."
            ),
        }
    shape = _client_shape_error(cid, secret)
    if shape:
        return {"ok": False, "code": "malformed_client", "error": shape}
    redirect = _redirect_uri(request_base)
    state = new_state("google_calendar", redirect_uri=redirect)
    url = authorize_redirect(
        AUTH_URL,
        {
            "client_id": cid,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": CALENDAR_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        },
    )
    return {
        "ok": True,
        "authorize_url": url,
        "redirect_uri": redirect,
        "scopes": CALENDAR_SCOPES,
        "message": "Redirect user to authorize_url",
    }


async def finish_google_calendar_oauth(code: str, state: str) -> dict[str, Any]:
    """Exchange code; store only if Calendar API probe succeeds."""
    meta = pop_state(state)
    if not meta or meta.get("provider") != "google_calendar":
        return {
            "ok": False,
            "error": "Invalid or expired OAuth state. Try Connect Calendar again.",
        }
    cid = _client_id()
    secret = _client_secret()
    redirect = meta.get("redirect_uri") or _redirect_uri()
    if not cid or not secret:
        return {"ok": False, "error": "Google OAuth client not configured"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": cid,
                "client_secret": secret,
                "redirect_uri": redirect,
                "grant_type": "authorization_code",
            },
        )
        payload = r.json() if r.content else {}
        if r.status_code >= 400:
            return {
                "ok": False,
                "error": payload.get("error_description")
                or payload.get("error")
                or f"Token exchange failed ({r.status_code})",
            }
        access = payload.get("access_token") or ""
        refresh = payload.get("refresh_token") or ""
        scope_str = str(payload.get("scope") or "")
        if not access:
            return {"ok": False, "error": "No access_token from Google"}
        if not scope_str:
            try:
                ti = await client.get(TOKENINFO_URL, params={"access_token": access})
                if ti.status_code < 400 and ti.content:
                    scope_str = str((ti.json() or {}).get("scope") or "")
            except Exception as exc:
                logger.debug("[calendar.oauth] tokeninfo: %s", exc)

        email_addr = ""
        try:
            u = await client.get(
                USERINFO_URL, headers={"Authorization": f"Bearer {access}"}
            )
            if u.status_code < 400:
                email_addr = (u.json() or {}).get("email") or ""
        except Exception:
            pass

        ok, reason = await probe_calendar_api(client, access)
        if not ok:
            logger.warning(
                "[calendar.oauth] Calendar probe failed reason=%s scope=%r",
                reason,
                scope_str,
            )
            persist_google_tokens("", "", "", scope_str, probe_ok=reason)
            return {
                "ok": False,
                "code": "insufficient_scopes",
                "granted_scopes": scope_str,
                "error": SCOPE_FIX_HINT,
            }
        if not scopes_include_google_calendar(scope_str):
            logger.info(
                "[calendar.oauth] probe OK but scope string unclear: %r", scope_str
            )

    persist_google_tokens(
        access, refresh, email_addr, scope_str, probe_ok="ok"
    )
    return {
        "ok": True,
        "email": email_addr,
        "scopes": scope_str,
        "message": (
            f"Google Calendar connected"
            f"{f' as {email_addr}' if email_addr else ''}."
        ),
    }


async def refresh_google_calendar_access_token(
    refresh_token: str,
    *,
    client_id: str = "",
    client_secret: str = "",
) -> tuple[str, str]:
    """Return (access_token, refresh_token). Also persists."""
    cid = client_id or _client_id()
    secret = client_secret or _client_secret()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            TOKEN_URL,
            data={
                "client_id": cid,
                "client_secret": secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        payload = r.json() if r.content else {}
        if r.status_code >= 400:
            raise RuntimeError(
                payload.get("error_description")
                or payload.get("error")
                or "Google Calendar token refresh failed"
            )
        access = payload.get("access_token") or ""
        new_refresh = payload.get("refresh_token") or refresh_token
        scope_str = str(payload.get("scope") or "")
        if not access:
            raise RuntimeError("No access_token on Calendar refresh")
        persist_google_tokens(access, new_refresh, scopes=scope_str, probe_ok="ok")
        return access, new_refresh
