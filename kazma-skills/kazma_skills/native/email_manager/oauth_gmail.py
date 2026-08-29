"""Google OAuth 2.0 (authorization code) for Gmail API — Workspace-friendly."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from kazma_skills.native.email_manager.credentials import vault_store
from kazma_skills.native.email_manager.oauth_common import (
    authorize_redirect,
    new_state,
    pop_state,
    public_base_url,
)

logger = logging.getLogger(__name__)

# Mail scopes required for list/read/send/modify. gmail.modify covers read+labels;
# gmail.send is redundant but listed so consent screen shows "Send email".
# Do NOT rely on openid/email alone — that yields ACCESS_TOKEN_SCOPE_INSUFFICIENT.
# drive.file is for the offsite backup provider (cloud_sync.py GoogleDriveSync).
GMAIL_MAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive.file",
)
GMAIL_SCOPES = " ".join(
    [
        *GMAIL_MAIL_SCOPES,
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]
)

SCOPE_FIX_HINT = (
    "Gmail token is missing mail scopes. In Google Cloud Console → APIs & Services → "
    "OAuth consent screen → Data access / Scopes, add: "
    "…/auth/gmail.modify and …/auth/gmail.send (or …/auth/gmail.readonly for read-only), "
    "plus …/auth/drive.file if you use Google Drive offsite backups. "
    "Enable Gmail API. Add yourself as a Test user. Then Settings → Email → Disconnect, "
    "Connect with Google again, and approve Gmail access (not only your email address)."
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1"


def _client_id() -> str:
    from kazma_skills.native.email_manager.credentials import cred

    return cred("EMAIL_GMAIL_CLIENT_ID", "email.gmail.client_id") or cred(
        "GOOGLE_OAUTH_CLIENT_ID", "email.gmail.client_id"
    )


def _client_secret() -> str:
    from kazma_skills.native.email_manager.credentials import cred

    return cred("EMAIL_GMAIL_CLIENT_SECRET", "email.gmail.client_secret") or cred(
        "GOOGLE_OAUTH_CLIENT_SECRET", "email.gmail.client_secret"
    )


def gmail_redirect_uri(request_base: str | None = None) -> str:
    custom = (os.environ.get("EMAIL_GMAIL_REDIRECT_URI") or "").strip()
    if custom:
        return custom
    return f"{public_base_url(request_base)}/api/email/oauth/gmail/callback"


def scopes_include_gmail_mail(scope_str: str) -> bool:
    """True if granted scopes allow Gmail list/read (modify, readonly, or full mail)."""
    s = (scope_str or "").lower().replace("%2f", "/")
    parts = {p.strip() for p in s.replace(",", " ").split() if p.strip()}
    mail_markers = (
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.labels",
        "https://mail.google.com/",
        "https://mail.google.com",
        "gmail.modify",
        "gmail.readonly",
        "mail.google.com",
    )
    return any(m in s or m in parts for m in mail_markers)


#: A Google OAuth client ID always ends with this; a client secret starts
#: with ``GOCSPX-``. The two sit next to each other in the Cloud Console and
#: look alike, so pasting the secret into both fields is an easy slip.
CLIENT_ID_SUFFIX = ".apps.googleusercontent.com"
CLIENT_SECRET_PREFIX = "GOCSPX-"


def _client_shape_error(client_id: str, client_secret: str) -> str:
    """Operator-facing message when the ID/secret pair is obviously wrong.

    Returns ``""`` when the shapes are plausible. Deliberately format-only —
    it cannot tell whether a well-formed client still exists in the Cloud
    Console, just that this value could never be a client ID.
    """
    cid, sec = (client_id or "").strip(), (client_secret or "").strip()
    if cid and cid == sec:
        return (
            "Client ID and Client Secret hold the same value — one was pasted "
            "into both fields. In Google Cloud Console → APIs & Services → "
            f"Credentials, the Client ID ends with '{CLIENT_ID_SUFFIX}' and "
            f"the secret starts with '{CLIENT_SECRET_PREFIX}'. Re-save both in "
            "Settings → Email → OAuth."
        )
    if cid.startswith(CLIENT_SECRET_PREFIX):
        return (
            "The saved Client ID is actually the Client SECRET (it starts with "
            f"'{CLIENT_SECRET_PREFIX}'). The Client ID is the longer value "
            f"ending in '{CLIENT_ID_SUFFIX}'. Re-save both in Settings → "
            "Email → OAuth."
        )
    if cid and not cid.endswith(CLIENT_ID_SUFFIX):
        return (
            f"The saved Client ID does not end with '{CLIENT_ID_SUFFIX}', so "
            "Google will reject it as 'invalid_client'. Copy it from Google "
            "Cloud Console → APIs & Services → Credentials."
        )
    return ""


def start_gmail_oauth(request_base: str | None = None) -> dict[str, Any]:
    """Return Google authorize URL for browser redirect."""
    cid = _client_id()
    if not cid:
        return {
            "ok": False,
            "code": "missing_client_id",
            "error": (
                "Google OAuth Client ID is not set. In Settings → Email → OAuth, "
                "paste your Google Cloud OAuth Web client ID and secret, click "
                "Save OAuth client, then Connect with Google. "
                "Or set EMAIL_GMAIL_CLIENT_ID + EMAIL_GMAIL_CLIENT_SECRET."
            ),
        }
    secret = _client_secret()
    if not secret:
        return {
            "ok": False,
            "code": "missing_client_secret",
            "error": (
                "Google OAuth Client secret is not set. Re-enter Client ID + secret "
                "in Settings → Email, click Save OAuth client, then Connect again."
            ),
        }
    # Stored credentials can predate the save-time format check, and Google's
    # own failure for a bad client ID is an opaque "Error 401: invalid_client
    # — The OAuth client was not found" on its consent page. Catch it here so
    # the message names the field instead.
    shape = _client_shape_error(cid, secret)
    if shape:
        return {"ok": False, "code": "malformed_client", "error": shape}
    redirect = gmail_redirect_uri(request_base)
    state = new_state("gmail", redirect_uri=redirect)
    # Persist client for callback process
    os.environ["EMAIL_GMAIL_CLIENT_ID"] = cid
    os.environ["EMAIL_GMAIL_CLIENT_SECRET"] = secret
    # prompt=consent + access_type=offline → refresh token every reconnect
    # Do NOT set include_granted_scopes — partial prior grants (email only) caused
    # tokens without gmail.modify and Gmail API 403 insufficientPermissions.
    url = authorize_redirect(
        AUTH_URL,
        {
            "client_id": cid,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": GMAIL_SCOPES,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        },
    )
    return {
        "ok": True,
        "authorize_url": url,
        "redirect_uri": redirect,
        "scopes": GMAIL_SCOPES,
        "message": "Redirect user to authorize_url",
    }


async def _fetch_granted_scopes(client: httpx.AsyncClient, access: str) -> str:
    try:
        ti = await client.get(TOKENINFO_URL, params={"access_token": access})
        if ti.status_code < 400 and ti.content:
            data = ti.json() or {}
            return str(data.get("scope") or "")
    except Exception as exc:
        logger.debug("[email.oauth] tokeninfo: %s", exc)
    return ""


async def _probe_gmail_api(client: httpx.AsyncClient, access: str) -> dict[str, Any]:
    """Call users/me/profile; return {ok, status_code, body_snip}."""
    try:
        r = await client.get(
            f"{GMAIL_API}/users/me/profile",
            headers={"Authorization": f"Bearer {access}"},
        )
        snip = (r.text or "")[:200]
        if r.status_code < 400:
            return {"ok": True, "status_code": r.status_code, "email": (r.json() or {}).get("emailAddress")}
        return {"ok": False, "status_code": r.status_code, "body": snip}
    except Exception as exc:
        return {"ok": False, "status_code": 0, "body": str(exc)}


async def _probe_drive_api(client: httpx.AsyncClient, access: str) -> tuple[bool, str]:
    """Soft-probe the Drive API so the backup card can show the real state.

    Drive is optional for Gmail — a failure here must NEVER fail the connect.
    Returns (ok, reason) where reason is a Google error reason or a short message.
    """
    try:
        r = await client.get(
            "https://www.googleapis.com/drive/v3/about",
            params={"fields": "user"},
            headers={"Authorization": f"Bearer {access}"},
        )
        if r.status_code == 200:
            return True, "ok"
        reason = ""
        try:
            from kazma_core.backup.cloud_sync import google_error_reason

            reason = google_error_reason(r)
        except Exception:
            pass
        return False, reason or f"HTTP {r.status_code}"
    except Exception as exc:
        return False, str(exc)


async def finish_gmail_oauth(code: str, state: str) -> dict[str, Any]:
    """Exchange code for tokens; store only if Gmail API scopes work."""
    meta = pop_state(state)
    if not meta or meta.get("provider") != "gmail":
        return {"ok": False, "error": "Invalid or expired OAuth state. Try Connect Gmail again."}
    cid = _client_id()
    secret = _client_secret()
    redirect = meta.get("redirect_uri") or gmail_redirect_uri()
    if not cid or not secret:
        return {"ok": False, "error": "Gmail OAuth client not configured"}
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
            scope_str = await _fetch_granted_scopes(client, access)

        # Profile email (works with userinfo.email alone — not proof of Gmail access)
        email_addr = ""
        try:
            u = await client.get(
                USERINFO_URL,
                headers={"Authorization": f"Bearer {access}"},
            )
            if u.status_code < 400:
                email_addr = (u.json() or {}).get("email") or ""
        except Exception:
            pass

        probe = await _probe_gmail_api(client, access)
        scope_ok = scopes_include_gmail_mail(scope_str) and probe.get("ok")
        if not probe.get("ok"):
            # Explicit fail — do not store a "connected" token that only has openid/email
            logger.warning(
                "[email.oauth] Gmail probe failed status=%s scope=%r body=%s",
                probe.get("status_code"),
                scope_str,
                probe.get("body"),
            )
            return {
                "ok": False,
                "code": "insufficient_scopes",
                "granted_scopes": scope_str,
                "error": SCOPE_FIX_HINT,
            }
        if not scopes_include_gmail_mail(scope_str):
            # Profile worked but scope string odd — still ok if probe passed
            logger.info(
                "[email.oauth] Gmail probe OK but scope string unclear: %r", scope_str
            )

        if probe.get("email") and not email_addr:
            email_addr = str(probe["email"])

        # Soft-verify the drive.file scope for the offsite backup provider.
        # The Gmail probe above proves mail scopes only; a consent screen that
        # lacks …/auth/drive.file silently strips it, and Google never adds
        # scopes to existing grants — the backup card needs to know.
        drive_ok, drive_reason = await _probe_drive_api(client, access)
        vault_store(
            "email.gmail.drive_ok",
            "ok" if drive_ok else drive_reason,
            category="email",
        )

    persist_gmail_tokens(access, refresh, email_addr, scopes=scope_str)
    return {
        "ok": True,
        "email": email_addr,
        "scopes": scope_str,
        "scopes_ok": True if scope_ok else bool(probe.get("ok")),
        "drive_ok": drive_ok,
        "drive_error": None if drive_ok else drive_reason,
        "message": (
            f"Gmail connected via OAuth{f' as {email_addr}' if email_addr else ''}. "
            "Mail scopes verified."
        ),
    }


def persist_gmail_tokens(
    access: str,
    refresh: str = "",
    email: str = "",
    scopes: str = "",
) -> None:
    if access:
        os.environ["EMAIL_GMAIL_ACCESS_TOKEN"] = access
        vault_store("email.gmail.access_token", access, category="email")
    if refresh:
        os.environ["EMAIL_GMAIL_REFRESH_TOKEN"] = refresh
        vault_store("email.gmail.refresh_token", refresh, category="email")
    if email:
        os.environ["EMAIL_GMAIL_ADDRESS"] = email
        vault_store("email.gmail.address", email, category="email")
    if scopes:
        os.environ["EMAIL_GMAIL_SCOPES"] = scopes
        vault_store("email.gmail.scopes", scopes, category="email")
    # Mark auth mode (wins over IMAP/POP until user reconnects protocol)
    os.environ["EMAIL_GMAIL_AUTH"] = "oauth"
    vault_store("email.gmail.auth", "oauth", category="email")
    if refresh:
        # When this grant was minted. Google expires refresh tokens after 7
        # days for projects whose OAuth publishing status is "Testing", which
        # is a deliberate choice on this install -- verification is not worth
        # it for a single-user app. Recording the time is what lets Kazma warn
        # BEFORE the weekly expiry instead of discovering it mid-task.
        vault_store("email.gmail.connected_at", str(int(time.time())),
                    category="email")


def clear_gmail_oauth() -> dict[str, Any]:
    for k in (
        "EMAIL_GMAIL_ACCESS_TOKEN",
        "EMAIL_GMAIL_REFRESH_TOKEN",
        "EMAIL_GMAIL_AUTH",
        "EMAIL_GMAIL_SCOPES",
    ):
        os.environ.pop(k, None)
    try:
        from kazma_core.paths import vault_db_path
        from kazma_core.security.vault import SecretVault, get_vault

        v = get_vault() or SecretVault(db_path=vault_db_path())
        for name in (
            "email.gmail.access_token",
            "email.gmail.refresh_token",
            "email.gmail.app_password",
            "email.gmail.scopes",
            "email.gmail.drive_ok",
        ):
            try:
                v.delete(name)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("clear gmail oauth vault: %s", exc)
    os.environ.pop("EMAIL_GMAIL_APP_PASSWORD", None)
    return {"ok": True, "message": "Gmail OAuth tokens cleared."}


async def refresh_gmail_access_token(
    refresh_token: str,
    *,
    client_id: str = "",
    client_secret: str = "",
) -> tuple[str, str]:
    """Return (access_token, refresh_token)."""
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
                or "Gmail token refresh failed"
            )
        access = payload.get("access_token") or ""
        new_refresh = payload.get("refresh_token") or refresh_token
        scope_str = str(payload.get("scope") or "")
        if not access:
            raise RuntimeError("No access_token on Gmail refresh")
        persist_gmail_tokens(access, new_refresh, scopes=scope_str)
        return access, new_refresh
