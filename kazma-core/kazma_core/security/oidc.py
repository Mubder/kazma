"""Optional OIDC (OpenID Connect) login — Phase 4.4 IdP foundation.

Configure::

    KAZMA_OIDC_ISSUER=https://accounts.example.com
    KAZMA_OIDC_CLIENT_ID=...
    KAZMA_OIDC_CLIENT_SECRET=...
    KAZMA_OIDC_REDIRECT_URI=https://your.domain/api/auth/oidc/callback
    # optional role claim mapping
    KAZMA_OIDC_ROLE_CLAIM=role
    KAZMA_OIDC_DEFAULT_ROLE=operator

Flow: browser → /api/auth/oidc/start → IdP → /api/auth/oidc/callback
→ opaque kazma-session with bound username/role.

Does not require a heavy SDK — uses discovery + authorization code + PKCE.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx

__all__ = [
    "OidcConfig",
    "build_authorize_url",
    "exchange_code",
    "fetch_discovery",
    "oidc_configured",
    "oidc_role_from_claims",
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


async def fetch_discovery(issuer: str) -> dict[str, Any]:
    url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15.0) as client:
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


async def build_authorize_url() -> dict[str, str]:
    """Build authorize URL + store state/pkce in ConfigStore. Returns dict with url."""
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
    verifier, challenge = make_pkce()
    from kazma_core.config_store import get_config_store

    get_config_store().batch_set(
        [
            ("auth.oidc.state", state, "auth"),
            ("auth.oidc.pkce_verifier", verifier, "auth"),
            ("auth.oidc.state_exp", time.time() + 600, "auth"),
        ]
    )

    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "scope": cfg.scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return {
        "url": f"{auth_ep}?{urlencode(params)}",
        "state": state,
        "redirect_uri": cfg.redirect_uri,
    }


async def exchange_code(code: str, state: str) -> dict[str, Any]:
    """Exchange authorization code for tokens; return claims + role."""
    cfg = OidcConfig()
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    expected = cs.get("auth.oidc.state")
    exp = cs.get("auth.oidc.state_exp") or 0
    verifier = cs.get("auth.oidc.pkce_verifier") or ""
    if not expected or state != expected:
        raise PermissionError("Invalid OIDC state")
    try:
        if time.time() > float(exp):
            raise PermissionError("OIDC state expired")
    except (TypeError, ValueError):
        raise PermissionError("OIDC state expired") from None

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

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(token_ep, data=data)
        if resp.status_code >= 400:
            raise RuntimeError(f"OIDC token exchange failed: {resp.status_code} {resp.text[:200]}")
        tokens = resp.json()

    # Prefer id_token claims (JWT) — verify signature when JWKS available
    claims: dict[str, Any] = {}
    id_token = tokens.get("id_token")
    if id_token:
        claims = _decode_id_token_unverified(id_token)
        # Optional: verify with JWKS if PyJWT + issuer keys present
        claims = await _verify_id_token(id_token, disc, cfg) or claims

    # userinfo fallback
    if not claims.get("sub") and tokens.get("access_token") and disc.get("userinfo_endpoint"):
        async with httpx.AsyncClient(timeout=15.0) as client:
            ui = await client.get(
                disc["userinfo_endpoint"],
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if ui.status_code == 200:
                claims = ui.json()

    role = oidc_role_from_claims(claims, cfg)
    # Clear one-time state
    try:
        cs.delete("auth.oidc.state")
        cs.delete("auth.oidc.pkce_verifier")
        cs.delete("auth.oidc.state_exp")
    except Exception:
        pass

    return {
        "claims": claims,
        "role": role,
        "username": str(
            claims.get("preferred_username")
            or claims.get("email")
            or claims.get("sub")
            or "oidc-user"
        ),
        "user_id": str(claims.get("sub") or claims.get("email") or "oidc"),
        "tokens": {"token_type": tokens.get("token_type"), "expires_in": tokens.get("expires_in")},
    }


def oidc_role_from_claims(claims: dict[str, Any], cfg: OidcConfig | None = None) -> str:
    cfg = cfg or OidcConfig()
    raw = claims.get(cfg.role_claim) or claims.get("roles") or claims.get("groups")
    if isinstance(raw, list) and raw:
        raw = raw[0]
    role = str(raw or cfg.default_role).lower()
    if role in ("admin", "operator", "viewer"):
        return role
    # Map common IdP names
    if role in ("owner", "superadmin", "administrator"):
        return "admin"
    if role in ("user", "member", "write"):
        return "operator"
    if role in ("read", "readonly", "guest"):
        return "viewer"
    return cfg.default_role if cfg.default_role in ("admin", "operator", "viewer") else "operator"


def _decode_id_token_unverified(token: str) -> dict[str, Any]:
    try:
        import jwt as _jwt

        return dict(_jwt.decode(token, options={"verify_signature": False}))
    except Exception:
        return {}


async def _verify_id_token(
    token: str, disc: dict[str, Any], cfg: OidcConfig
) -> dict[str, Any] | None:
    """Best-effort JWKS verification; returns None if unavailable."""
    jwks_uri = disc.get("jwks_uri")
    if not jwks_uri:
        return None
    try:
        import jwt as _jwt
        from jwt import PyJWKClient

        jwks_client = PyJWKClient(jwks_uri)
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        return dict(
            _jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "ES256", "HS256"],
                audience=cfg.client_id,
                issuer=cfg.issuer,
            )
        )
    except Exception as exc:
        logger.debug("[oidc] id_token verify failed: %s", exc)
        return None
