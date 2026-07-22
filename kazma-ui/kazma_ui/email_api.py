"""Email integration API — Gmail/Microsoft OAuth + app-password + status."""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/email", tags=["email"])


class DevicePollBody(BaseModel):
    device_code: str = Field(..., min_length=1)


class GmailConnectBody(BaseModel):
    address: str = Field(..., min_length=3)
    app_password: str = Field(..., min_length=4)


class GmailOAuthClientBody(BaseModel):
    client_id: str = Field(..., min_length=8)
    client_secret: str = Field(..., min_length=4)


class MsClientBody(BaseModel):
    client_id: str = Field(..., min_length=8)
    client_secret: str = Field(default="")
    tenant_id: str = Field(default="common")


def _request_base(request: Request) -> str:
    # Honor reverse proxy headers when present
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        return f"{proto}://{host}".rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/status")
async def email_status() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.credentials import status_summary
        from kazma_skills.native.email_manager.router import detect_available_provider

        data = status_summary()
        data["active_provider"] = detect_available_provider()
        # Auth modes
        from kazma_skills.native.email_manager.credentials import cred

        data["gmail_oauth"] = bool(
            cred("EMAIL_GMAIL_ACCESS_TOKEN", "email.gmail.access_token")
            or cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token")
        )
        data["gmail_app_password"] = bool(
            cred("EMAIL_GMAIL_APP_PASSWORD", "email.gmail.app_password")
        )
        data["gmail_oauth_client_set"] = bool(
            cred("EMAIL_GMAIL_CLIENT_ID", "email.gmail.client_id")
            or cred("GOOGLE_OAUTH_CLIENT_ID", "email.gmail.client_id")
        )
        return JSONResponse(data)
    except Exception as exc:
        logger.exception("email status failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


# ── Gmail app password (optional; Workspace may block) ─────────────────


@router.post("/gmail/connect")
async def gmail_connect(body: GmailConnectBody) -> JSONResponse:
    address = body.address.strip()
    password = body.app_password.strip().replace(" ", "")
    if "@" not in address:
        return JSONResponse({"ok": False, "error": "Invalid email address"}, status_code=400)
    try:
        from kazma_skills.native.email_manager.credentials import vault_store

        os.environ["EMAIL_GMAIL_ADDRESS"] = address
        os.environ["EMAIL_GMAIL_APP_PASSWORD"] = password
        os.environ["EMAIL_GMAIL_AUTH"] = "app_password"
        vault_store("email.gmail.address", address, category="email")
        vault_store("email.gmail.app_password", password, category="email")
        return JSONResponse(
            {
                "ok": True,
                "address": address,
                "message": "Gmail app password saved. Prefer OAuth if Workspace blocks app passwords.",
            }
        )
    except Exception as exc:
        logger.exception("gmail connect failed")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/gmail/disconnect")
async def gmail_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.oauth_gmail import clear_gmail_oauth

        clear_gmail_oauth()
        os.environ.pop("EMAIL_GMAIL_ADDRESS", None)
        try:
            from kazma_core.security.vault import SecretVault, get_vault
            from kazma_core.paths import vault_db_path

            v = get_vault() or SecretVault(db_path=vault_db_path())
            try:
                v.delete("email.gmail.address")
            except Exception:
                pass
        except Exception:
            pass
        return JSONResponse({"ok": True, "message": "Gmail credentials cleared."})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ── Gmail OAuth (browser) ──────────────────────────────────────────────


@router.post("/oauth/gmail/client")
async def gmail_set_oauth_client(body: GmailOAuthClientBody) -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.credentials import vault_store

        os.environ["EMAIL_GMAIL_CLIENT_ID"] = body.client_id.strip()
        os.environ["EMAIL_GMAIL_CLIENT_SECRET"] = body.client_secret.strip()
        vault_store("email.gmail.client_id", body.client_id.strip(), category="email")
        vault_store("email.gmail.client_secret", body.client_secret.strip(), category="email")
        return JSONResponse(
            {
                "ok": True,
                "message": "Google OAuth client saved. Click Connect with Google.",
            }
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/oauth/gmail/start")
async def gmail_oauth_start(request: Request) -> Any:
    """Redirect browser to Google consent screen."""
    from kazma_skills.native.email_manager.oauth_gmail import start_gmail_oauth

    result = start_gmail_oauth(_request_base(request))
    if not result.get("ok"):
        # JSON for API clients; Settings uses fetch then window.location
        return JSONResponse(result, status_code=400)
    return RedirectResponse(result["authorize_url"], status_code=302)


@router.get("/oauth/gmail/start.json")
async def gmail_oauth_start_json(request: Request) -> JSONResponse:
    from kazma_skills.native.email_manager.oauth_gmail import start_gmail_oauth

    result = start_gmail_oauth(_request_base(request))
    code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=code)


@router.get("/oauth/gmail/callback")
async def gmail_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Google redirects here; store tokens and send user back to Settings."""
    base = _request_base(request)
    settings_url = f"{base}/settings?tab=email"
    if error:
        return RedirectResponse(
            f"{settings_url}&email_oauth=error&msg={quote(error)}",
            status_code=302,
        )
    if not code or not state:
        return RedirectResponse(
            f"{settings_url}&email_oauth=error&msg={quote('missing_code')}",
            status_code=302,
        )
    from kazma_skills.native.email_manager.oauth_gmail import finish_gmail_oauth

    result = await finish_gmail_oauth(code, state)
    if not result.get("ok"):
        return RedirectResponse(
            f"{settings_url}&email_oauth=error&msg={quote(str(result.get('error') or 'failed'))}",
            status_code=302,
        )
    email = quote(str(result.get("email") or ""))
    return RedirectResponse(
        f"{settings_url}&email_oauth=ok&provider=gmail&email={email}",
        status_code=302,
    )


# ── Microsoft OAuth browser + device ───────────────────────────────────


@router.post("/oauth/microsoft/client")
async def ms_set_client(body: MsClientBody) -> JSONResponse:
    cid = body.client_id.strip()
    tenant = (body.tenant_id or "common").strip() or "common"
    if not cid:
        return JSONResponse({"ok": False, "error": "client_id required"}, status_code=400)
    try:
        from kazma_skills.native.email_manager.credentials import vault_store

        os.environ["EMAIL_MS_CLIENT_ID"] = cid
        os.environ["EMAIL_MS_TENANT_ID"] = tenant
        vault_store("email.microsoft.client_id", cid, category="email")
        if body.client_secret.strip():
            os.environ["EMAIL_MS_CLIENT_SECRET"] = body.client_secret.strip()
            vault_store(
                "email.microsoft.client_secret",
                body.client_secret.strip(),
                category="email",
            )
        return JSONResponse(
            {
                "ok": True,
                "client_id_set": True,
                "tenant_id": tenant,
                "message": "Microsoft app saved. Use Connect with Microsoft (browser) or device code.",
            }
        )
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/oauth/microsoft/start")
async def ms_oauth_start(request: Request) -> Any:
    from kazma_skills.native.email_manager.oauth_ms_browser import start_ms_browser_oauth

    result = start_ms_browser_oauth(_request_base(request))
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return RedirectResponse(result["authorize_url"], status_code=302)


@router.get("/oauth/microsoft/start.json")
async def ms_oauth_start_json(request: Request) -> JSONResponse:
    from kazma_skills.native.email_manager.oauth_ms_browser import start_ms_browser_oauth

    result = start_ms_browser_oauth(_request_base(request))
    code = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=code)


@router.get("/oauth/microsoft/callback")
async def ms_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    base = _request_base(request)
    settings_url = f"{base}/settings?tab=email"
    if error:
        msg = error_description or error
        return RedirectResponse(
            f"{settings_url}&email_oauth=error&msg={quote(str(msg))}",
            status_code=302,
        )
    if not code or not state:
        return RedirectResponse(
            f"{settings_url}&email_oauth=error&msg={quote('missing_code')}",
            status_code=302,
        )
    from kazma_skills.native.email_manager.oauth_ms_browser import finish_ms_browser_oauth

    result = await finish_ms_browser_oauth(code, state)
    if not result.get("ok"):
        return RedirectResponse(
            f"{settings_url}&email_oauth=error&msg={quote(str(result.get('error') or 'failed'))}",
            status_code=302,
        )
    return RedirectResponse(
        f"{settings_url}&email_oauth=ok&provider=microsoft",
        status_code=302,
    )


@router.post("/oauth/microsoft/device/start")
async def ms_device_start() -> JSONResponse:
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
    try:
        from kazma_skills.native.email_manager.oauth_ms import poll_device_code_flow

        result = await poll_device_code_flow(body.device_code)
        return JSONResponse(result)
    except Exception as exc:
        logger.exception("ms device poll failed")
        return JSONResponse({"ok": False, "status": "failed", "error": str(exc)}, status_code=500)


@router.post("/oauth/microsoft/disconnect")
async def ms_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.oauth_ms import clear_microsoft_tokens

        return JSONResponse(clear_microsoft_tokens())
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/accounts")
async def email_accounts() -> JSONResponse:
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
