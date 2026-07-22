"""Email integration API — status + Microsoft device-code OAuth."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/email", tags=["email"])


class DevicePollBody(BaseModel):
    device_code: str = Field(..., min_length=1)


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
