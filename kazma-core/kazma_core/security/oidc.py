"""Optional OIDC (OpenID Connect) login — Phase 4.4 IdP foundation.

Configure::

    KAZMA_OIDC_ISSUER=https://accounts.example.com
    KAZMA_OIDC_CLIENT_ID=...
    KAZMA_OIDC_CLIENT_SECRET=...
    KAZMA_OIDC_REDIRECT_URI=https://your.domain/api/auth/oidc/callback
    # optional role claim mapping
    KAZMA_OIDC_ROLE_CLAIM=role
    KAZMA_OIDC_DEFAULT_ROLE=operator
    # optional: the claim naming the user's Kazma tenant (off by default)
    KAZMA_OIDC_TENANT_CLAIM=tenant

Flow: browser → /api/auth/oidc/start → IdP → /api/auth/oidc/callback
→ opaque kazma-session with bound username/role.

Does not require a heavy SDK — uses discovery + authorization code + PKCE.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from kazma_core.http_tls import shared_ssl_context

__all__ = [
    "BINDING_COOKIE_PATH",
    "OidcConfig",
    "binding_cookie_name",
    "build_authorize_url",
    "exchange_code",
    "fetch_discovery",
    "oidc_configured",
    "oidc_role_from_claims",
    "oidc_tenant_from_claims",
]

logger = logging.getLogger(__name__)


def oidc_configured() -> bool:
    return bool(
        (os.environ.get("KAZMA_OIDC_ISSUER") or "").strip()
        and (os.environ.get("KAZMA_OIDC_CLIENT_ID") or "").strip()
    )


class OidcConfig:
    def __init__(self) -> None:
        self.issuer = (os.environ.get("KAZMA_OIDC_ISSUER") or "").strip().rstrip("/")
        self.client_id = (os.environ.get("KAZMA_OIDC_CLIENT_ID") or "").strip()
        self.client_secret = (os.environ.get("KAZMA_OIDC_CLIENT_SECRET") or "").strip()
        self.redirect_uri = (os.environ.get("KAZMA_OIDC_REDIRECT_URI") or "").strip()
        self.scopes = (os.environ.get("KAZMA_OIDC_SCOPES") or "openid profile email").strip()
        self.role_claim = (os.environ.get("KAZMA_OIDC_ROLE_CLAIM") or "role").strip()
        self.default_role = (os.environ.get("KAZMA_OIDC_DEFAULT_ROLE") or "operator").strip()
        # Opt-in: the claim naming the user's Kazma tenant. Unset = every OIDC
        # user shares the default tenant, as before.
        self.tenant_claim = (os.environ.get("KAZMA_OIDC_TENANT_CLAIM") or "").strip()
        # Who of the issuer's accounts may sign in (security audit
        # 2026-11-10). None set: every account the issuer authenticates.
        self.allowed_emails = _env_set("KAZMA_OIDC_ALLOWED_EMAILS")
        self.allowed_domains = {d.lstrip("@") for d in _env_set("KAZMA_OIDC_ALLOWED_DOMAINS")}
        self.allowed_groups = _env_set("KAZMA_OIDC_ALLOWED_GROUPS")
        if self.issuer and not self.restricted:
            _warn_open_issuer_once(self.issuer)

    @property
    def restricted(self) -> bool:
        return bool(self.allowed_emails or self.allowed_domains or self.allowed_groups)


def _env_set(name: str) -> set[str]:
    return {
        part.strip().lower()
        for part in (os.environ.get(name) or "").split(",")
        if part.strip()
    }


_open_issuer_warned: list[bool] = [False]


def _warn_open_issuer_once(issuer: str) -> None:
    if _open_issuer_warned[0]:
        return
    _open_issuer_warned[0] = True
    logger.warning(
        "[oidc] Every account %s authenticates may sign in to Kazma. Name who may "
        "with KAZMA_OIDC_ALLOWED_EMAILS, KAZMA_OIDC_ALLOWED_DOMAINS or "
        "KAZMA_OIDC_ALLOWED_GROUPS.",
        issuer,
    )


def _claim_values(claims: dict[str, Any], *names: str) -> list[str]:
    """Every value of the named claims, a list claim read element by element."""
    values: list[str] = []
    for name in names:
        raw = claims.get(name)
        items = raw if isinstance(raw, list) else [raw]
        values += [str(v).strip() for v in items if v not in (None, "") and str(v).strip()]
    return values


def _email_of(claims: dict[str, Any]) -> str:
    """The claims' email, refused when the IdP says it is not verified.

    An address the IdP has not verified is whatever the account typed: at an
    issuer that lets anyone register, the owner's address (security audit
    2026-11-10). An IdP that does not send ``email_verified`` is taken at its
    word, as before.
    """
    email = str(claims.get("email") or "").strip()
    if email and claims.get("email_verified") in (False, "false", "False"):
        raise PermissionError("OIDC email is not verified by the identity provider")
    return email


def _sign_in_allowed(claims: dict[str, Any], cfg: OidcConfig) -> bool:
    """Whether the allowlists admit this account (always, with none set)."""
    if not cfg.restricted:
        return True
    if cfg.allowed_emails or cfg.allowed_domains:
        email = _email_of(claims).lower()
        if email and email in cfg.allowed_emails:
            return True
        if email and "@" in email and email.rsplit("@", 1)[1] in cfg.allowed_domains:
            return True
    groups = {g.lower() for g in _claim_values(claims, "groups", "roles", cfg.role_claim)}
    return bool(groups & cfg.allowed_groups)


def oidc_tenant_from_claims(claims: dict[str, Any], cfg: OidcConfig) -> str | None:
    """The tenant named by ``KAZMA_OIDC_TENANT_CLAIM``, if configured and valid."""
    if not cfg.tenant_claim:
        return None
    value = claims.get(cfg.tenant_claim)
    if isinstance(value, list):
        value = value[0] if len(value) == 1 else None
    if not value:
        return None
    from kazma_core.security.platform_rbac import valid_tenant_id

    tenant = str(value).strip()
    if not valid_tenant_id(tenant):
        logger.warning("[oidc] ignoring malformed tenant claim %r", tenant[:64])
        return None
    return tenant


async def fetch_discovery(issuer: str) -> dict[str, Any]:
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15.0, verify=shared_ssl_context()) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


#: Every sign-in in progress, one settings key (FA-133): ``{state id:
#: {"verifier", "exp"}}``, changed only by ``atomic_update``. The state was one
#: global key compared with ``!=``: a second sign-in replaced the first, and a
#: state that leaked to another browser signed that browser in.
_PENDING_KEY = "auth.oidc.pending"
_PENDING_TTL_S = 600
#: Sign-ins kept at once; past it the one closest to expiry goes first.
_PENDING_MAX = 32
#: Where the browser-binding cookie is sent: the callback only.
BINDING_COOKIE_PATH = "/api/auth/oidc"


def _state_id(state: str) -> str:
    """A sign-in's settings id: the state's hash, so the state itself is
    stored nowhere but in the browser that started it."""
    return hashlib.sha256(state.encode("utf-8")).hexdigest()[:32]


def binding_cookie_name(state: str) -> str:
    """The cookie that binds a sign-in to the browser that started it.

    One per sign-in (named by its state), so two sign-ins started in one
    browser both finish. Its value is the state; the callback refuses a state
    the browser does not hold (:func:`exchange_code`).
    """
    return f"kazma-oidc-{_state_id(state)[:16]}"


def _live_pending(curr: Any, now: float) -> dict[str, dict[str, Any]]:
    """The pending sign-ins in *curr* that have not expired."""
    if not isinstance(curr, dict):
        return {}
    live: dict[str, dict[str, Any]] = {}
    for sid, entry in curr.items():
        try:
            if isinstance(entry, dict) and float(entry.get("exp") or 0) > now:
                live[str(sid)] = dict(entry)
        except (TypeError, ValueError):
            continue
    return live


async def build_authorize_url() -> dict[str, str]:
    """Build the authorize URL and record the sign-in as pending.

    Returns ``url``, ``state``, ``redirect_uri`` and ``binding_cookie`` (the
    name of the cookie the start route sets to ``state``).
    """
    cfg = OidcConfig()
    if not cfg.issuer or not cfg.client_id:
        raise RuntimeError("OIDC not configured")
    if not cfg.redirect_uri:
        public = (os.environ.get("KAZMA_PUBLIC_URL") or "").strip().rstrip("/")
        if public:
            cfg.redirect_uri = f"{public}/api/auth/oidc/callback"
        else:
            raise RuntimeError("Set KAZMA_OIDC_REDIRECT_URI or KAZMA_PUBLIC_URL")

    disc = await fetch_discovery(cfg.issuer)
    auth_ep = disc.get("authorization_endpoint")
    if not auth_ep:
        raise RuntimeError("OIDC discovery missing authorization_endpoint")

    state = secrets.token_urlsafe(24)
    # Binds the id_token to this sign-in: a token minted for another one (a
    # replayed or injected id_token) carries another nonce.
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = make_pkce()
    from kazma_core.config_store import get_config_store

    def _add(curr: Any) -> dict[str, dict[str, Any]]:
        now = time.time()
        pending = _live_pending(curr, now)
        while len(pending) >= _PENDING_MAX:
            pending.pop(min(pending, key=lambda k: float(pending[k].get("exp") or 0)))
        pending[_state_id(state)] = {
            "verifier": verifier, "nonce": nonce, "exp": now + _PENDING_TTL_S,
        }
        return pending

    # Off the loop: a settings write is a database round trip.
    await asyncio.to_thread(get_config_store().atomic_update, _PENDING_KEY, _add, "auth")

    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": cfg.scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {
        "url": f"{auth_ep}?{urlencode(params)}",
        "state": state,
        "redirect_uri": cfg.redirect_uri,
        "binding_cookie": binding_cookie_name(state),
    }


async def exchange_code(code: str, state: str, *, bound_state: str) -> dict[str, Any]:
    """Exchange authorization code for tokens; return claims + role.

    ``bound_state`` is the browser's binding cookie (:func:`binding_cookie_name`):
    a state another browser started is refused, compared in constant time.
    The pending sign-in is taken out of the store before anything else, in
    one compare-and-set, so a state is good for one callback whatever
    happens next -- a second callback with it, at the same moment or later,
    finds nothing (FA-133).
    """
    cfg = OidcConfig()
    from kazma_core.config_store import get_config_store

    if not state or not bound_state or not hmac.compare_digest(
        bound_state.encode("utf-8"), state.encode("utf-8")
    ):
        raise PermissionError("OIDC sign-in was not started in this browser")

    taken: dict[str, Any] = {}

    def _take(curr: Any) -> dict[str, dict[str, Any]]:
        now = time.time()
        pending = curr if isinstance(curr, dict) else {}
        entry = pending.get(_state_id(state))
        taken.clear()
        if isinstance(entry, dict):
            taken.update(entry)
        rest = _live_pending(pending, now)
        rest.pop(_state_id(state), None)
        return rest

    # A settings read-modify-write is a database round trip: off the loop.
    await asyncio.to_thread(get_config_store().atomic_update, _PENDING_KEY, _take, "auth")
    if not taken:
        raise PermissionError("Invalid OIDC state")
    try:
        if time.time() > float(taken.get("exp") or 0):
            raise PermissionError("OIDC state expired")
    except (TypeError, ValueError):
        raise PermissionError("OIDC state expired") from None
    verifier = str(taken.get("verifier") or "")
    nonce = str(taken.get("nonce") or "")

    disc = await fetch_discovery(cfg.issuer)
    token_ep = disc.get("token_endpoint")
    if not token_ep:
        raise RuntimeError("OIDC discovery missing token_endpoint")

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri
        or f"{(os.environ.get('KAZMA_PUBLIC_URL') or '').rstrip('/')}/api/auth/oidc/callback",
        "client_id": cfg.client_id,
        "code_verifier": verifier,
    }
    if cfg.client_secret:
        data["client_secret"] = cfg.client_secret

    async with httpx.AsyncClient(timeout=20.0, verify=shared_ssl_context()) as client:
        resp = await client.post(token_ep, data=data)
        if resp.status_code >= 400:
            raise RuntimeError(f"OIDC token exchange failed: {resp.status_code} {resp.text[:200]}")
        tokens = resp.json()

    # id_token MUST verify (JWKS or HS* client secret). Never decode
    # unverified. Userinfo is only used when the IdP omitted id_token.
    claims: dict[str, Any]
    id_token = tokens.get("id_token")
    if id_token:
        claims = await _verify_id_token(str(id_token), disc, cfg)
        # A sign-in started before the nonce existed has none recorded.
        if nonce and not hmac.compare_digest(
            str(claims.get("nonce") or "").encode("utf-8"), nonce.encode("utf-8")
        ):
            raise PermissionError("OIDC id_token nonce does not match this sign-in")
    elif tokens.get("access_token") and disc.get("userinfo_endpoint"):
        claims = await _userinfo_claims(
            str(tokens["access_token"]),
            str(disc["userinfo_endpoint"]),
        )
    else:
        raise PermissionError("OIDC token response had no verifiable identity")

    if not (claims.get("sub") or claims.get("email")):
        raise PermissionError("OIDC claims missing subject")
    # An unverified email is refused wherever it would count: as the name or
    # id below, or matched against an allowlist.
    email_counts = bool(
        cfg.allowed_emails or cfg.allowed_domains
        or not claims.get("sub") or not claims.get("preferred_username")
    )
    email = _email_of(claims) if email_counts else str(claims.get("email") or "").strip()
    if not _sign_in_allowed(claims, cfg):
        logger.warning("[oidc] refused a sign-in the allowlists do not name")
        raise PermissionError("This account may not sign in to Kazma")

    role = oidc_role_from_claims(claims, cfg)
    return {
        "claims": claims,
        "role": role,
        "username": str(
            claims.get("preferred_username")
            or email
            or claims.get("sub")
            or "oidc-user"
        ),
        "user_id": str(claims.get("sub") or email or "oidc"),
        "tenant_id": oidc_tenant_from_claims(claims, cfg),
        "tokens": {"token_type": tokens.get("token_type"), "expires_in": tokens.get("expires_in")},
    }


#: Common IdP role names, as Kazma's roles.
_ROLE_NAMES: dict[str, str] = {
    "admin": "admin", "owner": "admin", "superadmin": "admin", "administrator": "admin",
    "operator": "operator", "user": "operator", "member": "operator", "write": "operator",
    "viewer": "viewer", "read": "viewer", "readonly": "viewer", "guest": "viewer",
}
_RANK = {"viewer": 1, "operator": 2, "admin": 3}


def oidc_role_from_claims(claims: dict[str, Any], cfg: OidcConfig | None = None) -> str:
    """The highest Kazma role any value of the role claim maps to.

    A list-valued claim is read element by element (only its first element
    was read: ``["employees", "kazma-admin"]`` gave the default role and
    ``["admin", ...]`` admin whatever came after; security audit 2026-11-10).
    No value that maps: the default role.
    """
    cfg = cfg or OidcConfig()
    raw = claims.get(cfg.role_claim) or claims.get("roles") or claims.get("groups")
    items = raw if isinstance(raw, list) else [raw]
    mapped = [_ROLE_NAMES[str(v).strip().lower()] for v in items
              if v is not None and str(v).strip().lower() in _ROLE_NAMES]
    if mapped:
        return max(mapped, key=_RANK.__getitem__)
    default = str(cfg.default_role or "").lower()
    return default if default in _RANK else "operator"


_ASYM_ALGS = ("RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512")
_HS_ALGS = ("HS256", "HS384", "HS512")


async def _userinfo_claims(access_token: str, endpoint: str) -> dict[str, Any]:
    """OIDC UserInfo fallback — only when the token response has no id_token."""
    async with httpx.AsyncClient(timeout=15.0, verify=shared_ssl_context()) as client:
        ui = await client.get(
            endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if ui.status_code != 200:
        raise PermissionError(f"OIDC userinfo failed: {ui.status_code}")
    try:
        data = ui.json()
    except Exception as exc:
        raise PermissionError("OIDC userinfo was not JSON") from exc
    if not isinstance(data, dict):
        raise PermissionError("OIDC userinfo was not an object")
    return data


async def _verify_id_token(
    token: str, disc: dict[str, Any], cfg: OidcConfig
) -> dict[str, Any]:
    """Verify id_token signature + iss/aud/exp. Raises PermissionError on any failure.

    Fail-closed: a JWKS miss, ``alg: none``, algorithm confusion (HS* with a
    public key), or PyJWT error never returns unverified claims.
    """
    try:
        import jwt as _jwt
        from jwt import PyJWKClient
        from jwt.exceptions import PyJWTError
    except ImportError as exc:
        raise PermissionError("OIDC verification requires PyJWT") from exc

    try:
        header = _jwt.get_unverified_header(token)
    except Exception as exc:
        raise PermissionError("OIDC id_token is not a JWT") from exc

    alg = str(header.get("alg") or "")
    if alg.lower() == "none" or alg not in (*_ASYM_ALGS, *_HS_ALGS):
        raise PermissionError("OIDC id_token algorithm rejected")

    issuer = str(disc.get("issuer") or cfg.issuer)
    decode_kw: dict[str, Any] = {
        "audience": cfg.client_id,
        "issuer": issuer,
        "leeway": 60,
        "options": {"require": ["sub", "iss", "aud", "exp"]},
    }

    try:
        if alg in _HS_ALGS:
            if not cfg.client_secret:
                raise PermissionError("OIDC HS* id_token requires client secret")
            claims = _jwt.decode(
                token,
                cfg.client_secret,
                algorithms=list(_HS_ALGS),
                **decode_kw,
            )
        else:
            jwks_uri = disc.get("jwks_uri")
            if not jwks_uri:
                raise PermissionError("OIDC discovery missing jwks_uri")
            jwks_client = PyJWKClient(str(jwks_uri))
            signing_key = jwks_client.get_signing_key_from_jwt(token)
            claims = _jwt.decode(
                token,
                signing_key.key,
                algorithms=list(_ASYM_ALGS),
                **decode_kw,
            )
    except PermissionError:
        raise
    except PyJWTError as exc:
        logger.warning("[oidc] id_token verify failed: %s", exc)
        raise PermissionError("OIDC id_token verification failed") from exc
    except Exception as exc:
        logger.warning("[oidc] id_token verify failed: %s", exc)
        raise PermissionError("OIDC id_token verification failed") from exc

    if not isinstance(claims, dict):
        raise PermissionError("OIDC id_token claims missing")
    return dict(claims)
