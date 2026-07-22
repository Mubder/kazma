"""Email integration API — status, Gmail vault save, Microsoft device-code OAuth."""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/email", tags=["email"])


class DevicePollBody(BaseModel):
    device_code: str = Field(..., min_length=1)


class GmailConnectBody(BaseModel):
    address: str = Field(..., min_length=3)
    app_password: str = Field(..., min_length=4)


class MsClientBody(BaseModel):
    """Optional client id / tenant for device flow (stored in env for process)."""

    client_id: str = Field(..., min_length=8)
    tenant_id: str = Field(default="common")


@router.get("/status")
async def email_status() -> JSONResponse:
    """Non-secret email provider configuration status."""
    try:
        from kazma_skills.native.email_manager.credentials import status_summary
        from kazma_skills.native.email_manager.router import detect_available_provider

        data = status_summary()
        data["active_provider"] = detect_available_provider()
        return JSONResponse(data)
    except Exception as exc:
        logger.exception("email status failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/oauth/microsoft/device/start")
async def ms_device_start() -> JSONResponse:
    """Start Microsoft device-code flow for Graph mail scopes."""
    try:
        from kazma_skills.native.email_manager.oauth_ms import start_device_code_flow

        result = await start_device_code_flow()
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as exc:
        logger.exception("ms device start failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/oauth/microsoft/device/poll")
async def ms_device_poll(body: DevicePollBody) -> JSONResponse:
    """Poll device-code until authorized; stores tokens in vault + env."""
    try:
        from kazma_skills.native.email_manager.oauth_ms import poll_device_code_flow

        result = await poll_device_code_flow(body.device_code)
        # 200 even when pending so clients can poll easily
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("ms device poll failed")
        return JSONResponse({"ok": False, "status": "failed", "error": str(exc)}, status_code=500)


@router.post("/oauth/microsoft/disconnect")
async def ms_disconnect() -> JSONResponse:
    """Clear Microsoft Graph tokens from env + vault."""
    try:
        from kazma_skills.native.email_manager.oauth_ms import clear_microsoft_tokens

        return JSONResponse(clear_microsoft_tokens())
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/accounts")
async def email_accounts() -> JSONResponse:
    """List multi-account aliases (names only)."""
    try:
        from kazma_skills.native.email_manager.credentials import (
            account_config,
            list_account_aliases,
        )

        aliases = list_account_aliases()
        rows = []
        for a in aliases:
            cfg = account_config(a)
            rows.append(
                {
                    "alias": a,
                    "type": cfg.get("type") or "unknown",
                    "address": cfg.get("address") or "",
                    "has_password": bool(cfg.get("password")),
                    "has_token": bool(cfg.get("access_token") or cfg.get("refresh_token")),
                }
            )
        return JSONResponse({"accounts": rows, "count": len(rows)})
    except Exception as exc:
        return JSONResponse({"accounts": [], "error": str(exc)}, status_code=500)


@router.post("/gmail/connect")
async def gmail_connect(body: GmailConnectBody) -> JSONResponse:
    """Save Gmail address + app password to env (process) and vault."""
    address = body.address.strip()
    password = body.app_password.strip().replace(" ", "")
    if "@" not in address:
        return JSONResponse({"ok": False, "error": "Invalid email address"}, status_code=400)
    try:
        from kazma_skills.native.email_manager.credentials import vault_store

        os.environ["EMAIL_GMAIL_ADDRESS"] = address
        os.environ["EMAIL_GMAIL_APP_PASSWORD"] = password
        vault_ok = vault_store("email.gmail.address", address, category="email")
        vault_ok = vault_store("email.gmail.app_password", password, category="email") and vault_ok
        return JSONResponse(
            {
                "ok": True,
                "address": address,
                "vault": vault_ok,
                "message": "Gmail credentials saved. Agent will use [gmail mode] when provider=auto.",
            }
        )
    except Exception as exc:
        logger.exception("gmail connect failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/gmail/disconnect")
async def gmail_disconnect() -> JSONResponse:
    """Clear Gmail credentials from env + vault."""
    try:
        os.environ.pop("EMAIL_GMAIL_ADDRESS", None)
        os.environ.pop("EMAIL_GMAIL_APP_PASSWORD", None)
        try:
            from kazma_core.security.vault import SecretVault, get_vault
            from kazma_core.paths import vault_db_path

            v = get_vault() or SecretVault(db_path=vault_db_path())
            for name in ("email.gmail.address", "email.gmail.app_password"):
                try:
                    v.delete(name)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("gmail vault clear: %s", exc)
        return JSONResponse({"ok": True, "message": "Gmail credentials cleared."})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/oauth/microsoft/client")
async def ms_set_client(body: MsClientBody) -> JSONResponse:
    """Set Azure app client id / tenant for device-code flow (process env + vault)."""
    cid = body.client_id.strip()
    tenant = (body.tenant_id or "common").strip() or "common"
    if not cid:
        return JSONResponse({"ok": False, "error": "client_id required"}, status_code=400)
    try:
        from kazma_skills.native.email_manager.credentials import vault_store

        os.environ["EMAIL_MS_CLIENT_ID"] = cid
        os.environ["EMAIL_MS_TENANT_ID"] = tenant
        vault_store("email.microsoft.client_id", cid, category="email")
        return JSONResponse(
            {
                "ok": True,
                "client_id_set": True,
                "tenant_id": tenant,
                "message": "Microsoft app registered for this process. Click Connect Microsoft next.",
            }
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
