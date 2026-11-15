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
from kazma_core.http_tls import shared_ssl_context

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
    #: The MCP server this login is for (where the token request may go).
    place: "_ServerPlace | None" = None


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


# ---------------------------------------------------------------------------
# Where an OAuth request may go (security audit 2026-11-15)
# ---------------------------------------------------------------------------
#
# Every address below but the MCP server's own is chosen by that server: the
# metadata URL in its 401 challenge, the endpoints in its metadata. Kazma
# fetched them with redirects followed and no address check, so a server
# could make Kazma GET or POST any internal address, and a refresh posted the
# refresh token to whatever ``token_endpoint`` said. Now:
#
# * no request follows a redirect, and none uses an ambient proxy;
# * an address on the MCP server's own origin, when the owner configured a
#   server on this machine or their own network, is asked directly;
# * every other address must be ``https`` and public, and is asked through
#   the pinned path (``security.pinned_request``);
# * the token and registration endpoints must be on the origin of the issuer
#   the authorization-server metadata names;
# * an answer's body never reaches an error message.

#: The largest metadata or token answer read.
_MAX_ANSWER_BYTES = 256 * 1024


@dataclass(frozen=True)
class _ServerPlace:
    """The MCP server the owner configured, and whether it is on their own
    network (then its own origin may be asked directly)."""

    url: str
    own_network: bool


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """``(scheme, host, port)`` with the scheme's default port filled in."""
    try:
        parts = urlparse(url)
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return None
    if port is None:
        port = 443 if scheme == "https" else 80
    return scheme, host, port


def _same_origin(url: str, other: str) -> bool:
    mine = _origin(url)
    return mine is not None and mine == _origin(other)


async def _server_place(resource_url: str) -> _ServerPlace:
    from kazma_core.security.ssrf import resolves_to_own_network

    if _origin(resource_url) is None:
        raise MCPOAuthError("The MCP server's address is not an http(s) URL")
    # A DNS lookup: off the loop.
    own = await asyncio.to_thread(resolves_to_own_network, resource_url)
    return _ServerPlace(url=resource_url, own_network=own)


def _check_address(url: str, place: _ServerPlace, what: str) -> None:
    """Refuse, before anything is sent, an address this flow may not use."""
    if _origin(url) is None:
        raise MCPOAuthError(f"The {what} is not an http(s) address")
    if place.own_network and _same_origin(url, place.url):
        return
    if (urlparse(url).scheme or "").lower() != "https":
        raise MCPOAuthError(
            f"The {what} must be an https address (only the MCP server's own "
            "address on your network may use http)"
        )


async def _oauth_request(
    method: str,
    url: str,
    place: _ServerPlace,
    what: str,
    *,
    data: dict[str, Any] | None = None,
    json_body: Any = None,
) -> httpx.Response:
    """One OAuth request, with no redirect followed (see the rules above)."""
    from kazma_core.security.pinned_request import AnswerTooLarge, pinned_request
    from kazma_core.security.ssrf import SSRFError
    from kazma_core.web_acquire.http import read_response_capped
    from kazma_core.web_acquire.result import AcquisitionError

    _check_address(url, place, what)
    headers = {"Accept": "application/json"}
    try:
        if place.own_network and _same_origin(url, place.url):
            # The owner's own server, on their own machine or network: its
            # private address is the point. Its origin only, never a redirect.
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_S, follow_redirects=False, trust_env=False,
                verify=shared_ssl_context(),
            ) as http:
                async with http.stream(method, url, headers=headers, data=data, json=json_body) as raw:
                    answer, _text, truncated = await read_response_capped(raw, _MAX_ANSWER_BYTES)
            if truncated:
                raise MCPOAuthError(f"The {what} answered with more than {_MAX_ANSWER_BYTES} bytes")
            return answer
        return await pinned_request(
            method, url, headers=headers, data=data, json_body=json_body,
            timeout=_HTTP_TIMEOUT_S, max_bytes=_MAX_ANSWER_BYTES,
        )
    except MCPOAuthError:
        raise
    except AnswerTooLarge as exc:
        raise MCPOAuthError(f"The {what} answered with more than {_MAX_ANSWER_BYTES} bytes") from exc
    except (SSRFError, AcquisitionError) as exc:
        raise MCPOAuthError(
            f"The {what} is not a public address Kazma may ask ({type(exc).__name__})"
        ) from exc
    except (httpx.HTTPError, ValueError, TimeoutError) as exc:
        raise MCPOAuthError(f"Could not reach the {what} ({type(exc).__name__})") from exc


def _json_answer(response: httpx.Response, what: str) -> dict[str, Any]:
    if response.status_code in (301, 302, 303, 307, 308):
        raise MCPOAuthError(f"The {what} answered with a redirect, which Kazma does not follow")
    try:
        body = response.json()
    except ValueError as exc:
        raise MCPOAuthError(f"The {what} did not answer with JSON") from exc
    if not isinstance(body, dict):
        raise MCPOAuthError(f"The {what} did not answer with a JSON object")
    return body


