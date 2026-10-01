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

import asyncio
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


class _DeviceStartBody(BaseModel):
    # An extra account's name: the sign-in keeps its tokens under it.
    account: str = Field(default="", max_length=64)


class _AccountAddBody(BaseModel):
    """An extra account that signs in with a password (an app password for
    Gmail). Its login is tried before it is kept."""

    alias: str = Field(..., min_length=1, max_length=64)
    type: str = Field(..., description="gmail | microsoft | imap | pop")
    address: str = Field(..., min_length=3)
    password: str = Field(..., min_length=4)
    imap_host: str = Field(default="")
    imap_port: int | None = Field(default=None)
    pop_host: str = Field(default="")
    pop_port: int | None = Field(default=None)
    smtp_host: str = Field(default="")
    smtp_port: int | None = Field(default=None)


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
def email_status() -> JSONResponse:
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
def gmail_connect(body: GmailConnectBody) -> JSONResponse:
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
def gmail_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.protocol_connect import disconnect_protocol

        return JSONResponse(disconnect_protocol("gmail"))
    except Exception as exc:
        return _safe_error(exc)


# ── Gmail OAuth (browser) ──────────────────────────────────────────────

#: A Google OAuth **client ID** always ends with this.
_GOOGLE_CLIENT_ID_SUFFIX = ".apps.googleusercontent.com"

#: A Google OAuth **client secret** always starts with this.
_GOOGLE_CLIENT_SECRET_PREFIX = "GOCSPX-"


def _gmail_client_format_error(client_id: str, client_secret: str) -> str:
    """Return an operator-facing message when the two fields are swapped/wrong.

    Google's failure for a bad client ID is ``Error 401: invalid_client — The
    OAuth client was not found``, raised on its own consent page long after
    the value was saved and with no indication of which field is wrong. The
    two values are visually similar in the Cloud Console and sit next to each
    other, so pasting the secret into both is an easy slip that costs a
    debugging session.

    The formats are unambiguous, so catch it at the point of entry instead.
    """
    cid, sec = client_id.strip(), client_secret.strip()

    if cid == sec:
        return (
            "Client ID and Client Secret are identical — the same value was "
            "pasted into both fields. In Google Cloud Console → APIs & "
            "Services → Credentials, the Client ID ends with "
            f"'{_GOOGLE_CLIENT_ID_SUFFIX}' and the secret starts with "
            f"'{_GOOGLE_CLIENT_SECRET_PREFIX}'."
        )

    if cid.startswith(_GOOGLE_CLIENT_SECRET_PREFIX):
        return (
            "That looks like the Client SECRET, not the Client ID — it starts "
            f"with '{_GOOGLE_CLIENT_SECRET_PREFIX}'. The Client ID is the "
            f"longer value ending in '{_GOOGLE_CLIENT_ID_SUFFIX}'. Google "
            "would reject this with 'Error 401: invalid_client — The OAuth "
            "client was not found'."
        )

    if not cid.endswith(_GOOGLE_CLIENT_ID_SUFFIX):
        return (
            f"Client ID must end with '{_GOOGLE_CLIENT_ID_SUFFIX}'. Copy it "
            "from Google Cloud Console → APIs & Services → Credentials → your "
            "OAuth 2.0 Client ID."
        )

    if sec.endswith(_GOOGLE_CLIENT_ID_SUFFIX):
        return (
            "The Client Secret field contains a Client ID. The secret is the "
            f"shorter value starting with '{_GOOGLE_CLIENT_SECRET_PREFIX}'."
        )

    return ""


@protected_router.post("/oauth/gmail/client", dependencies=[Depends(_verify_same_origin)])
def gmail_set_oauth_client(body: GmailOAuthClientBody) -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.credentials import vault_store

        cid = body.client_id.strip()
        secret = body.client_secret.strip()
        if not cid or not secret:
            return JSONResponse(
                {"ok": False, "error": "client_id and client_secret are required"},
                status_code=400,
            )
        problem = _gmail_client_format_error(cid, secret)
        if problem:
            return JSONResponse({"ok": False, "error": problem}, status_code=400)
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
async def gmail_oauth_start(request: Request, account: str = "") -> Any:
    """Redirect browser to Google consent screen (*account*: an extra
    account's name; empty = the main Gmail account)."""
    from kazma_skills.native.email_manager.oauth_gmail import start_gmail_oauth

    result = await asyncio.to_thread(start_gmail_oauth, _request_base(request), account=account)
    if not result.get("ok"):
        # JSON for API clients; Settings uses fetch then window.location
        return JSONResponse(result, status_code=400)
    return RedirectResponse(result["authorize_url"], status_code=302)


