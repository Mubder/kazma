"""MCP OAuth 2.1 + Dynamic Client Registration (DCR) support.

Implements the MCP authorization spec for streamable_http servers that
reject static tokens with ``401 + WWW-Authenticate: Bearer
resource_metadata=...``:

1. **Discovery** — RFC 9728 protected-resource metadata + RFC 8414
   authorization-server metadata.
2. **DCR** — RFC 7591 dynamic client registration (no pre-created app).
3. **PKCE (S256)** — authorization-code flow with a local loopback
   callback listener.
4. **Token storage** — ConfigStore-backed; token blobs are vault-encrypted
   via the existing ``*_token`` sensitivity rule when ``KAZMA_VAULT_KEY``
   is set. Refresh tokens are used automatically before connect.

Public API:
    - :func:`discover_auth_requirements` — parse a 401 challenge.
    - :func:`start_oauth_flow` — DCR + PKCE + local listener; returns the
      URL the user's browser must open.
    - :func:`get_valid_token` — stored token, refreshing when expired.
    - :func:`clear_oauth` — revoke local state for a server.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from kazma_core.background import spawn_background

logger = logging.getLogger(__name__)

__all__ = [
    "MCPOAuthError",
    "OAuthPending",
    "clear_oauth",
    "discover_auth_requirements",
    "get_valid_token",
    "start_oauth_flow",
]

#: Loopback callback port range for the authorization-code listener.
_CALLBACK_PORT_RANGE = range(47820, 47840)
_CALLBACK_PATH = "/oauth/callback"
#: How long to wait for the user to complete browser login.
_LOGIN_TIMEOUT_S = 300.0
_HTTP_TIMEOUT_S = 20.0

#: In-flight login sessions, keyed by MCP server name.
_pending: dict[str, "OAuthPending"] = {}

_CONFIG_PREFIX = "mcp.oauth"


class MCPOAuthError(Exception):
    """Raised when an MCP OAuth step fails (discovery, DCR, token exchange)."""


@dataclass
class OAuthPending:
    """State for one in-flight browser login."""

    server_name: str
    authorization_url: str
    state: str
    code_verifier: str
    redirect_uri: str
    client_id: str
    token_endpoint: str
    scopes: str
    listener_port: int
    done: asyncio.Event = field(default_factory=asyncio.Event)
    code: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Discovery (RFC 9728 / RFC 8414)
# ---------------------------------------------------------------------------


def parse_www_authenticate(header_value: str) -> dict[str, str]:
    """Parse a ``WWW-Authenticate: Bearer ...`` challenge into params.

    Returns an empty dict for non-Bearer challenges — MCP OAuth discovery
    only applies to Bearer-protected resources.
    """
    if not header_value:
        return {}
    value = header_value.strip()
    if not value.lower().startswith("bearer"):
        return {}
    value = value[len("bearer"):].strip()
    params: dict[str, str] = {}
    for part in value.split(","):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        params[k.strip().lower()] = v.strip().strip('"')
    return params


async def discover_auth_requirements(
    resource_url: str,
    *,
    www_authenticate: str = "",
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Resolve OAuth endpoints for an MCP resource URL.

    Returns::

        {
            "resource_metadata_url": str,
            "authorization_endpoint": str,
            "token_endpoint": str,
            "registration_endpoint": str | None,
            "scopes": list[str],
        }

    Raises :class:`MCPOAuthError` when the resource does not advertise OAuth.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, follow_redirects=True)
    try:
        # 1. Protected-resource metadata (RFC 9728). The challenge tells us
        #    the exact metadata URL; fall back to the well-known path with the
        #    resource path appended (what Meta does: /.well-known/oauth-
        #    protected-resource/devtools).
        params = parse_www_authenticate(www_authenticate)
        resource_meta_url = params.get("resource_metadata", "")
        if not resource_meta_url:
            parsed = urlparse(resource_url)
            suffix = parsed.path.rstrip("/")
            resource_meta_url = urlunparse(
                (parsed.scheme, parsed.netloc,
                 f"/.well-known/oauth-protected-resource{suffix}", "", "", "")
            )

        r = await http.get(resource_meta_url)
        if r.status_code != 200:
            raise MCPOAuthError(
                f"OAuth resource metadata not found at {resource_meta_url} "
                f"(HTTP {r.status_code})"
            )
        meta = r.json()
        scopes = meta.get("scopes_supported") or []
        auth_servers = meta.get("authorization_servers") or []
        if not auth_servers:
            raise MCPOAuthError("Protected-resource metadata lists no authorization_servers")

        # 2. Authorization-server metadata (RFC 8414). Same host + resource
        #    path suffix convention as above.
        parsed = urlparse(resource_url)
        suffix = parsed.path.rstrip("/")
        as_meta_url = urlunparse(
            (parsed.scheme, parsed.netloc,
             f"/.well-known/oauth-authorization-server{suffix}", "", "", "")
        )
        r = await http.get(as_meta_url)
        if r.status_code != 200:
            raise MCPOAuthError(
                f"OAuth authorization-server metadata not found at {as_meta_url} "
                f"(HTTP {r.status_code})"
            )
        as_meta = r.json()

        challenge_scopes = (params.get("scope") or "").split()
        return {
            "resource_metadata_url": resource_meta_url,
            "authorization_endpoint": as_meta.get("authorization_endpoint", ""),
            "token_endpoint": as_meta.get("token_endpoint", ""),
            "registration_endpoint": as_meta.get("registration_endpoint"),
            "scopes": challenge_scopes or list(scopes),
        }
    except httpx.HTTPError as exc:
        raise MCPOAuthError(f"OAuth discovery failed: {exc}") from exc
    finally:
        if owns_client:
            await http.aclose()


# ---------------------------------------------------------------------------
# DCR (RFC 7591) + PKCE
# ---------------------------------------------------------------------------


def _generate_pkce() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


async def _register_client(
    http: httpx.AsyncClient,
    registration_endpoint: str,
    redirect_uri: str,
) -> str:
    """Dynamically register Kazma as a public OAuth client. Returns client_id."""
    payload = {
        "client_name": "Kazma MCP Client",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    r = await http.post(registration_endpoint, json=payload)
    if r.status_code not in (200, 201):
        logger.debug("[mcp-oauth] DCR full error body: %s", r.text)
        raise MCPOAuthError(
            f"Dynamic client registration failed (HTTP {r.status_code}): "
            f"{r.text[:300]}"
        )
    body = r.json()
    client_id = body.get("client_id")
    if not client_id:
        raise MCPOAuthError("DCR response missing client_id")
    return client_id


# ---------------------------------------------------------------------------
# Loopback callback listener
# ---------------------------------------------------------------------------


async def _run_callback_listener(pending: OAuthPending, port: int) -> None:
    """Serve one loopback HTTP request carrying the authorization code."""
    from aiohttp import web  # local import — optional dependency guard

    async def _handler(request: "web.Request") -> "web.Response":
        params = request.rel_url.query
        if params.get("state") != pending.state:
            pending.error = "OAuth state mismatch"
        elif "error" in params:
            pending.error = params.get("error_description") or params["error"]
        else:
            pending.code = params.get("code")
        pending.done.set()
        body = (
            "<html><body style='font-family:sans-serif;text-align:center;"
            "padding:4em'><h2>Login complete</h2>"
            "<p>You can close this tab and return to Kazma.</p></body></html>"
        )
        return web.Response(text=body, content_type="text/html")

    app = web.Application()
    app.router.add_get(_CALLBACK_PATH, _handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    try:
        await pending.done.wait()
    finally:
        await runner.cleanup()


async def start_oauth_flow(
    server_name: str,
    resource_url: str,
    *,
    www_authenticate: str = "",
    open_browser: bool = True,
) -> dict[str, Any]:
    """Begin an MCP OAuth login for *server_name*.

    Runs DCR + PKCE, starts a loopback callback listener, and returns the
    authorization URL to open. Completion is awaited by the caller via
    :func:`await_login_completion`.
    """
    if server_name in _pending and not _pending[server_name].done.is_set():
        p = _pending[server_name]
        return {"status": "ok", "authorization_url": p.authorization_url,
                "already_pending": True}

    try:
        from aiohttp import web  # noqa: F401
    except ImportError as exc:
        raise MCPOAuthError(
            "aiohttp is required for MCP OAuth login callbacks"
        ) from exc

    endpoints = await discover_auth_requirements(
        resource_url, www_authenticate=www_authenticate
    )
    if not endpoints.get("registration_endpoint"):
        raise MCPOAuthError(
            "Authorization server does not support dynamic client registration"
        )

    # Pick a free loopback port.
    listener_port = 0
    for port in _CALLBACK_PORT_RANGE:
        try:
            import socket

            with socket.socket() as s:
                s.bind(("127.0.0.1", port))
            listener_port = port
            break
        except OSError:
            continue
    if not listener_port:
        raise MCPOAuthError("No free loopback port for the OAuth callback listener")

    redirect_uri = f"http://127.0.0.1:{listener_port}{_CALLBACK_PATH}"

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, follow_redirects=True) as http:
        client_id = await _register_client(
            http, endpoints["registration_endpoint"], redirect_uri
        )

    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(16)
    scopes = " ".join(endpoints.get("scopes") or [])
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    authorization_url = f"{endpoints['authorization_endpoint']}?{query}"

    pending = OAuthPending(
        server_name=server_name,
        authorization_url=authorization_url,
        state=state,
        code_verifier=verifier,
        redirect_uri=redirect_uri,
        client_id=client_id,
        token_endpoint=endpoints["token_endpoint"],
        scopes=scopes,
        listener_port=listener_port,
    )
    _pending[server_name] = pending

    async def _listen() -> None:
        try:
            await _run_callback_listener(pending, listener_port)
        except Exception as exc:  # noqa: BLE001
            pending.error = f"Callback listener failed: {exc}"
            pending.done.set()

    listener_task = asyncio.create_task(_listen())

    if open_browser:
        try:
            import webbrowser

            webbrowser.open(authorization_url)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[MCP-OAuth] could not open browser: %s", exc)

    async def _await_and_exchange() -> None:
        try:
            code = await _wait_for_code(server_name)
            tokens = await _exchange_code(pending, code)
            _save_tokens(server_name, resource_url, pending.client_id, tokens)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[MCP-OAuth] login for '%s' failed: %s", server_name, exc)
        finally:
            _pending.pop(server_name, None)
            listener_task.cancel()

    spawn_background(_await_and_exchange(), name="mcp-oauth-exchange")
    return {"status": "ok", "authorization_url": authorization_url,
            "already_pending": False}


async def _wait_for_code(server_name: str) -> str:
    pending = _pending.get(server_name)
    if pending is None:
        raise MCPOAuthError("No pending OAuth login for this server")
    try:
        await asyncio.wait_for(pending.done.wait(), timeout=_LOGIN_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        raise MCPOAuthError("OAuth login timed out (5 minutes)") from exc
    if pending.error:
        raise MCPOAuthError(f"OAuth authorization failed: {pending.error}")
    if not pending.code:
        raise MCPOAuthError("OAuth callback did not carry an authorization code")
    return pending.code


async def _exchange_code(pending: OAuthPending, code: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, follow_redirects=True) as http:
        r = await http.post(
            pending.token_endpoint,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending.redirect_uri,
                "client_id": pending.client_id,
                "code_verifier": pending.code_verifier,
            },
        )
    if r.status_code != 200:
        logger.debug("[mcp-oauth] token exchange full error body: %s", r.text)
        raise MCPOAuthError(
            f"Token exchange failed (HTTP {r.status_code}): {r.text[:300]}"
        )
    body = r.json()
    if "access_token" not in body:
        raise MCPOAuthError("Token response missing access_token")
    return body


# ---------------------------------------------------------------------------
# Token persistence + refresh
# ---------------------------------------------------------------------------


def _config_key(server_name: str) -> str:
    safe = server_name.lower().replace(" ", "_")
    return f"{_CONFIG_PREFIX}.{safe}_token"


def _save_tokens(
    server_name: str,
    resource_url: str,
    client_id: str,
    tokens: dict[str, Any],
) -> None:
    try:
        from kazma_core.config_store import get_config_store
    except Exception:  # noqa: BLE001
        logger.warning("[MCP-OAuth] ConfigStore unavailable; token not persisted")
        return
    record = {
        "access_token": tokens.get("access_token", ""),
        "refresh_token": tokens.get("refresh_token", ""),
        "client_id": client_id,
        "resource_url": resource_url,
        # ``expires_in`` is seconds; keep a 60s safety margin.
        "expires_at": time.time() + float(tokens.get("expires_in", 3600)) - 60.0,
        "token_type": tokens.get("token_type", "Bearer"),
    }
    try:
        # Key ends in *_token → vault-encrypted when KAZMA_VAULT_KEY is set.
        get_config_store().set(_config_key(server_name), json.dumps(record))
        logger.info("[MCP-OAuth] stored token for '%s'", server_name)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MCP-OAuth] failed to persist token for '%s': %s",
                       server_name, exc)


def _load_tokens(server_name: str) -> dict[str, Any] | None:
    try:
        from kazma_core.config_store import get_config_store

        raw = get_config_store().get(_config_key(server_name))
        if not raw:
            return None
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


async def _refresh(pending_record: dict[str, Any]) -> dict[str, Any] | None:
    endpoints = await discover_auth_requirements(pending_record["resource_url"])
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S, follow_redirects=True) as http:
        r = await http.post(
            endpoints["token_endpoint"],
            data={
                "grant_type": "refresh_token",
                "refresh_token": pending_record["refresh_token"],
                "client_id": pending_record["client_id"],
            },
        )
    if r.status_code != 200:
        logger.warning("[MCP-OAuth] refresh failed (HTTP %s)", r.status_code)
        return None
    body = r.json()
    if "access_token" not in body:
        return None
    return body


async def get_valid_token(server_name: str) -> str | None:
    """Return a usable access token for *server_name*, refreshing if needed."""
    record = _load_tokens(server_name)
    if not record or not record.get("access_token"):
        return None
    if time.time() < float(record.get("expires_at", 0)):
        return record["access_token"]

    refresh_token = record.get("refresh_token")
    if not refresh_token:
        return None
    try:
        refreshed = await _refresh(record)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[MCP-OAuth] refresh error for '%s': %s", server_name, exc)
        return None
    if not refreshed:
        return None
    _save_tokens(
        server_name,
        record["resource_url"],
        record["client_id"],
        {**record, **refreshed},
    )
    return refreshed["access_token"]


def clear_oauth(server_name: str) -> bool:
    """Delete stored OAuth state for *server_name*."""
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().delete(_config_key(server_name))
        return True
    except Exception:  # noqa: BLE001
        return False


def oauth_status(server_name: str) -> str:
    """``"authenticated"`` | ``"pending"`` | ``"none"`` for the UI badge."""
    if server_name in _pending and not _pending[server_name].done.is_set():
        return "pending"
    record = _load_tokens(server_name)
    return "authenticated" if record and record.get("access_token") else "none"
