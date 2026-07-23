"""Email integration API — Gmail/Microsoft OAuth + app-password + status.

Security notes (audit H4/H5/H6):
- Mutating POST endpoints are mounted on a sub-router protected by an Origin
  + custom-header check (CSRF defense; browsers won't send ``X-Requested-With``
  cross-site without a preflight, and the frontend sets it explicitly).
- Error responses are sanitized via :func:`_safe_error` — internal exception
  text is only returned when ``KAZMA_PRODUCTION`` is unset.
- ``_request_base`` delegates to ``oauth_common.public_base_url`` so the
  ``KAZMA_PUBLIC_URL`` env var is authoritative and raw ``Host`` /
  ``X-Forwarded-Host`` headers can't redirect OAuth callbacks off-site.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Open router: GET / status / OAuth callbacks (callbacks are browser-redirect
# targets from Google/Microsoft and cannot carry a custom header).
router = APIRouter(prefix="/api/email", tags=["email"])

# Protected router: every state-mutating POST. Inherits the prefix.
protected_router = APIRouter(prefix="/api/email", tags=["email"])


# ── Pydantic bodies ────────────────────────────────────────────────────


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


class ProtocolConnectBody(BaseModel):
    """IMAP or POP for gmail | microsoft | generic."""

    provider: str = Field(..., min_length=3, description="gmail | microsoft | generic")
    protocol: str = Field(..., min_length=3, description="imap | pop")
    address: str = Field(..., min_length=3)
    password: str = Field(..., min_length=4)
    imap_host: str = Field(default="")
    imap_port: int | None = Field(default=None)
    pop_host: str = Field(default="")
    pop_port: int | None = Field(default=None)
    smtp_host: str = Field(default="")
    smtp_port: int | None = Field(default=None)


class ProtocolDisconnectBody(BaseModel):
    provider: str = Field(..., min_length=3)


# ── Helpers ────────────────────────────────────────────────────────────


def _is_production() -> bool:
    return (os.environ.get("KAZMA_PRODUCTION") or "").strip().lower() in (
        "1", "true", "on", "yes",
    )


def _safe_error(exc: Exception, status: int = 500) -> JSONResponse:
    """Sanitized error response — full detail is logged server-side only.

    Internal exception text is only echoed to the client in non-production
    mode (audit H5). Mirrors the global handler's intent but uses the
    canonical ``KAZMA_PRODUCTION`` flag (the global catch-all in app.py uses
    ``KAZMA_ENV``, a narrower one-off).
    """
    logger.exception("[email_api] %s", exc)
    return JSONResponse(
        {
            "ok": False,
            "error": "internal_error",
            "detail": "" if _is_production() else str(exc)[:300],
        },
        status_code=status,
    )


def _request_base(request: Request) -> str:
    """Resolve our own public base URL for OAuth redirect URIs / post-callback
    redirects.

    Security (audit H6): the base MUST be operator-controlled, never derived
    from client-supplied ``Host`` / ``X-Forwarded-Host`` headers — otherwise an
    attacker can spoof the Host header and redirect the OAuth callback (or the
    post-callback browser 302) to an attacker-controlled host. The fallbacks
    therefore read only environment configuration:

    precedence: ``KAZMA_PUBLIC_URL`` → ``KAZMA_HOST``:``KAZMA_PORT`` →
    ``127.0.0.1:`` ``KAZMA_PORT``.

    Note: ``oauth_common.public_base_url`` is NOT used here because its
    fallback echoes ``request.base_url`` (which is built from the spoofable
    Host header), re-introducing the very vector this fix closes. The
    ``KAZMA_PUBLIC_URL``-first behavior matches that helper and the GitHub-OAuth
    / OIDC precedent, but the fallback is hard-locked to local config.
    """
    import os

    public = (os.environ.get("KAZMA_PUBLIC_URL") or "").strip().rstrip("/")
    if public:
        return public
    host = (os.environ.get("KAZMA_HOST") or "").strip() or "127.0.0.1"
    port = (os.environ.get("KAZMA_PORT") or "9090").strip()
    return f"http://{host}:{port}"


async def _verify_same_origin(request: Request) -> None:
    """CSRF guard for mutating email POST endpoints (audit H4).

    Two layers, both required:
    1. A custom ``X-Requested-With`` header that the browser cannot be
       tricked into sending cross-site without a CORS preflight (and the
       Kazma app never grants such preflight). The frontend sets this header
       explicitly via ``KazmaAPI.fetch``. This is the primary defense.
    2. When the request carries an ``Origin`` (or ``Referer``), it must target
       the same host the browser is on. This is defense-in-depth; the custom
       header already blocks cross-site forgery. We compare against the
       request's actual host (not the operator ``KAZMA_PUBLIC_URL``) so LAN /
       loopback / proxied access isn't falsely rejected.
    """
    xrw = request.headers.get("x-requested-with", "").lower()
    if xrw != "xmlhttprequest":
        raise HTTPException(status_code=403, detail="missing custom request header")
    origin = request.headers.get("origin") or request.headers.get("referer") or ""
    if origin:
        # Compare only the host:port, tolerating scheme differences (http/https
        # behind a TLS-terminating proxy). Extract the netloc from the Origin.
        own_host = request.headers.get("host") or ""
        try:
            from urllib.parse import urlparse

            origin_host = urlparse(origin).netloc
        except Exception:
            origin_host = ""
        if own_host and origin_host and origin_host != own_host:
            raise HTTPException(status_code=403, detail="cross-origin request denied")


# ── Status (open) ──────────────────────────────────────────────────────


@router.get("/status")
async def email_status() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.credentials import status_summary
        from kazma_skills.native.email_manager.router import detect_available_provider

        data = status_summary()
        data["active_provider"] = detect_available_provider()
        # Auth modes
        from kazma_skills.native.email_manager.credentials import cred

        # Prefer modes from status_summary; fill oauth client flag
        data["gmail_oauth_client_set"] = bool(
            cred("EMAIL_GMAIL_CLIENT_ID", "email.gmail.client_id")
            or cred("GOOGLE_OAUTH_CLIENT_ID", "email.gmail.client_id")
        )
        # Back-compat aliases for older Settings JS
        if "gmail_app_password" not in data:
            data["gmail_app_password"] = bool(data.get("gmail_imap") or data.get("gmail_pop"))
        return JSONResponse(data)
    except Exception as exc:
        return _safe_error(exc)


# ── Gmail app password (optional; Workspace may block) ─────────────────


@protected_router.post("/gmail/connect", dependencies=[Depends(_verify_same_origin)])
async def gmail_connect(body: GmailConnectBody) -> JSONResponse:
    address = body.address.strip()
    password = body.app_password.strip().replace(" ", "")
    if "@" not in address:
        return JSONResponse({"ok": False, "error": "Invalid email address"}, status_code=400)
    try:
        from kazma_skills.native.email_manager.credentials import vault_store

        os.environ["EMAIL_GMAIL_ADDRESS"] = address
        os.environ["EMAIL_GMAIL_APP_PASSWORD"] = password
        os.environ["EMAIL_GMAIL_AUTH"] = "imap"
        os.environ.setdefault("EMAIL_IMAP_HOST", "imap.gmail.com")
        os.environ.setdefault("EMAIL_SMTP_HOST", "smtp.gmail.com")
        vault_store("email.gmail.address", address, category="email")
        vault_store("email.gmail.app_password", password, category="email")
        vault_store("email.gmail.auth", "imap", category="email")
        return JSONResponse(
            {
                "ok": True,
                "address": address,
                "protocol": "imap",
                "message": "Gmail IMAP (app password) saved. Prefer OAuth if Workspace blocks app passwords.",
            }
        )
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/gmail/disconnect", dependencies=[Depends(_verify_same_origin)])
async def gmail_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.protocol_connect import disconnect_protocol

        return JSONResponse(disconnect_protocol("gmail"))
    except Exception as exc:
        return _safe_error(exc)


# ── Gmail OAuth (browser) ──────────────────────────────────────────────


@protected_router.post("/oauth/gmail/client", dependencies=[Depends(_verify_same_origin)])
async def gmail_set_oauth_client(body: GmailOAuthClientBody) -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.credentials import vault_store

        cid = body.client_id.strip()
        secret = body.client_secret.strip()
        if not cid or not secret:
            return JSONResponse(
                {"ok": False, "error": "client_id and client_secret are required"},
                status_code=400,
            )
        os.environ["EMAIL_GMAIL_CLIENT_ID"] = cid
        os.environ["EMAIL_GMAIL_CLIENT_SECRET"] = secret
        ok_id = vault_store("email.gmail.client_id", cid, category="email")
        ok_sec = vault_store("email.gmail.client_secret", secret, category="email")
        # Process env is enough for this run; vault needed after restart
        msg = "Google OAuth client saved. Click Connect with Google."
        if not (ok_id and ok_sec):
            msg += (
                " Warning: vault store failed — credentials live only in this "
                "process until restart. Check KAZMA_VAULT_KEY."
            )
        return JSONResponse(
            {
                "ok": True,
                "client_id_set": True,
                "vault_ok": bool(ok_id and ok_sec),
                "message": msg,
            }
        )
    except Exception as exc:
        return _safe_error(exc)


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


@protected_router.post("/oauth/microsoft/client", dependencies=[Depends(_verify_same_origin)])
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
        return _safe_error(exc)


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


@protected_router.post("/oauth/microsoft/device/start", dependencies=[Depends(_verify_same_origin)])
async def ms_device_start() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.oauth_ms import start_device_code_flow

        result = await start_device_code_flow()
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/oauth/microsoft/device/poll", dependencies=[Depends(_verify_same_origin)])
async def ms_device_poll(body: DevicePollBody) -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.oauth_ms import poll_device_code_flow

        result = await poll_device_code_flow(body.device_code)
        return JSONResponse(result)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/oauth/microsoft/disconnect", dependencies=[Depends(_verify_same_origin)])
async def ms_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.oauth_ms import clear_microsoft_tokens

        return JSONResponse(clear_microsoft_tokens())
    except Exception as exc:
        return _safe_error(exc)


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
        return _safe_error(exc)


# ── IMAP / POP protocol connect (Gmail, Microsoft, generic) ────────────


@router.get("/presets")
async def email_presets() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.presets import list_presets

        return JSONResponse({"ok": True, "presets": list_presets()})
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/protocol/connect", dependencies=[Depends(_verify_same_origin)])
async def protocol_connect(body: ProtocolConnectBody) -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.protocol_connect import connect_protocol

        result = connect_protocol(
            provider=body.provider,
            protocol=body.protocol,
            address=body.address,
            password=body.password,
            imap_host=body.imap_host,
            imap_port=body.imap_port,
            pop_host=body.pop_host,
            pop_port=body.pop_port,
            smtp_host=body.smtp_host,
            smtp_port=body.smtp_port,
        )
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/protocol/disconnect", dependencies=[Depends(_verify_same_origin)])
async def protocol_disconnect(body: ProtocolDisconnectBody) -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.protocol_connect import disconnect_protocol

        result = disconnect_protocol(body.provider)
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as exc:
        return _safe_error(exc)
