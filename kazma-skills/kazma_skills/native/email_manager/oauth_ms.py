"""Microsoft identity platform — device code flow for Graph mail scopes."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from kazma_skills.native.email_manager.credentials import vault_store
from kazma_core.http_tls import shared_ssl_context

logger = logging.getLogger(__name__)

SCOPES = (
    "https://graph.microsoft.com/Mail.Read "
    "https://graph.microsoft.com/Mail.ReadWrite "
    "https://graph.microsoft.com/Mail.Send "
    "https://graph.microsoft.com/Calendars.ReadWrite "
    "https://graph.microsoft.com/Files.ReadWrite "
    "offline_access "
    "openid "
    "profile"
)

# In-memory device flows: device_code -> meta (expires)
_pending: dict[str, dict[str, Any]] = {}


def _client_id() -> str:
    from kazma_skills.native.email_manager.credentials import cred

    return cred("EMAIL_MS_CLIENT_ID", "email.microsoft.client_id")


def _tenant() -> str:
    from kazma_skills.native.email_manager.credentials import cred

    return cred("EMAIL_MS_TENANT_ID", "") or "common"


async def start_device_code_flow(*, purpose: str = "mail", account: str = "") -> dict[str, Any]:
    """Start OAuth2 device code flow. Returns user_code + verification_uri.

    *purpose* ``"calendar"`` is the calendar card's fallback for a redirect
    Microsoft refuses: the poll then keeps the tokens for Outlook Calendar
    only (``store_microsoft_calendar_tokens``), as the card's browser sign-in
    does. Without it a disconnected Outlook Calendar had no way back.
    """
    if purpose not in ("mail", "calendar"):
        raise ValueError(f"unknown Microsoft sign-in purpose: {purpose!r}")
    alias = ""
    if account:
        # An extra Microsoft account (email_manager.accounts): one grant for
        # its mail and calendar, kept under its own name.
        from kazma_skills.native.email_manager.accounts import alias_problem, normalize_alias

        alias = normalize_alias(account)
        problem = alias_problem(alias, kind="microsoft")
        if problem:
            return {"ok": False, "code": "bad_account_name", "error": problem}
    client_id = _client_id()
    if not client_id:
        return {
            "ok": False,
            "error": "EMAIL_MS_CLIENT_ID is not set. Register an Azure app (public client) first.",
        }
    tenant = _tenant()
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode"
    async with httpx.AsyncClient(timeout=30.0, verify=shared_ssl_context()) as client:
        r = await client.post(
            url,
            data={"client_id": client_id, "scope": SCOPES},
        )
        if r.status_code >= 400:
            return {"ok": False, "error": f"Device code start failed: {r.status_code} {r.text[:300]}"}
        data = r.json()
    device_code = data.get("device_code") or ""
    if not device_code:
        return {"ok": False, "error": "No device_code in response"}
    _pending[device_code] = {
        "interval": int(data.get("interval") or 5),
        "expires_at": time.time() + int(data.get("expires_in") or 900),
        "client_id": client_id,
        "tenant": tenant,
        "purpose": purpose,
        "account": alias,
    }
    return {
        "ok": True,
        "device_code": device_code,
        "user_code": data.get("user_code"),
        "verification_uri": data.get("verification_uri") or data.get("verification_uri_complete"),
        "verification_uri_complete": data.get("verification_uri_complete"),
        "expires_in": data.get("expires_in"),
        "interval": data.get("interval"),
        "message": data.get("message")
        or f"Go to {data.get('verification_uri')} and enter code {data.get('user_code')}",
    }


async def poll_device_code_flow(device_code: str) -> dict[str, Any]:
    """Poll until authorized, then store tokens in vault + env-friendly keys."""
    device_code = (device_code or "").strip()
    meta = _pending.get(device_code)
    if not meta:
        return {"ok": False, "error": "Unknown or expired device_code — start again.", "status": "expired"}
    if time.time() > meta["expires_at"]:
        _pending.pop(device_code, None)
        return {"ok": False, "error": "Device code expired — start again.", "status": "expired"}

    client_id = meta["client_id"]
    tenant = meta["tenant"]
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    async with httpx.AsyncClient(timeout=30.0, verify=shared_ssl_context()) as client:
        r = await client.post(
            token_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": device_code,
            },
        )
        payload = r.json() if r.content else {}
        if r.status_code >= 400:
            err = payload.get("error") or "authorization_pending"
            if err in ("authorization_pending", "slow_down"):
                return {
                    "ok": False,
                    "status": err,
                    "error": payload.get("error_description") or err,
                    "interval": meta["interval"],
                }
            _pending.pop(device_code, None)
            return {
                "ok": False,
                "status": "failed",
                "error": payload.get("error_description") or err,
            }

    if not (payload.get("access_token") or ""):
        return {"ok": False, "status": "failed", "error": "No access_token in token response"}

    _pending.pop(device_code, None)
    if meta.get("account"):
        try:
            row = await asyncio.to_thread(store_microsoft_account_tokens, payload, meta["account"])
        except (ValueError, RuntimeError) as exc:
            return {"ok": False, "status": "failed", "account": meta["account"], "error": str(exc)}
        return {
            "ok": True,
            "status": "authorized",
            "account": row["alias"],
            "email": row.get("address") or "",
            "message": f"Microsoft account “{row['alias']}” connected.",
        }
    if meta.get("purpose") == "calendar":
        try:
            address = store_microsoft_calendar_tokens(payload)
        except RuntimeError as exc:
            return {"ok": False, "status": "failed", "purpose": "calendar", "error": str(exc)}
        logger.info("[calendar.oauth] Outlook Calendar tokens stored (device code)")
        return {
            "ok": True,
            "status": "authorized",
            "purpose": "calendar",
            "email": address,
            "message": "Outlook Calendar connected.",
        }
    address = store_microsoft_tokens(payload, client_id)
    logger.info("[email.oauth] Microsoft Graph tokens stored (vault + env)")
    return {
        "ok": True,
        "status": "authorized",
        "purpose": "mail",
        "email": address,
        "expires_in": payload.get("expires_in"),
        "scope": payload.get("scope"),
        "message": "Microsoft Graph connected. email tools will use [microsoft_graph mode].",
    }


def store_microsoft_tokens(payload: dict[str, Any], client_id: str) -> str:
    """Keep a Microsoft token response: env + vault, the calendar's copy, and
    the account's own address. Both sign-in flows (device code, browser
    redirect) end here. Returns the address, or "" when the response named
    none.

    The address comes from the response's OpenID ``id_token`` (the sign-in
    asks for ``openid profile``) and is kept apart from an IMAP/POP address
    (``email.microsoft.oauth_address``), so a leftover protocol login never
    stands in for the OAuth account. Before 2026-09-28 it was not read at
    all: Settings showed no Microsoft address, and "email my MSN account"
    meant digging it out of Sent Items.
    """
    from kazma_skills.native.email_manager.oauth_common import address_from_id_token

    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    os.environ["EMAIL_MS_ACCESS_TOKEN"] = access
    vault_store("email.microsoft.access_token", access, category="email")
    if refresh:
        os.environ["EMAIL_MS_REFRESH_TOKEN"] = refresh
        vault_store("email.microsoft.refresh_token", refresh, category="email")
    vault_store("email.microsoft.client_id", client_id, category="email")
    os.environ["EMAIL_MS_AUTH"] = "oauth"
    vault_store("email.microsoft.auth", "oauth", category="email")
    scope_str = str(payload.get("scope") or SCOPES)
    vault_store("email.microsoft.scopes", scope_str, category="email")
    address = address_from_id_token(payload.get("id_token"))
    if address:
        vault_store("email.microsoft.oauth_address", address, category="email")
    try:
        from kazma_skills.native.calendar.credentials import persist_microsoft_tokens

        persist_microsoft_tokens(access, refresh, scope_str, address)
    except Exception:
        logger.debug("[email.oauth] calendar token copy skipped", exc_info=True)
    return address


def store_microsoft_account_tokens(payload: dict[str, Any], alias: str) -> dict[str, Any]:
    """Keep a Microsoft token response for an EXTRA account (its mail and its
    calendar share the grant): that account's own keys, never the main
    account's. Raises ValueError when the address is already connected."""
    from kazma_skills.native.email_manager.accounts import upsert_oauth_account
    from kazma_skills.native.email_manager.oauth_common import address_from_id_token

    return upsert_oauth_account(
        alias,
        "microsoft",
        address_from_id_token(payload.get("id_token")),
        str(payload.get("access_token") or ""),
        str(payload.get("refresh_token") or ""),
        str(payload.get("scope") or SCOPES),
    )


def store_microsoft_calendar_tokens(payload: dict[str, Any]) -> str:
    """Keep a Microsoft token response for Outlook Calendar ONLY: the sign-in
    started from the calendar card. Mail is left as it is -- connecting the
    calendar must not reconnect a mailbox the owner disconnected -- and an
    earlier calendar disconnect is lifted first. Returns the address, or "".
    Raises when the calendar cannot be turned back on."""
    from kazma_skills.native.calendar.credentials import (
        persist_microsoft_tokens,
        turn_calendar_on,
    )
    from kazma_skills.native.email_manager.oauth_common import address_from_id_token

    turn_calendar_on("microsoft")
    address = address_from_id_token(payload.get("id_token"))
    persist_microsoft_tokens(
        str(payload.get("access_token") or ""),
        str(payload.get("refresh_token") or ""),
        str(payload.get("scope") or SCOPES),
        address,
    )
    return address


def clear_microsoft_tokens() -> dict[str, Any]:
    """Remove Microsoft OAuth tokens (and protocol password if disconnect-all)."""
    from kazma_skills.native.email_manager.protocol_connect import disconnect_protocol

    # Full disconnect: OAuth + IMAP/POP password for Microsoft
    return disconnect_protocol("microsoft")