def _oauth_error_code(response: httpx.Response) -> str:
    """The OAuth ``error`` code of a refusal (RFC 6749 §5.2), never its text."""
    try:
        body = response.json()
    except ValueError:
        return ""
    code = body.get("error") if isinstance(body, dict) else None
    if isinstance(code, str) and 0 < len(code) <= 64 and code.replace("_", "").isalnum() and code.isascii():
        return code
    return ""


def _refused(what: str, response: httpx.Response) -> MCPOAuthError:
    code = _oauth_error_code(response)
    return MCPOAuthError(
        f"{what} failed (HTTP {response.status_code}{', ' + code if code else ''})"
    )


def _same_resource(named: Any, resource_url: str) -> bool:
    """Whether the ``resource`` a metadata document names is *resource_url*
    (its origin and path; a trailing slash aside)."""
    if not isinstance(named, str):
        return False
    try:
        a, b = urlparse(named), urlparse(resource_url)
    except ValueError:
        return False
    return _same_origin(named, resource_url) and a.path.rstrip("/") == b.path.rstrip("/")


def _chosen_issuer(servers: Any, place: _ServerPlace) -> str:
    """The first authorization server the resource lists that this flow may use."""
    if not isinstance(servers, list) or not servers:
        raise MCPOAuthError("Protected-resource metadata lists no authorization_servers")
    for server in servers:
        if not isinstance(server, str) or not server:
            continue
        parts = urlparse(server)
        if parts.query or parts.fragment:
            continue  # an issuer identifier has neither (RFC 8414 §2)
        try:
            _check_address(server, place, "OAuth issuer")
        except MCPOAuthError:
            continue
        return server
    raise MCPOAuthError("No authorization server the MCP server lists is an address Kazma may use")


