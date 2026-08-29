"""Authentication middleware for sensitive API endpoints.

When the ``KAZMA_SECRET`` environment variable is set, all sensitive API
endpoints (``/api/settings``, ``/api/swarm``, ``/api/mcp``, ``/api/skills``,
``/api/models``, ``/api/ollama``) require an ``X-Kazma-Secret`` request header
whose value matches the env var.  Comparison uses :func:`secrets.compare_digest`
for timing safety.

When ``KAZMA_SECRET`` is **not** set, every endpoint remains open (backward
compatible).

Read-only endpoints (``GET /api/status``, ``GET /api/telemetry``,
``GET /health``, ``/`` page routes, static assets) are **always** open
regardless of whether the secret is configured.

Usage (in ``app.py``)::

    from kazma_ui.auth import create_auth_middleware
    app.middleware("http")(create_auth_middleware())
"""

from __future__ import annotations

import hmac
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# One-shot loud warning for KAZMA_DEMO_MODE (see auth_middleware_with_gate).
_demo_mode_warned = False

# ── Configuration ────────────────────────────────────────────────────────

#: Header name clients must send to authenticate.
SECRET_HEADER = "X-Kazma-Secret"

#: Environment variable holding the shared secret.  When empty/unset the
#: entire auth layer is bypassed (backward-compatible open mode).
SECRET_ENV_VAR = "KAZMA_SECRET"

def _accept_legacy_secret_cookie() -> bool:
    """True only when the operator opted out of opaque sessions.

    Default-on opaque sessions must not treat a ``kazma-secret`` cookie
    (the raw ``KAZMA_SECRET``) as a credential.
    """
    try:
        from kazma_core.security.web_sessions import use_opaque_sessions

        return not use_opaque_sessions()
    except Exception:
        return False


#: Prefer opaque sessions (``SESSION_COOKIE``) — raw secret cookie is legacy.
SECRET_COOKIE = "kazma-secret"

# Re-export opaque session cookie name
try:
    from kazma_core.security.web_sessions import SESSION_COOKIE as SESSION_COOKIE
except Exception:  # pragma: no cover
    SESSION_COOKIE = "kazma-session"


def _is_https(request: Request) -> bool:
    """Check if the request is over HTTPS (either direct or via proxy).

    ``X-Forwarded-Proto`` is honoured only from a declared trusted proxy — a
    direct client could otherwise claim HTTPS and get a ``Secure`` cookie it
    can never send back (audit F-01, same trust boundary as ``_client_host``).
    """
    if request.url.scheme == "https":
        return True
    if _peer_host(request) not in trusted_proxies():
        return False
    return (request.headers.get("x-forwarded-proto") or "").strip().lower() == "https"


#: Comma-separated peer addresses whose ``X-Forwarded-For`` we trust, e.g.
#: ``KAZMA_TRUSTED_PROXIES=127.0.0.1,::1``. Empty (default) = no proxy, so the
#: TCP peer is the client and ``X-Forwarded-For`` is ignored entirely.
TRUSTED_PROXIES_ENV_VAR = "KAZMA_TRUSTED_PROXIES"


