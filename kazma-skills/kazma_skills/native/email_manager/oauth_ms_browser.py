"""Microsoft OAuth 2.0 authorization-code (browser redirect) for Graph."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlencode

import httpx

from kazma_skills.native.email_manager.credentials import vault_store
from kazma_skills.native.email_manager.oauth_common import (
    authorize_redirect,
    new_state,
    pop_state,
    public_base_url,
)

logger = logging.getLogger(__name__)

SCOPES = (
    "https://graph.microsoft.com/Mail.Read "
    "https://graph.microsoft.com/Mail.ReadWrite "
    "https://graph.microsoft.com/Mail.Send "
    "offline_access openid profile"
)


def _client_id() -> str:
    from kazma_skills.native.email_manager.credentials import cred

    return cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id")


def _client_secret() -> str:
    from kazma_skills.native.email_manager.credentials import cred

    return cred("EMAIL_MS_CLIENT_SECRET", "email.microsoft.client_secret")


def _tenant() -> str:
    return (os.environ.get("EMAIL_MS_TENANT_ID") or "common").strip() or "common"


def ms_redirect_uri(request_base: str | None = None) -> str:
    custom = (os.environ.get("EMAIL_MS_REDIRECT_URI") or "").strip()
    if custom:
        return custom
    return f"{public_base_url(request_base)}/api/email/oauth/microsoft/callback"


def start_ms_browser_oauth(request_base: str | None = None) -> dict[str, Any]:
    cid = _client_id()
    if not cid:
        return {
            "ok": False,
            "error": "EMAIL_MS_CLIENT_ID is not set. Save Azure Application (client) ID first.",
        }
    tenant = _tenant()
    redirect = ms_redirect_uri(request_base)
    state = new_state("microsoft", redirect_uri=redirect, tenant=tenant)
    # confidential clients need secret; public clients can omit
    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": redirect,
        "response_mode": "query",
        "scope": SCOPES,
        "state": state,
    }
    url = authorize_redirect(
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        params,
    )
    return {
        "ok": True,
        "authorize_url": url,
        "redirect_uri": redirect,
        "message": "Redirect user to authorize_url",
    }


async def finish_ms_browser_oauth(code: str, state: str) -> dict[str, Any]:
    meta = pop_state(state)
    if not meta or meta.get("provider") != "microsoft":
        return {"ok": False, "error": "Invalid or expired OAuth state. Try Connect Microsoft again."}
    cid = _client_id()
    secret = _client_secret()
    tenant = meta.get("tenant") or _tenant()
    redirect = meta.get("redirect_uri") or ms_redirect_uri()
    if not cid:
        return {"ok": False, "error": "Microsoft client_id not configured"}
    data = {
        "client_id": cid,
        "code": code,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
        "scope": SCOPES,
    }
    if secret:
        data["client_secret"] = secret
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(token_url, data=data)
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
        return {"ok": False, "error": "No access_token from Microsoft"}
    os.environ["EMAIL_MS_ACCESS_TOKEN"] = access
    vault_store("email.microsoft.access_token", access, category="email")
    if refresh:
        os.environ["EMAIL_MS_REFRESH_TOKEN"] = refresh
        vault_store("email.microsoft.refresh_token", refresh, category="email")
    vault_store("email.microsoft.client_id", cid, category="email")
    os.environ["EMAIL_MS_AUTH"] = "oauth"
    vault_store("email.microsoft.auth", "oauth", category="email")
    logger.info("[email.oauth] Microsoft Graph browser OAuth tokens stored")
    return {
        "ok": True,
        "message": "Microsoft Graph connected via OAuth. Email tools will use [microsoft_graph mode].",
    }