def _metadata_addresses(issuer: str) -> list[str]:
    """Where an issuer's metadata is, in the order the MCP specification
    asks: RFC 8414's (``/.well-known/...`` before the issuer's path), then
    OpenID Connect's two forms. All on the issuer's own origin."""
    parts = urlparse(issuer)
    path = parts.path.rstrip("/")
    origin = f"{parts.scheme}://{parts.netloc}"
    if not path:
        return [f"{origin}/.well-known/oauth-authorization-server",
                f"{origin}/.well-known/openid-configuration"]
    return [f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration"]


async def _authorization_server_metadata(issuer: str, place: _ServerPlace) -> dict[str, Any]:
    """The issuer's metadata: the first of :func:`_metadata_addresses` that answers."""
    statuses: list[str] = []
    for address in _metadata_addresses(issuer):
        r = await _oauth_request("GET", address, place, "OAuth authorization-server metadata address")
        if r.status_code == 200:
            return _json_answer(r, "OAuth authorization-server metadata address")
        if r.status_code in (301, 302, 303, 307, 308):
            raise MCPOAuthError(
                "The OAuth authorization-server metadata address answered with a "
                "redirect, which Kazma does not follow"
            )
        statuses.append(str(r.status_code))
    raise MCPOAuthError(
        f"OAuth authorization-server metadata not found for {issuer} (HTTP {', '.join(statuses)})"
    )


async def discover_auth_requirements(
    resource_url: str,
    *,
    www_authenticate: str = "",
) -> dict[str, Any]:
    """Resolve OAuth endpoints for an MCP resource URL.

    Returns::

        {
            "resource_metadata_url": str,
            "issuer": str,
            "authorization_endpoint": str,
            "token_endpoint": str,
            "registration_endpoint": str | None,
            "scopes": list[str],
        }

    Raises :class:`MCPOAuthError` when the resource does not advertise OAuth,
    or names an address this flow may not use (see the rules above).
    """
    place = await _server_place(resource_url)
    # 1. Protected-resource metadata (RFC 9728). The challenge tells us the
    #    exact metadata URL; fall back to the well-known path with the
    #    resource path appended (what Meta does: /.well-known/oauth-
    #    protected-resource/devtools).
    params = parse_www_authenticate(www_authenticate)
    resource_meta_url = params.get("resource_metadata", "")
    parsed = urlparse(resource_url)
    suffix = parsed.path.rstrip("/")
    if not resource_meta_url:
        resource_meta_url = urlunparse(
            (parsed.scheme, parsed.netloc,
             f"/.well-known/oauth-protected-resource{suffix}", "", "", "")
        )

    r = await _oauth_request("GET", resource_meta_url, place, "OAuth resource metadata address")
    if r.status_code != 200:
        raise MCPOAuthError(
            f"OAuth resource metadata not found at {resource_meta_url} "
            f"(HTTP {r.status_code})"
        )
    meta = _json_answer(r, "OAuth resource metadata address")
    scopes = meta.get("scopes_supported") or []
    # The metadata must be about this server (RFC 9728 §3.3): metadata of
    # another resource is no answer about this one.
    named_resource = meta.get("resource")
    if named_resource is not None and not _same_resource(named_resource, resource_url):
        raise MCPOAuthError("The OAuth resource metadata is about another resource")
    issuer = _chosen_issuer(meta.get("authorization_servers"), place)

    # 2. Authorization-server metadata (RFC 8414), read from the
    #    authorization server the resource names -- never from the resource's
    #    own host -- and holding that very issuer (RFC 8414 §3.3). Until
    #    2026-11-16 it was read from the MCP server's host and its issuer was
    #    taken as written, so a server could name any issuer and Kazma sent
    #    the sign-in there.
    as_meta = await _authorization_server_metadata(issuer, place)
    named_issuer = as_meta.get("issuer")
    if not isinstance(named_issuer, str) or not named_issuer:
        raise MCPOAuthError("Authorization-server metadata names no issuer")
    if named_issuer.rstrip("/") != issuer.rstrip("/"):
        raise MCPOAuthError(
            "The authorization server's metadata names another issuer than the "
            "one the MCP server lists"
        )

    # 3. The endpoints Kazma posts to belong to the issuer.
    endpoints: dict[str, str | None] = {}
    for key, required in (
        ("authorization_endpoint", True),
        ("token_endpoint", True),
        ("registration_endpoint", False),
    ):
        value = as_meta.get(key)
        if not value:
            if required:
                raise MCPOAuthError(f"Authorization-server metadata names no {key}")
            endpoints[key] = None
            continue
        if not isinstance(value, str):
            raise MCPOAuthError(f"The {key} is not an address")
        _check_address(value, place, key.replace("_", " "))
        if key != "authorization_endpoint" and not _same_origin(value, issuer):
            raise MCPOAuthError(
                f"The {key.replace('_', ' ')} is not on the issuer's own address"
            )
        endpoints[key] = value

    challenge_scopes = (params.get("scope") or "").split()
    return {
        "resource_metadata_url": resource_meta_url,
        "issuer": issuer,
        "authorization_endpoint": endpoints["authorization_endpoint"] or "",
        "token_endpoint": endpoints["token_endpoint"] or "",
        "registration_endpoint": endpoints["registration_endpoint"],
        "scopes": challenge_scopes or list(scopes),
    }


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
    place: _ServerPlace,
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
    r = await _oauth_request(
        "POST", registration_endpoint, place, "registration endpoint", json_body=payload,
    )
    if r.status_code not in (200, 201):
        # The server's text is never repeated: the OAuth error code at most.
        raise _refused("Dynamic client registration", r)
    body = _json_answer(r, "registration endpoint")
    client_id = body.get("client_id")
    if not client_id or not isinstance(client_id, str):
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

    place = await _server_place(resource_url)
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

    client_id = await _register_client(
        place, endpoints["registration_endpoint"], redirect_uri
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
        place=place,
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
            # The token is a settings write, vault-encrypted (audit 2026-10-26 AUD-111).
            await asyncio.to_thread(_save_tokens, server_name, resource_url, pending.client_id, tokens)
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
    if pending.place is None:
        raise MCPOAuthError("No MCP server recorded for this login")
    r = await _oauth_request(
        "POST", pending.token_endpoint, pending.place, "token endpoint",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": pending.client_id,
            "code_verifier": pending.code_verifier,
        },
    )
    if r.status_code != 200:
        # The server's text is never repeated: the OAuth error code at most.
        raise _refused("Token exchange", r)
    body = _json_answer(r, "token endpoint")
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
    resource_url = pending_record["resource_url"]
    endpoints = await discover_auth_requirements(resource_url)
    # The refresh token goes only where discovery allows a token request:
    # an https public address on the issuer's origin (or the owner's own
    # server), pinned, never a redirect.
    r = await _oauth_request(
        "POST", endpoints["token_endpoint"], await _server_place(resource_url), "token endpoint",
        data={
            "grant_type": "refresh_token",
            "refresh_token": pending_record["refresh_token"],
            "client_id": pending_record["client_id"],
        },
    )
    if r.status_code != 200:
        logger.warning("[MCP-OAuth] refresh failed (HTTP %s)", r.status_code)
        return None
    body = _json_answer(r, "token endpoint")
    if "access_token" not in body:
        return None
    return body


async def get_valid_token(server_name: str) -> str | None:
    """Return a usable access token for *server_name*, refreshing if needed."""
    record = await asyncio.to_thread(_load_tokens, server_name)  # a settings read (audit 2026-10-26 AUD-111)
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
    await asyncio.to_thread(
        _save_tokens,
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