def trusted_proxies() -> frozenset[str]:
    """Peer addresses allowed to speak for a client via ``X-Forwarded-For``.

    Read live (not cached) so tests and Settings changes take effect without a
    restart. Only these peers may rewrite the apparent client address — a
    client-supplied ``X-Forwarded-For`` from anywhere else is ignored.
    """
    raw = os.environ.get(TRUSTED_PROXIES_ENV_VAR, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def behind_proxy() -> bool:
    """True when the operator declared a reverse proxy in front of us."""
    return bool(trusted_proxies())


#: Headers only a reverse proxy inserts. Their presence from an *undeclared*
#: peer is the traffic telling us the deployment topology is not what
#: ``KAZMA_TRUSTED_PROXIES`` claims.
_FORWARDED_HEADERS = ("x-forwarded-for", "x-forwarded-proto", "x-real-ip", "forwarded")

#: Set once a proxied request arrives from a peer we were not told about.
#: Never cleared at runtime — the topology does not change mid-process, and a
#: flag that could be cleared would be a way to re-open the hole.
_undeclared_proxy: dict[str, Any] = {"seen": False, "peer": "", "warned": False}


def undeclared_proxy_detected() -> bool:
    """True once a forwarded header arrived from a peer not in the allowlist.

    See :func:`_note_forwarded_headers` for why this exists.
    """
    return bool(_undeclared_proxy["seen"])


def reset_proxy_detection() -> None:
    """Clear the detection latch (tests only)."""
    _undeclared_proxy.update({"seen": False, "peer": "", "warned": False})


def _note_forwarded_headers(request: Request) -> None:
    """Detect a reverse proxy the operator did not declare, and fail closed.

    ``KAZMA_TRUSTED_PROXIES`` is the operator's *claim* about the topology,
    and a wrong claim fails in two directions. Unset (or pointed at the wrong
    address) while a same-host proxy is really in front means every visitor
    arrives as ``127.0.0.1`` and inherits operator trust — audit F-01. Under
    Docker the proxy's address is the bridge IP, not ``127.0.0.1``, so the
    natural guess is wrong and the failure is silent.

    The traffic itself settles it. Only a proxy inserts ``X-Forwarded-*``. If
    one shows up from a peer that is *not* on the allowlist, then either a
    proxy is in front and undeclared, or a client is spoofing the header —
    and both mean peer address has stopped being evidence of anything. So we
    latch the observation and stop treating peer address as a credential.

    Spoofing therefore costs an attacker the loopback convenience login and
    buys them nothing; the operator can still authenticate with the secret.
    That asymmetry is deliberate — this is only ever allowed to close doors.
    """
    if _undeclared_proxy["seen"]:
        return
    peer = _peer_host(request)
    if not peer or peer in trusted_proxies():
        return
    try:
        headers = request.headers
        present = [h for h in _FORWARDED_HEADERS if headers.get(h)]
    except Exception:
        return
    if not present:
        return

    _undeclared_proxy["seen"] = True
    _undeclared_proxy["peer"] = peer
    if not _undeclared_proxy["warned"]:
        _undeclared_proxy["warned"] = True
        logger.error(
            "[SECURITY] %s arrived from peer %s, which is not in %s. A reverse "
            "proxy is in front of this instance and was not declared (under "
            "Docker its address is the bridge IP, not 127.0.0.1). "
            "Peer-address trust is now DISABLED for the life of this process "
            "— no client will be auto-logged-in, and X-Forwarded-For is "
            "still ignored, so per-client rate limiting is degraded. "
            "Set %s=%s and restart.",
            "/".join(present),
            peer,
            TRUSTED_PROXIES_ENV_VAR,
            TRUSTED_PROXIES_ENV_VAR,
            peer,
        )


def _peer_host(request: Request) -> str:
    """The raw TCP peer address (never the forwarded client)."""
    if request.client is None:
        return ""
    return (request.client.host or "").strip().lower()


def _client_host(request: Request) -> str:
    """The *client* address — forwarded value only when the peer is a trusted proxy.

    Audit F-01: this used to return the TCP peer unconditionally. Behind a
    same-host reverse proxy (the topology ``docs/guide/deployment.md``
    recommends) the peer is ``127.0.0.1`` for every internet visitor, which
    made every anonymous request look like the local operator. We now honour
    ``X-Forwarded-For`` — but *only* from a peer the operator listed in
    ``KAZMA_TRUSTED_PROXIES``, so a spoofed header from a direct client is
    still ignored.
    """
    peer = _peer_host(request)
    if not peer or peer not in trusted_proxies():
        return peer
    # Left-most entry is the original client; the proxy appends its own view.
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded.lower() or peer


def _is_loopback_client(request: Request) -> bool:
    """True when the resolved client address is loopback (local operator)."""
    host = _client_host(request)
    return host in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")


def _is_private_lan_client(request: Request) -> bool:
    """True for RFC1918 / link-local peers (home lab, WSL, Docker bridge)."""
    host = _client_host(request)
    if not host:
        return False
    try:
        import ipaddress

        ip = ipaddress.ip_address(host.split("%")[0])  # drop IPv6 zone id
        return bool(ip.is_private or ip.is_link_local)
    except ValueError:
        return False


def _trust_lan_enabled() -> bool:
    """Auto-auth private LAN when secret is set.

    Default **OFF** for production safety (audit C2). Set
    ``KAZMA_TRUST_LAN=1`` for WSL/LAN single-operator labs that need cookie
    auto-issue without ``/login``.
    """
    raw = (os.environ.get("KAZMA_TRUST_LAN") or "0").strip().lower()
    return raw in ("1", "true", "on", "yes")


#: Extra allowed WebSocket origins (comma-separated, exact ``scheme://host[:port]``
#: strings, e.g. ``https://tunnel.example.com``). Used by the CSWSH guard below
#: when the operator intentionally serves the app under a different origin.
WS_EXTRA_ORIGINS_ENV_VAR = "KAZMA_WS_EXTRA_ORIGINS"


def _ws_origin_check_enabled() -> bool:
    """Kill-switch for the WebSocket cross-origin guard.

    Set ``KAZMA_WS_ORIGIN_CHECK=0`` to restore pre-guard behaviour (loopback /
    trusted-LAN peers authenticated with no Origin validation). Default ON.
    """
    return os.environ.get("KAZMA_WS_ORIGIN_CHECK", "").strip().lower() not in (
        "0", "false", "no",
    )


def _extract_ws_header(websocket: Any, name: str) -> str:
    """Case-insensitive header lookup on a WebSocket, tolerating raw scopes."""
    headers = getattr(websocket, "headers", None)
    if headers is None:
        return ""
    try:
        value = headers.get(name)  # Starlette Headers — case-insensitive
        if value:
            return str(value).strip()
    except Exception:
        pass  # not a Starlette Headers mapping — try the raw scope below
    # Raw ASGI scope headers: sequence of (name, value) byte pairs.
    try:
        target = name.lower().encode("latin-1")
        for key, val in headers:
            if str(key).lower().encode("latin-1") != target:
                continue
            if isinstance(val, bytes):
                return val.decode("latin-1", "ignore").strip()
            return str(val).strip()
    except Exception:
        pass  # unrecognised scope shape — header simply absent
    return ""


def _ws_origin_allowed(websocket: Any) -> bool:
    """Cross-Site WebSocket Hijacking guard for credential-less trust paths.

    Any public page can open ``ws://127.0.0.1:<port>/ws/...`` from a visitor's
    browser; the TCP peer is then loopback and used to be trusted with NO
    credential — letting an attacker consume HITL state or drive turns.
    Browsers always attach an ``Origin`` header to WS handshakes, so allow
    only when:

      1. The header is absent (curl / TUI / non-browser client), OR
      2. It matches the request ``Host`` exactly (same-origin page), OR
      3. It appears in ``KAZMA_WS_EXTRA_ORIGINS`` (comma-separated
         ``scheme://host[:port]`` entries).

    Kill-switch: ``KAZMA_WS_ORIGIN_CHECK=0`` disables the guard entirely.
    """
    if not _ws_origin_check_enabled():
        return True
    origin = _extract_ws_header(websocket, "origin")
    if not origin:
        return True  # non-browser client
    lowered = origin.strip().lower()
    host = _extract_ws_header(websocket, "host")
    # Browsers serialise Origin as scheme://host[:port]; compare its
    # authority (host[:port]) against the request Host header.
    try:
        from urllib.parse import urlsplit

        origin_authority = (urlsplit(origin).netloc or "").strip().lower()
    except Exception:
        origin_authority = ""
    if host and origin_authority and origin_authority == host.strip().lower():
        return True
    extra = {
        o.strip().lower()
        for o in os.environ.get(WS_EXTRA_ORIGINS_ENV_VAR, "").split(",")
        if o.strip()
    }
    if lowered in extra:
        return True
    logger.warning(
        "[SECURITY] WebSocket handshake rejected by Origin check "
        "(origin=%s, host=%s) — add it to KAZMA_WS_EXTRA_ORIGINS to allow",
        origin,
        host or "<none>",
    )
    return False


def _loopback_auto_login_enabled() -> bool:
    """Whether a loopback peer may still auto-login when a proxy is declared.

    Off by default (audit F-01). With ``KAZMA_TRUSTED_PROXIES`` set, a
    ``127.0.0.1`` client address can legitimately be the proxy speaking for a
    remote visitor whose ``X-Forwarded-For`` was stripped, so peer address is
    no longer proof of local operation. Operators who genuinely want
    credential-less loopback login alongside a proxy set
    ``KAZMA_LOOPBACK_AUTOLOGIN=1``.
    """
    raw = (os.environ.get("KAZMA_LOOPBACK_AUTOLOGIN") or "0").strip().lower()
    return raw in ("1", "true", "on", "yes")


def _peer_trust_allowed(request: Request) -> bool:
    """Whether peer address may be treated as a credential for this request.

    Direct-bind deployments (no proxy declared): yes — the peer really is the
    client, and single-operator localhost use depends on it. Proxied
    deployments: no, unless explicitly re-enabled. See audit F-01.

    A proxy the operator did *not* declare also revokes peer trust, so a
    mis-set ``KAZMA_TRUSTED_PROXIES`` (the Docker bridge address is not
    ``127.0.0.1``) fails closed instead of silently leaving the bypass open.
    """
    # Cheap when already latched; one header read per request otherwise.
    _note_forwarded_headers(request)
    if undeclared_proxy_detected():
        return False
    if not behind_proxy():
        return True
    if _loopback_auto_login_enabled():
        return True
    # A proxy is declared: trust the peer only when it is NOT the proxy, i.e.
    # a real direct-to-app connection that bypassed the proxy entirely.
    return _peer_host(request) not in trusted_proxies()


def _should_auto_issue_cookie(request: Request, expected: str) -> bool:
    """Whether to Set-Cookie the secret without an explicit login.

    - Loopback clients: yes, when peer trust applies (see
      :func:`_peer_trust_allowed`).
    - Private LAN when ``KAZMA_TRUST_LAN=1``: same condition.
    - Remote clients with a valid X-Kazma-Secret header: yes.
    - Public internet clients: no — must use /login.
    """
    if not expected:
        return False
    if _peer_trust_allowed(request):
        if _is_loopback_client(request):
            return True
        if _trust_lan_enabled() and _is_private_lan_client(request):
            return True
    provided = request.headers.get(SECRET_HEADER, "")
    return bool(provided and verify_secret(provided, expected))


def _wants_html_response(request: Request) -> bool:
    """True when the client expects an HTML document (browser nav / soft-nav)."""
    if request.headers.get("Kazma-Soft-Nav"):
        return True
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return True
    # Top-level page navigations (not /api/*)
    if request.method == "GET" and not request.url.path.startswith("/api/"):
        return True
    return False


def _unauthorized_response(request: Request) -> Response:
    """401 JSON for APIs; redirect browsers to /login?next=… for HTML pages."""
    from fastapi.responses import RedirectResponse

    if _wants_html_response(request):
        nxt = request.url.path
        if request.url.query:
            nxt = f"{nxt}?{request.url.query}"
        # Only same-origin relative next
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        from urllib.parse import quote

        return RedirectResponse(
            url=f"/login?next={quote(nxt, safe='/?&=')}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    response = Response(
        content='{"detail":"Missing or invalid X-Kazma-Secret header"}',
        status_code=status.HTTP_401_UNAUTHORIZED,
        media_type="application/json",
        headers={"WWW-Authenticate": SECRET_HEADER},
    )
    if request.cookies.get(SECRET_COOKIE):
        response.delete_cookie(SECRET_COOKIE, path="/")
    return response

#: Explicit sensitive prefixes (kept for docs / tests). Auth now **default-denies**
#: all ``/api/*`` and selected admin HTML pages when a secret is set (audit M1).
SENSITIVE_PREFIXES: tuple[str, ...] = (
    "/api/",  # default-deny: any /api/* not in ALWAYS_OPEN_* is gated
    "/metrics",
    "/v1/models",
    # Auto-generated API docs/schema — gate them so the OpenAPI surface
    # isn't reachable unauthenticated (and the PEP-563 /openapi.json 500
    # doesn't surface to anonymous visitors). Disabled entirely in prod
    # via FastAPI(docs_url=None, openapi_url=None) — see app.py.
    "/docs",
    "/redoc",
    "/openapi.json",
    # Admin HTML shells (audit M2)
    "/dashboard",
    "/settings",
    "/ide",
    "/swarm",
    "/agents",
    "/workspace",
    "/workspaces",
    "/memory",
    "/mcp",
    "/skills",
    "/pipelines",
    "/cron",
    "/observability",
    "/replay",
    "/research",
    "/knowledge",  # Knowledge Library admin shell (audit residual)
)

#: Exact read-only paths that are always open regardless of secret config.
#  (Page routes like "/", "/chat", "/workspace" and static files are
#  handled separately — they never start with a sensitive prefix.)
ALWAYS_OPEN_PATHS: frozenset[str] = frozenset({
    "/",
    "/health",
    "/health/live",
    "/health/ready",
    "/health/deep",  # ops canary — bounded work, TTL-cached 30s
    "/api/status",
    "/api/telemetry",
    "/favicon.ico",
    # RFC 9116 security contact file — must be reachable without auth.
    "/.well-known/security.txt",
    "/security.txt",
    # MCP preset catalog — read-only metadata (server names + npx commands),
    # no secrets or user data. Open so the Add Server dropdown works without
    # a stale-auth 401 on first page load / localhost probes.
    "/api/mcp/presets",
    # Explicit auth bootstrap (remote clients cannot use loopback auto-cookie)
    "/login",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/status",
    "/api/auth/oidc/start",
    "/api/auth/oidc/callback",
})

#: Path prefixes that are always open (browser-redirect targets that
#  cannot carry the X-Kazma-Secret header, e.g. the GitHub OAuth callback
#  which GitHub redirects to with only a ?code= query param).
ALWAYS_OPEN_PREFIXES: tuple[str, ...] = (
    # Callbacks are browser redirects with ?code= only (no secret header).
    "/api/github/oauth/callback",
    # /api/github/oauth/start is intentionally *not* open: starting OAuth
    # requires an authenticated session/cookie (audit residual — unauth start
    # could write oauth_state into ConfigStore).
    # Email OAuth: Google/Microsoft redirect with ?code= only (no secret header)
    "/api/email/oauth/gmail/callback",
    "/api/email/oauth/microsoft/callback",
    # NOTE: there is deliberately no "/api/auth" prefix entry. Every route that
    # must be reachable before login is listed individually in
    # ALWAYS_OPEN_PATHS, so a new /api/auth/* endpoint is gated by default.
    # A "/api/auth/" entry used to sit here and was inert anyway — the trailing
    # slash made is_always_open test `startswith("/api/auth//")` (audit F-15).
)


# ── Helpers ──────────────────────────────────────────────────────────────

_generated_secret: str | None = None


def get_kazma_secret() -> str:
    """Return the configured ``KAZMA_SECRET`` (delegates to config_store).

    Single source of truth: :func:`kazma_core.config_store.get_kazma_secret`.
    Cached in-module for UI middleware hot path after first resolve.
    """
    global _generated_secret
    if _generated_secret is not None:
        return _generated_secret

    # Env override still short-circuits without store (and without caching empty)
    env_secret = os.environ.get(SECRET_ENV_VAR, "").strip()
    if env_secret:
        return env_secret

    try:
        from kazma_core.config_store import get_kazma_secret as _core_get

        secret = _core_get()
        # Only cache non-empty secrets so tests can still flip env/open mode
        if secret:
            _generated_secret = secret
        return secret
    except Exception as exc:
        # Fail-loud: never silently disable auth (open admin). Use a cached
        # ephemeral random secret so tokens stay consistent within the process.
        import secrets as _secrets

        logger.error(
            "[SECURITY] config_store get_kazma_secret failed: %s — auth using "
            "ephemeral random secret; set KAZMA_SECRET to persist it",
            exc,
        )
        if _generated_secret is None:
            _generated_secret = _secrets.token_hex(16)
        return _generated_secret


def is_sensitive_path(path: str) -> bool:
    """Return *True* if *path* requires auth when a secret is configured.

    Policy (audit M1): default-deny all ``/api/*`` and admin page shells.
    Paths in :data:`ALWAYS_OPEN_PATHS` / :data:`ALWAYS_OPEN_PREFIXES` are
    handled separately by the middleware and never gated.
    """
    # Normalise trailing slashes so "/api/settings/" matches the prefix.
    normalised = path.rstrip("/") or "/"
    # Default-deny every API route (new endpoints cannot ship open by omission).
    if normalised == "/api" or normalised.startswith("/api/"):
        return True
    for prefix in SENSITIVE_PREFIXES:
        if prefix in ("/api/", "/api"):
            continue
        if normalised == prefix or normalised.startswith(prefix + "/"):
            return True
    return False


def is_always_open(path: str) -> bool:
    """Return *True* for read-only/page/redirect routes that bypass auth.

    Prefixes are normalised so an entry written with or without a trailing
    slash behaves identically — a stray slash used to make an entry match
    nothing at all (audit F-15).
    """
    if path in ALWAYS_OPEN_PATHS:
        return True
    for prefix in ALWAYS_OPEN_PREFIXES:
        p = prefix.rstrip("/")
        if not p:
            continue
        if path == p or path.startswith(p + "/"):
            return True
    return False


def verify_secret(provided: str, expected: str) -> bool:
    """Timing-safe comparison of *provided* against *expected*.

    Uses :func:`hmac.compare_digest` (alias of :func:`secrets.compare_digest`).
    Both arguments are coerced to ``str`` before comparison.
    """
    try:
        return hmac.compare_digest(str(provided or ""), str(expected or ""))
    except Exception:
        return False


def verify_api_token(provided: str) -> bool:
    """Return True when *provided* is a valid Account API token (``kazma_…``).

    Tokens are created in Settings → Account. Only the SHA-256 hash is stored
    (never the raw token). Accepts the raw token string from the create dialog.
    """
    if not provided or not str(provided).startswith("kazma_"):
        return False
    try:
        import hashlib
        import json
        from datetime import UTC, datetime

        from kazma_core.config_store import get_config_store

        token_hash = hashlib.sha256(provided.encode("utf-8")).hexdigest()
        raw = get_config_store().get("account.tokens", [])
        # Peel legacy double-json encoding (create used to json.dumps before set).
        for _ in range(3):
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    raw = []
                    break
            else:
                break
        if not isinstance(raw, list):
            return False
        now = datetime.now(UTC)
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            if entry.get("token_hash") != token_hash:
                continue
            expires_days = entry.get("expires_days")
            created = entry.get("created_at") or ""
            if expires_days and created:
                try:
                    created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    if created_dt.tzinfo is None:
                        created_dt = created_dt.replace(tzinfo=UTC)
                    if (now - created_dt).days > int(expires_days):
                        return False
                except Exception:
                    # Fail CLOSED (audit O3): this used to `pass` and fall
                    # through to `return True`, so a token whose expiry could
                    # not be parsed was accepted forever. A token that claims
                    # an expiry we cannot evaluate is not a valid token.
                    logger.warning(
                        "[SECURITY] API token has unparseable expiry "
                        "(created_at=%r expires_days=%r) — rejecting",
                        created, expires_days,
                    )
                    return False
            return True
        return False
    except Exception:
        logger.debug("[SECURITY] API token verify failed", exc_info=True)
        return False


# ── Per-session WebSocket tokens ─────────────────────────────────────────
# The browser can't set custom headers on a WebSocket handshake, so the
# WS auth path uses ?token=... query parameter. Previously this exposed
# the raw KAZMA_SECRET (which gates ALL HTTP APIs) in browser history,
# proxy logs, and view-source via a <meta> tag. These functions generate
# short-lived, per-process WS tokens that grant ONLY WebSocket access and
# expire after 1 hour — never the raw secret.

_ws_session_tokens: dict[str, float] = {}  # token → expiry epoch
_WS_TOKEN_TTL_SECONDS = 3600  # 1 hour


def generate_ws_session_token() -> str:
    """Generate a short-lived per-session WS token (NOT the raw KAZMA_SECRET).

    The token grants ONLY WebSocket access and expires after 1 hour.
    Call this once per page render and inject into the meta tag so the
    browser JS can use it for WS ?token=... without exposing the secret.
    """
    import secrets
    import time

    # Prune expired tokens (keep dict small).
    now = time.time()
    expired = [k for k, exp in _ws_session_tokens.items() if exp < now]
    for k in expired:
        del _ws_session_tokens[k]

    token = secrets.token_urlsafe(32)
    _ws_session_tokens[token] = now + _WS_TOKEN_TTL_SECONDS
    return token


def verify_ws_session_token(token: str) -> bool:
    """Verify a per-session WS token. Returns False if expired or unknown."""
    import time

    if not token:
        return False
    expiry = _ws_session_tokens.get(token)
    if expiry is None:
        return False
    if time.time() > expiry:
        _ws_session_tokens.pop(token, None)
        return False
    return True


def extract_provided_credential(request: Request) -> str:
    """Pull auth material from headers/cookie (secret, session, or API token).

    Order:
      1. ``X-Kazma-Secret``
      2. ``X-Api-Token`` / ``X-Kazma-Token``
      3. ``Authorization: Bearer …``
      4. ``kazma-session`` opaque cookie (preferred)
      5. ``kazma-secret`` cookie — only when opaque sessions are off
    """
    provided = (request.headers.get(SECRET_HEADER) or "").strip()
    if provided:
        return provided
    provided = (
        request.headers.get("X-Api-Token")
        or request.headers.get("X-Kazma-Token")
        or ""
    ).strip()
    if provided:
        return provided
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    sess = (request.cookies.get(SESSION_COOKIE) or "").strip()
    if sess:
        return f"session:{sess}"
    if _accept_legacy_secret_cookie():
        return (request.cookies.get(SECRET_COOKIE) or "").strip()
    return ""


def is_authenticated(request: Request, expected_secret: str = "") -> bool:
    """True if request carries a valid secret, opaque session, or API token."""
    provided = extract_provided_credential(request)
    if not provided:
        return False
    if provided.startswith("session:"):
        try:
            from kazma_core.security.web_sessions import validate_session

            return validate_session(provided[8:])
        except Exception:
            return False
    expected = expected_secret or get_kazma_secret()
    if expected and verify_secret(provided, expected):
        return True
    if verify_api_token(provided):
        return True
    return False


def websocket_is_authenticated(websocket: Any, expected_secret: str = "") -> bool:
    """Auth for WebSocket handshakes (cookies/headers/query/loopback/private LAN).

    Accepts the same credentials as HTTP, plus query parameter token:
      1. ``X-Kazma-Secret`` header
      2. ``Authorization: Bearer …``
      3. ``?token=…`` query parameter — accepts a **per-session WS token**
         (NOT the raw KAZMA_SECRET). See ``generate_ws_session_token()``.
      4. ``kazma-session`` opaque cookie (preferred, mint by /login or TRUST_LAN)
      5. ``kazma-secret`` legacy cookie — only when opaque sessions are off
      6. Loopback or private LAN peers (WSL bridge 172.28.x.x, Docker 172.17.x.x, 192.168.x.x)
         — with a cross-origin check (``_ws_origin_allowed``): a public page can
         open ``ws://127.0.0.1`` from any browser (CSWSH), so the handshake
         ``Origin`` must be absent / same-host / allow-listed
         (``KAZMA_WS_EXTRA_ORIGINS``). Kill-switch ``KAZMA_WS_ORIGIN_CHECK=0``.
         If the Origin check fails, credential paths below are still tried.
         Peer trust is additionally disabled whenever a reverse proxy is
         declared (``KAZMA_TRUSTED_PROXIES``) — see :func:`_peer_trust_allowed`
         and audit F-01, where an absent ``Origin`` plus a proxied loopback
         peer authenticated any anonymous non-browser client.
      7. Dev bypass: ``KAZMA_DEV_WS_BYPASS=1`` (local testing only — blocked in production)
    """
    expected = expected_secret or get_kazma_secret()

    # Dev bypass for local testing — never enable in production
    if os.environ.get("KAZMA_DEV_WS_BYPASS", "").strip().lower() in ("1", "true", "yes", "on"):
        if os.environ.get("KAZMA_PRODUCTION", "").strip().lower() in ("1", "true", "yes"):
            logger.error(
                "[SECURITY] KAZMA_DEV_WS_BYPASS is set but KAZMA_PRODUCTION=1 "
                "— refusing to bypass WebSocket auth"
            )
        else:
            return True

    if not expected:
        return True

    # Loopback peers: trusted for single-operator local use.
    # The browser authenticates via the same-origin kazma-session cookie; the
    # old <meta> token path was removed (leaked the bearer into page source /
    # logs). Loopback trust keeps local single-operator use working without
    # any credential — but a public page can open ws://127.0.0.1 from any
    # visitor's browser (CSWSH), so require the handshake Origin to be
    # absent / same-host / explicitly allow-listed first. Callers reject
    # with websocket.close(code=1008, ...) on this policy-violation path;
    # credentialed clients still authenticate via the checks below.
    if _peer_trust_allowed(websocket):
        if _is_loopback_client(websocket) and _ws_origin_allowed(websocket):
            return True

        # Private LAN peers (WSL bridge, Docker, 192.168.x.x) — only if TRUST_LAN enabled
        if (
            _is_private_lan_client(websocket)
            and _trust_lan_enabled()
            and _ws_origin_allowed(websocket)
        ):
            return True

    # Query parameter: per-session WS token only — never the raw KAZMA_SECRET
    # (URL query lands in access logs / Referer).
    provided = ""
    try:
        query_params = websocket.query_params
        if query_params:
            provided = (query_params.get("token") or "").strip()
    except Exception:
        pass  # no query params on this transport — treated as no token

    if provided and verify_ws_session_token(provided):
        return True
    # Ignore leftover query tokens that are not session tokens (incl. raw secret).
    provided = ""

    if not provided:
        provided = (websocket.headers.get(SECRET_HEADER) or "").strip()
    if not provided:
        auth = (websocket.headers.get("authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
    if not provided:
        sess = (websocket.cookies.get(SESSION_COOKIE) or "").strip()
        if sess:
            try:
                from kazma_core.security.web_sessions import validate_session

                if validate_session(sess):
                    return True
            except Exception:
                # Fail-closed by construction (audit O3): an unvalidated
                # session simply does not authenticate; the credential checks
                # below still run. Logged because a session-store outage
                # otherwise looks like mass credential rejection.
                logger.warning(
                    "[SECURITY] WebSocket session validation failed",
                    exc_info=True,
                )
    if not provided and _accept_legacy_secret_cookie():
        provided = (websocket.cookies.get(SECRET_COOKIE) or "").strip()
    if not provided:
        return False
    if verify_secret(provided, expected):
        return True
    if verify_api_token(provided):
        return True
    return False


def get_request_principal(request: Request) -> dict[str, Any] | None:
    """Return authenticated principal {username, role, user_id, source} or None."""
    provided = extract_provided_credential(request)
    if not provided:
        return None
    if provided.startswith("session:"):
        try:
            from kazma_core.security.web_sessions import get_session_payload

            payload = get_session_payload(provided[8:])
            if not payload:
                return None
            return {
                "username": payload.get("username") or payload.get("actor") or "session",
                "role": payload.get("role") or "admin",
                "user_id": payload.get("user_id") or "",
                "source": "session",
            }
        except Exception:
            return None
    expected = get_kazma_secret()
    if expected and verify_secret(provided, expected):
        return {
            "username": "operator",
            "role": "admin",
            "user_id": "shared-secret",
            "source": "secret",
        }
    if verify_api_token(provided):
        return {
            "username": "api-token",
            "role": "operator",
            "user_id": "api-token",
            "source": "api_token",
        }
    return None


def _mint_auth_cookie(response: Response, request: Request, expected: str) -> None:
    """Set browser auth cookie — opaque session preferred (audit H1)."""
    try:
        from kazma_core.security.web_sessions import (
            SESSION_COOKIE as _SC,
        )
        from kazma_core.security.web_sessions import (
            create_session,
            use_opaque_sessions,
        )

        if use_opaque_sessions():
            # Don't re-mint if valid session already present
            existing = (request.cookies.get(_SC) or "").strip()
            if existing:
                from kazma_core.security.web_sessions import validate_session

                if validate_session(existing):
                    return
            sid = create_session(actor="auto-cookie")
            response.set_cookie(
                key=_SC,
                value=sid,
                httponly=True,
                samesite="lax",
                path="/",
                secure=_is_https(request),
                max_age=60 * 60 * 24 * 14,
            )
            # Drop legacy secret cookie if present
            if request.cookies.get(SECRET_COOKIE):
                response.delete_cookie(SECRET_COOKIE, path="/")
            return
    except Exception as exc:
        logger.warning("[auth] opaque session mint failed — not writing raw secret cookie: %s", exc)

    # Never put KAZMA_SECRET in a cookie. Drop any leftover legacy cookie.
    if request.cookies.get(SECRET_COOKIE):
        response.delete_cookie(SECRET_COOKIE, path="/")


# ── FastAPI Dependency (for manual application) ─────────────────────────


def require_kazma_secret(
    x_kazma_secret: str = Header(default="", alias=SECRET_HEADER),
) -> None:
    """FastAPI dependency that enforces ``X-Kazma-Secret`` or Account API token.

    Raise ``HTTPException(401)`` when the secret is configured and the
    header is missing or incorrect.  When the secret is unset this is a
    no-op (backward compatible).
    """
    expected = get_kazma_secret()
    if not expected:
        return  # Auth disabled — open mode.
    if expected and verify_secret(x_kazma_secret, expected):
        return
    if verify_api_token(x_kazma_secret):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing or invalid X-Kazma-Secret header (or Account API token)",
        headers={"WWW-Authenticate": SECRET_HEADER},
    )


# ── Middleware Factory ──────────────────────────────────────────────────


def create_auth_middleware(
    secret: str | None = None,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Create an ASGI/Starlette HTTP middleware enforcing ``KAZMA_SECRET``.

    Args:
        secret: Optional explicit secret.  When ``None`` (default) the
            secret is read from the ``KAZMA_SECRET`` env var at **each
            request** so tests can monkeypatch ``os.environ`` dynamically.

    Returns:
        Middleware coroutine suitable for ``app.middleware("http")(...)``.
    """
    # Capture a static secret when provided; otherwise resolve per-request.
    static_secret = secret

    # ── Gate for sensitive paths (dead always-cookie middleware removed — L1)
    async def auth_middleware_with_gate(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        expected = static_secret if static_secret is not None else get_kazma_secret()

        # 0. Public demo mode: KAZMA_DEMO_MODE bypasses the secret gate so a
        #    public demo (e.g. kazma-demo.fly.dev) is open to all visitors
        #    without login. Only enable this on a throwaway demo instance —
        #    never on a production deployment with real secrets/data.
        if os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes"):
            global _demo_mode_warned
            if not _demo_mode_warned:
                _demo_mode_warned = True
                logger.warning(
                    "[Auth] KAZMA_DEMO_MODE is active — the ENTIRE auth gate is "
                    "disabled and every /api endpoint is open. Enable this only "
                    "on throwaway demo instances."
                )
            if os.environ.get("KAZMA_PRODUCTION", "").lower() in ("1", "true", "yes"):
                # Refuse to silently open a production instance.
                logger.error(
                    "[Auth] KAZMA_DEMO_MODE and KAZMA_PRODUCTION are both set — "
                    "refusing to disable auth; returning 503."
                )
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": (
                            "KAZMA_DEMO_MODE cannot be combined with "
                            "KAZMA_PRODUCTION — refusing to disable auth."
                        )
                    },
                )
            return await call_next(request)

        # 1. Read-only & page routes always pass through.
        # Cookie auto-issue only for loopback or when secret header is present
        # (never mint auth cookie for anonymous remote visitors — C2 fix).
        if is_always_open(path):
            response = await call_next(request)
            if expected and _should_auto_issue_cookie(request, expected):
                _mint_auth_cookie(response, request, expected)
            return response

        # 2. Only sensitive paths are gated (default-deny /api/* + admin shells).
        # Static assets and non-admin HTML remain open for soft-nav shells.
        if not is_sensitive_path(path):
            response = await call_next(request)
            if expected and _should_auto_issue_cookie(request, expected):
                _mint_auth_cookie(response, request, expected)
            return response

        # 3. No secret configured → open mode UNLESS the caller presented an
        #    Account API token (still validate those when present).
        provided = extract_provided_credential(request)
        if not expected:
            # Open mode: still accept valid API tokens; otherwise pass through.
            if provided and provided.startswith("kazma_") and not verify_api_token(provided):
                return _unauthorized_response(request)
            return await call_next(request)

        # 4. Verify KAZMA_SECRET / opaque session / Account API token.
        if not is_authenticated(request, expected):
            return _unauthorized_response(request)

        # 4b. Platform RBAC (Phase 4.4) when multi-user is enabled.
        # Fail-closed: if we know multi-user is on and the check errors, deny.
        _multi_user = False
        try:
            from kazma_core.security.platform_rbac import multi_user_enabled

            _multi_user = bool(multi_user_enabled())
        except Exception as exc:
            if (os.environ.get("KAZMA_MULTI_USER") or "").strip().lower() in (
                "1", "true", "on", "yes",
            ):
                logger.warning("[RBAC] multi-user check failed — denying: %s", exc)
                return Response(
                    content='{"detail":"Forbidden (RBAC unavailable)"}',
                    status_code=403,
                    media_type="application/json",
                )
            logger.debug("[RBAC] multi-user check skipped: %s", exc)
        if _multi_user:
            try:
                from kazma_core.security.platform_rbac import role_allows

                principal = get_request_principal(request)
                role = (principal or {}).get("role") or "viewer"
                # Shared-secret and admin still full access
                if (principal or {}).get("source") != "secret" and role != "admin":
                    if not role_allows(str(role), path, request.method):
                        logger.warning(
                            "[RBAC] denied role=%s %s %s",
                            role,
                            request.method,
                            path,
                        )
                        return Response(
                            content='{"detail":"Forbidden for your role"}',
                            status_code=403,
                            media_type="application/json",
                        )
            except Exception as exc:
                logger.warning("[RBAC] check failed — denying: %s", exc)
                return Response(
                    content='{"detail":"Forbidden (RBAC unavailable)"}',
                    status_code=403,
                    media_type="application/json",
                )

        response = await call_next(request)
        # Refresh cookie only for secret-header auth (not API tokens)
        if expected and provided and not provided.startswith("session:") and not provided.startswith("kazma_"):
            if verify_secret(provided, expected):
                _mint_auth_cookie(response, request, expected)
        return response

    return auth_middleware_with_gate


def extract_tenant_from_jwt(token: str) -> str | None:
    """Extract tenant_id only from a *verified* JWT.

    Unverified base64 payload decoding is disabled (forgery risk). Set
    ``KAZMA_JWT_SECRET`` to enable HS256 verification; otherwise returns None.
    """
    secret = os.environ.get("KAZMA_JWT_SECRET", "").strip()
    if not secret:
        logger.debug("[TENANT] JWT tenant extraction disabled (KAZMA_JWT_SECRET unset)")
        return None
    try:
        import jwt as _jwt  # PyJWT
        payload = _jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"require": ["exp"]},
        )
        if isinstance(payload, dict):
            tid = payload.get("tenant_id") or payload.get("tenant")
            return str(tid) if tid else None
    except Exception as exc:
        logger.debug("[TENANT] JWT verification failed: %s", exc)
    return None


def create_tenant_middleware() -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """Create an HTTP middleware that extracts tenant id and propagates it.

    Production **or** multi-user mode: client-supplied ``X-Tenant-ID`` header
    and cookies are **ignored** unless a verified JWT / opaque principal is
    present (audit H11 + SaaS residual). Single-tenant default is ``default``.
    """
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id
    from kazma_core.tenant_isolation import (
        client_tenant_spoof_allowed,
        principal_tenant_id,
    )

    async def tenant_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        allow_spoof = client_tenant_spoof_allowed()
        tenant_id: str | None = None
        # Whether the tenant was EXPLICITLY chosen (JWT/principal/header/cookie)
        # vs the 'default' fallback. The X-Tenant-ID cookie is only persisted
        # for explicit choices — stamping 'default' onto every anonymous
        # request would pollute clients for no benefit (audit follow-up).
        explicit_tenant = False

        # Always try verified JWT first
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if auth_header and auth_header.lower().startswith("bearer "):
            tenant_id = extract_tenant_from_jwt(auth_header[7:].strip())
            if tenant_id:
                explicit_tenant = True
        if not tenant_id:
            for cookie_name in ("jwt", "token", "x-tenant-id-jwt", "tenant_jwt"):
                tok = request.cookies.get(cookie_name)
                if tok:
                    tenant_id = extract_tenant_from_jwt(tok)
                    if tenant_id:
                        explicit_tenant = True
                        break

        # Opaque session / principal may carry tenant (multi-user)
        if not tenant_id:
            try:
                principal = get_request_principal(request)
                tenant_id = principal_tenant_id(principal)
                if tenant_id:
                    explicit_tenant = True
            except Exception:
                pass

        # Spoofable header/cookie only in single-tenant non-prod labs
        if not tenant_id and allow_spoof:
            tenant_id = request.headers.get("X-Tenant-ID") or request.headers.get("x-tenant-id")
            if not tenant_id:
                tenant_id = (
                    request.cookies.get("X-Tenant-ID")
                    or request.cookies.get("x-tenant-id")
                    or request.cookies.get("tenant_id")
                )
            if tenant_id:
                explicit_tenant = True
        elif not tenant_id:
            # Hardened mode — never trust client header
            tenant_id = "default"

        if not tenant_id:
            tenant_id = "default"

        if tenant_id:
            logger.debug("[TENANT] Inbound request scoped to tenant_id: %s", tenant_id)

        token = set_current_tenant_id(tenant_id)
        try:
            response = await call_next(request)
            if explicit_tenant and tenant_id and allow_spoof:
                if request.cookies.get("X-Tenant-ID") != tenant_id:
                    response.set_cookie(
                        key="X-Tenant-ID",
                        value=tenant_id,
                        httponly=True,
                        samesite="strict",
                        path="/",
                        secure=_is_https(request),
                    )
            return response
        finally:
            reset_current_tenant_id(token)

    return tenant_middleware


__all__: list[str] = [
    "SECRET_HEADER",
    "SECRET_COOKIE",
    "SECRET_ENV_VAR",
    "SENSITIVE_PREFIXES",
    "ALWAYS_OPEN_PATHS",
    "create_auth_middleware",
    "get_kazma_secret",
    "is_sensitive_path",
    "is_always_open",
    "require_kazma_secret",
    "verify_secret",
    "verify_api_token",
    "extract_provided_credential",
    "is_authenticated",
    "websocket_is_authenticated",
    "create_tenant_middleware",
    "TRUSTED_PROXIES_ENV_VAR",
    "trusted_proxies",
    "behind_proxy",
    "client_address",
    "assert_proxy_configuration",
    "undeclared_proxy_detected",
    "reset_proxy_detection",
    "proxy_health",
]


def proxy_health() -> dict[str, Any]:
    """Operator-facing view of whether the proxy configuration is coherent.

    Surfaced by ``GET /api/auth/status`` so a misconfiguration is visible
    without reading logs — the F-01 class fails silently otherwise.
    """
    declared = sorted(trusted_proxies())
    undeclared = undeclared_proxy_detected()
    if undeclared:
        state = "undeclared_proxy"
    elif declared:
        state = "declared"
    else:
        state = "direct"
    return {
        "state": state,
        "trusted_proxies": declared,
        "peer_trust_active": not undeclared
        and (not declared or _loopback_auto_login_enabled()),
        "observed_proxy_peer": _undeclared_proxy["peer"] or None,
        "hint": (
            f"A proxy at {_undeclared_proxy['peer']} is sending forwarded headers "
            f"but is not in {TRUSTED_PROXIES_ENV_VAR}. Set "
            f"{TRUSTED_PROXIES_ENV_VAR}={_undeclared_proxy['peer']} and restart."
            if undeclared
            else None
        ),
    }


def client_address(request: Request) -> str:
    """Public accessor for the resolved client address (proxy-aware).

    Callers that key state per client — rate limiting, login throttling, audit
    logs — must use this rather than ``request.client.host``, or every request
    behind a reverse proxy collapses into a single bucket (audit F-01/F-12).
    """
    return _client_host(request)


def assert_proxy_configuration() -> None:
    """Warn (loudly) when peer-address trust is active on an exposed bind.

    Called at app startup. Peer trust is safe when the app owns its socket and
    dangerous the moment something else terminates connections for it, so a
    production instance that is neither loopback-bound nor proxy-aware is
    almost certainly the F-01 topology.
    """
    production = (os.environ.get("KAZMA_PRODUCTION") or "").strip().lower() in (
        "1", "true", "on", "yes",
    )
    if not production or behind_proxy():
        return
    bind = (os.environ.get("KAZMA_HOST") or "127.0.0.1").strip().lower()
    if bind in ("127.0.0.1", "::1", "localhost"):
        logger.warning(
            "[SECURITY] KAZMA_PRODUCTION=1 with a loopback bind and no "
            "%s set. If a reverse proxy fronts this instance, set %s to the "
            "proxy address — otherwise every visitor is treated as the local "
            "operator and auto-issued an admin session.",
            TRUSTED_PROXIES_ENV_VAR,
            TRUSTED_PROXIES_ENV_VAR,
        )
        return
    logger.error(
        "[SECURITY] KAZMA_PRODUCTION=1 bound to %s with no %s configured. "
        "Peer-address trust is active: any client whose connection arrives "
        "from a loopback or private address is auto-authenticated. Set %s to "
        "your proxy's address, or set KAZMA_LOOPBACK_AUTOLOGIN=0 explicitly.",
        bind,
        TRUSTED_PROXIES_ENV_VAR,
        TRUSTED_PROXIES_ENV_VAR,
    )


# Silence unused-import warnings for re-exported symbols.
_ = (Any, status, Awaitable)