@router.get("/oauth/gmail/start.json")
async def gmail_oauth_start_json(request: Request, account: str = "") -> JSONResponse:
    from kazma_skills.native.email_manager.oauth_gmail import start_gmail_oauth

    result = await asyncio.to_thread(start_gmail_oauth, _request_base(request), account=account)
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
    from kazma_skills.native.email_manager.oauth_common import peek_state

    peeked = peek_state(state or "")
    if peeked and peeked.get("provider") == "google_calendar":
        from kazma_skills.native.calendar.oauth_google import finish_google_calendar_oauth

        result = await finish_google_calendar_oauth(code, state)
        if not result.get("ok"):
            return RedirectResponse(
                f"{settings_url}&calendar_oauth=error&msg={quote(str(result.get('error') or 'failed'))}",
                status_code=302,
            )
        email = quote(str(result.get("email") or ""))
        return RedirectResponse(
            f"{settings_url}&calendar_oauth=ok&provider=google&email={email}",
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
    cal = "1" if result.get("calendar_ok") else "0"
    # An extra account names itself, so the page can say which one it was.
    acct = f"&account={quote(str(result['account']))}" if result.get("account") else ""
    return RedirectResponse(
        f"{settings_url}&email_oauth=ok&provider=gmail&email={email}&calendar={cal}{acct}",
        status_code=302,
    )


# ── Microsoft OAuth browser + device ───────────────────────────────────


@protected_router.post("/oauth/microsoft/client", dependencies=[Depends(_verify_same_origin)])
def ms_set_client(body: MsClientBody) -> JSONResponse:
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
async def ms_oauth_start(request: Request, account: str = "") -> Any:
    from kazma_skills.native.email_manager.oauth_ms_browser import start_ms_browser_oauth

    result = await asyncio.to_thread(start_ms_browser_oauth, _request_base(request), account=account)
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return RedirectResponse(result["authorize_url"], status_code=302)


@router.get("/oauth/microsoft/start.json")
async def ms_oauth_start_json(request: Request, account: str = "") -> JSONResponse:
    from kazma_skills.native.email_manager.oauth_ms_browser import start_ms_browser_oauth

    result = await asyncio.to_thread(start_ms_browser_oauth, _request_base(request), account=account)
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
    from kazma_skills.native.email_manager.oauth_common import peek_state
    from kazma_skills.native.email_manager.oauth_ms_browser import finish_ms_browser_oauth

    # The calendar card's sign-in comes back here too (one redirect URI in
    # Azure); its answer goes to the calendar's toast, not the mail card's.
    peeked = peek_state(state or "")
    flag = "calendar_oauth" if peeked and peeked.get("purpose") == "calendar" else "email_oauth"
    result = await finish_ms_browser_oauth(code, state)
    if not result.get("ok"):
        return RedirectResponse(
            f"{settings_url}&{flag}=error&msg={quote(str(result.get('error') or 'failed'))}",
            status_code=302,
        )
    provider = "outlook" if flag == "calendar_oauth" else "microsoft"
    email = quote(str(result.get("email") or ""))
    acct = f"&account={quote(str(result['account']))}" if result.get("account") else ""
    return RedirectResponse(
        f"{settings_url}&{flag}=ok&provider={provider}&email={email}{acct}",
        status_code=302,
    )


@protected_router.post("/oauth/microsoft/device/start", dependencies=[Depends(_verify_same_origin)])
async def ms_device_start(body: _DeviceStartBody | None = None) -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.oauth_ms import start_device_code_flow

        result = await start_device_code_flow(account=(body.account if body else ""))
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
def ms_disconnect() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.oauth_ms import clear_microsoft_tokens

        return JSONResponse(clear_microsoft_tokens())
    except Exception as exc:
        return _safe_error(exc)


@router.get("/accounts")
async def email_accounts() -> JSONResponse:
    """Every mail account: the main ones (``source: main``), those added in
    Settings (``settings``) and those in .env (``env``). Nothing secret."""
    try:
        from kazma_skills.native.email_manager.accounts import accounts_overview

        rows = await asyncio.to_thread(accounts_overview)
        for row in rows:  # the fields the page read before 2026-09-29
            row["has_password"] = row["auth"] == "password"
            row["has_token"] = row["auth"] == "oauth"
        return JSONResponse({"accounts": rows, "count": len(rows)})
    except Exception as exc:
        return _safe_error(exc)


#: What a refused login says, whichever protocol said it (IMAP, POP, SMTP).
_AUTH_REFUSALS = (
    "authenticationfailed", "invalid credentials", "login failed",
    "authentication failed", "not accepted",
)


def _login_refusal(exc: BaseException, kind: str, cfg: dict[str, str]) -> str:
    """A failed login, said so the person adding the account can act on it.
    A mistyped server name used to come back as the socket's own words,
    "[Errno 11001] getaddrinfo failed" (2026-09-29, live)."""
    import smtplib
    import socket
    import ssl

    from kazma_core.errors import validation_error

    host = (
        cfg.get("imap_host")
        or cfg.get("pop_host")
        or {"gmail": "imap.gmail.com", "microsoft": "outlook.office365.com"}.get(kind)
        or "The mail server"
    )
    if isinstance(exc, socket.gaierror):
        return f"The mail server {host} could not be found. Check its name."
    if isinstance(exc, ConnectionRefusedError):
        return f"{host} refused the connection. Check the server name and port."
    said = validation_error(exc)
    if isinstance(exc, ssl.SSLError):
        return (
            f"The secure connection to {host} failed ({said}). Check the port: "
            "IMAP is usually 993 and POP 995."
        )
    if isinstance(exc, smtplib.SMTPAuthenticationError) or any(
        word in said.lower() for word in _AUTH_REFUSALS
    ):
        hint = (
            " Gmail and Outlook take an app password here: the account's own "
            "password is refused when two-step sign-in is on."
            if kind in ("gmail", "microsoft")
            else ""
        )
        return f"{host} refused the address and password ({said}).{hint}"
    return f"The login did not work: {said}"


@protected_router.post("/accounts", dependencies=[Depends(_verify_same_origin)])
async def email_account_add(body: _AccountAddBody) -> JSONResponse:
    """Add an extra account that signs in with a password. Its login is
    tried first -- with the same code chat will use -- and a login that does
    not work is refused with the server's answer, never kept."""
    from kazma_skills.native.email_manager.accounts import (
        add_password_account,
        alias_problem,
        normalize_alias,
    )
    from kazma_skills.native.email_manager.models import ListQuery
    from kazma_skills.native.email_manager.router import (
        EmailNotConnectedError,
        backend_for_account,
    )
    from kazma_core.errors import validation_error

    alias = normalize_alias(body.alias)
    kind = body.type.strip().lower()
    problem = await asyncio.to_thread(alias_problem, alias, kind=kind)
    if problem:
        return JSONResponse({"ok": False, "error": problem}, status_code=400)
    hosts = {
        field: value
        for field in ("imap_host", "imap_port", "pop_host", "pop_port", "smtp_host", "smtp_port")
        if (value := getattr(body, field)) not in (None, "")
    }
    password = body.password.strip().replace(" ", "")
    cfg = {"alias": alias, "type": kind, "address": body.address.strip(), "password": password}
    cfg.update({k: str(v) for k, v in hosts.items()})
    try:
        backend = backend_for_account(alias, cfg)
        await asyncio.wait_for(backend.list_messages(ListQuery(limit=1)), timeout=45)
    except EmailNotConnectedError as exc:
        return JSONResponse({"ok": False, "error": exc.hint}, status_code=400)
    except TimeoutError:
        return JSONResponse(
            {"ok": False, "error": "The mail server did not answer in 45 seconds; check the host."},
            status_code=400,
        )
    except Exception as exc:  # noqa: BLE001 -- the login's own answer is the reply
        return JSONResponse({"ok": False, "error": _login_refusal(exc, kind, cfg)}, status_code=400)
    try:
        row = await asyncio.to_thread(
            add_password_account, alias, kind, cfg["address"], password, **hosts
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": validation_error(exc)}, status_code=400)
    except RuntimeError as exc:  # the vault did not keep the password
        return _safe_error(exc)
    return JSONResponse({"ok": True, "account": row, "message": f"Account “{alias}” added."})


@protected_router.post("/accounts/{alias}/remove", dependencies=[Depends(_verify_same_origin)])
async def email_account_remove(alias: str) -> JSONResponse:
    """Forget an extra account added in Settings (its tokens and password)."""
    try:
        from kazma_skills.native.email_manager.accounts import remove_account

        result = await asyncio.to_thread(remove_account, alias)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)
    except Exception as exc:
        return _safe_error(exc)


# ── IMAP / POP protocol connect (Gmail, Microsoft, generic) ────────────


@router.get("/presets")
def email_presets() -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.presets import list_presets

        return JSONResponse({"ok": True, "presets": list_presets()})
    except Exception as exc:
        return _safe_error(exc)


@protected_router.post("/protocol/connect", dependencies=[Depends(_verify_same_origin)])
def protocol_connect(body: ProtocolConnectBody) -> JSONResponse:
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
def protocol_disconnect(body: ProtocolDisconnectBody) -> JSONResponse:
    try:
        from kazma_skills.native.email_manager.protocol_connect import disconnect_protocol

        result = disconnect_protocol(body.provider)
        code = 200 if result.get("ok") else 400
        return JSONResponse(result, status_code=code)
    except Exception as exc:
        return _safe_error(exc)
