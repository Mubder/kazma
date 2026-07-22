"""Microsoft identity platform — device code flow for Graph mail scopes."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from kazma_skills.native.email_manager.credentials import vault_store

logger = logging.getLogger(__name__)

SCOPES = (
    "https://graph.microsoft.com/Mail.Read "
    "https://graph.microsoft.com/Mail.ReadWrite "
    "https://graph.microsoft.com/Mail.Send "
    "offline_access "
    "openid "
    "profile"
)

# In-memory device flows: device_code -> meta (expires)
_pending: dict[str, dict[str, Any]] = {}


def _client_id() -> str:
    return (os.environ.get("EMAIL_MS_CLIENT_ID") or "").strip()


def _tenant() -> str:
    return (os.environ.get("EMAIL_MS_TENANT_ID") or "common").strip() or "common"


async def start_device_code_flow() -> dict[str, Any]:
    """Start OAuth2 device code flow. Returns user_code + verification_uri."""
    client_id = _client_id()
    if not client_id:
        return {
            "ok": False,
            "error": "EMAIL_MS_CLIENT_ID is not set. Register an Azure app (public client) first.",
        }
    tenant = _tenant()
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/devicecode"
    async with httpx.AsyncClient(timeout=30.0) as client:
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
    async with httpx.AsyncClient(timeout=30.0) as client:
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

    access = payload.get("access_token") or ""
    refresh = payload.get("refresh_token") or ""
    if not access:
        return {"ok": False, "status": "failed", "error": "No access_token in token response"}

    # Persist for process + vault
    os.environ["EMAIL_MS_ACCESS_TOKEN"] = access
    if refresh:
        os.environ["EMAIL_MS_REFRESH_TOKEN"] = refresh
    vault_store("email.microsoft.access_token", access, category="email")
    if refresh:
        vault_store("email.microsoft.refresh_token", refresh, category="email")
    vault_store("email.microsoft.client_id", client_id, category="email")

    _pending.pop(device_code, None)
    logger.info("[email.oauth] Microsoft Graph tokens stored (vault + env)")
    return {
        "ok": True,
        "status": "authorized",
        "expires_in": payload.get("expires_in"),
        "scope": payload.get("scope"),
        "message": "Microsoft Graph connected. email tools will use [microsoft_graph mode].",
    }


def clear_microsoft_tokens() -> dict[str, Any]:
    """Remove Microsoft tokens from env + vault."""
    for k in ("EMAIL_MS_ACCESS_TOKEN", "EMAIL_MS_REFRESH_TOKEN"):
        os.environ.pop(k, None)
    try:
        from kazma_core.security.vault import SecretVault, get_vault
        from kazma_core.paths import vault_db_path

        v = get_vault() or SecretVault(db_path=vault_db_path())
        for name in (
            "email.microsoft.access_token",
            "email.microsoft.refresh_token",
        ):
            try:
                v.delete(name)
            except Exception:
                pass
    except Exception as exc:
        logger.debug("[email.oauth] vault clear: %s", exc)
    return {"ok": True, "message": "Microsoft Graph tokens cleared."}
