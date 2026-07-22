"""Google OAuth 2.0 (authorization code) for Gmail API — Workspace-friendly."""

from __future__ import annotations

import base64
import logging
import os
from email.message import EmailMessage as StdEmailMessage
from typing import Any
from urllib.parse import quote

import httpx

from kazma_skills.native.email_manager.credentials import vault_store
from kazma_skills.native.email_manager.oauth_common import (
    authorize_redirect,
    new_state,
    pop_state,
    public_base_url,
)

logger = logging.getLogger(__name__)

# Full mail access for Gmail API (read/send/modify/trash/labels)
GMAIL_SCOPES = " ".join(
    [
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]
)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
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


def start_gmail_oauth(request_base: str | None = None) -> dict[str, Any]:
    """Return Google authorize URL for browser redirect."""
    cid = _client_id()
    if not cid:
        return {
            "ok": False,
            "error": (
                "EMAIL_GMAIL_CLIENT_ID is not set. Create a Google Cloud OAuth "
                "Web client and set EMAIL_GMAIL_CLIENT_ID + EMAIL_GMAIL_CLIENT_SECRET."
            ),
        }
    secret = _client_secret()
    if not secret:
        return {
            "ok": False,
            "error": "EMAIL_GMAIL_CLIENT_SECRET is not set (required for web OAuth).",
        }
    redirect = gmail_redirect_uri(request_base)
    state = new_state("gmail", redirect_uri=redirect)
    # Persist client for callback process
    os.environ["EMAIL_GMAIL_CLIENT_ID"] = cid
    os.environ["EMAIL_GMAIL_CLIENT_SECRET"] = secret
    url = authorize_redirect(
        AUTH_URL,
        {
            "client_id": cid,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": GMAIL_SCOPES,
            "access_type": "offline",
            "prompt": "consent",  # ensure refresh_token on re-connect
            "include_granted_scopes": "true",
            "state": state,
        },
    )
    return {
        "ok": True,
        "authorize_url": url,
        "redirect_uri": redirect,
        "message": "Redirect user to authorize_url",
    }


async def finish_gmail_oauth(code: str, state: str) -> dict[str, Any]:
    """Exchange code for tokens; store in vault + env."""
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
        if not access:
            return {"ok": False, "error": "No access_token from Google"}
        # Profile email
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

    persist_gmail_tokens(access, refresh, email_addr)
    return {
        "ok": True,
        "email": email_addr,
        "message": f"Gmail connected via OAuth{f' as {email_addr}' if email_addr else ''}.",
    }


def persist_gmail_tokens(access: str, refresh: str = "", email: str = "") -> None:
    if access:
        os.environ["EMAIL_GMAIL_ACCESS_TOKEN"] = access
        vault_store("email.gmail.access_token", access, category="email")
    if refresh:
        os.environ["EMAIL_GMAIL_REFRESH_TOKEN"] = refresh
        vault_store("email.gmail.refresh_token", refresh, category="email")
    if email:
        os.environ["EMAIL_GMAIL_ADDRESS"] = email
        vault_store("email.gmail.address", email, category="email")
    # Mark auth mode
    os.environ["EMAIL_GMAIL_AUTH"] = "oauth"


def clear_gmail_oauth() -> dict[str, Any]:
    for k in (
        "EMAIL_GMAIL_ACCESS_TOKEN",
        "EMAIL_GMAIL_REFRESH_TOKEN",
        "EMAIL_GMAIL_AUTH",
    ):
        os.environ.pop(k, None)
    # Keep address optional; clear oauth secrets
    try:
        from kazma_core.security.vault import SecretVault, get_vault
        from kazma_core.paths import vault_db_path

        v = get_vault() or SecretVault(db_path=vault_db_path())
        for name in (
            "email.gmail.access_token",
            "email.gmail.refresh_token",
            "email.gmail.app_password",  # clear app password too when disconnecting
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
                payload.get("error_description") or payload.get("error") or "Gmail token refresh failed"
            )
        access = payload.get("access_token") or ""
        new_refresh = payload.get("refresh_token") or refresh_token
        if not access:
            raise RuntimeError("No access_token on Gmail refresh")
        persist_gmail_tokens(access, new_refresh)
        return access, new_refresh
