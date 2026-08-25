"""OIDC fail-closed identity (industry stack part 5).

The login path must never mint a session from an unverified JWT. A JWKS miss,
``alg: none``, or signature failure is PermissionError — not a fallback to
``jwt.decode(..., verify_signature=False)``.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kazma_core.security import oidc as oidc_mod
from kazma_core.security.oidc import (
    OidcConfig,
    _verify_id_token,
    exchange_code,
    oidc_role_from_claims,
)

ISSUER = "https://idp.example"
CLIENT_ID = "kazma-web"
CLIENT_SECRET = "hs-client-secret-value-32bytes!!"
REDIRECT = "https://app.example/api/auth/oidc/callback"

_OIDC_PY = (
    Path(__file__).resolve().parent.parent
    / "kazma-core"
    / "kazma_core"
    / "security"
    / "oidc.py"
)


@pytest.fixture(scope="module")
def rsa_keys():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


@pytest.fixture
def oidc_env(monkeypatch):
    monkeypatch.setenv("KAZMA_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("KAZMA_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("KAZMA_OIDC_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("KAZMA_OIDC_REDIRECT_URI", REDIRECT)
    return OidcConfig()


def _claims(**extra: Any) -> dict[str, Any]:
    now = int(time.time())
    body = {
        "sub": "user-1",
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "exp": now + 3600,
        "iat": now,
        "email": "op@example.com",
        "preferred_username": "op",
        "role": "operator",
    }
    body.update(extra)
    return body


def _rs_token(priv, claims: dict[str, Any] | None = None, *, alg: str = "RS256") -> str:
    return jwt.encode(claims or _claims(), priv, algorithm=alg, headers={"kid": "k1"})


def _hs_token(secret: str, claims: dict[str, Any] | None = None, *, alg: str = "HS256") -> str:
    return jwt.encode(claims or _claims(), secret, algorithm=alg)


def _none_token(claims: dict[str, Any] | None = None) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims or _claims()).encode()).rstrip(b"=").decode()
    return f"{header}.{body}."


def _unsigned_forged(claims: dict[str, Any] | None = None) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "k1"}).encode()).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims or _claims()).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"forged").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def _patch_jwks(monkeypatch, public_key) -> None:
    class _Key:
        key = public_key

    class _Client:
        def __init__(self, uri, *a, **k):
            self.uri = uri

        def get_signing_key_from_jwt(self, token):
            return _Key()

    monkeypatch.setattr("jwt.PyJWKClient", _Client)


def _disc(**extra: Any) -> dict[str, Any]:
    body = {
        "issuer": ISSUER,
        "jwks_uri": "https://idp.example/jwks",
        "token_endpoint": "https://idp.example/token",
        "userinfo_endpoint": "https://idp.example/userinfo",
        "authorization_endpoint": "https://idp.example/auth",
    }
    body.update(extra)
    return body


class _Store:
    def __init__(self, state: str = "st") -> None:
        self._d: dict[str, Any] = {
            "auth.oidc.state": state,
            "auth.oidc.state_exp": time.time() + 600,
            "auth.oidc.pkce_verifier": "verifier",
        }

    def get(self, k: str) -> Any:
        return self._d.get(k)

    def delete(self, k: str) -> None:
        self._d.pop(k, None)


class _Resp:
    def __init__(self, status_code: int, payload: Any, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if not isinstance(payload, str) else payload)

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ── Source contract ──────────────────────────────────────────────────────────


def test_oidc_source_never_decodes_unverified() -> None:
    src = _OIDC_PY.read_text(encoding="utf-8")
    assert "verify_signature" not in src
    assert "_decode_id_token_unverified" not in src
    assert "cfg) or claims" not in src


# ── Verify ───────────────────────────────────────────────────────────────────


async def test_verify_rs256_ok(rsa_keys, oidc_env, monkeypatch) -> None:
    priv, pub = rsa_keys
    _patch_jwks(monkeypatch, pub)
    claims = await _verify_id_token(_rs_token(priv), _disc(), oidc_env)
    assert claims["sub"] == "user-1"
    assert claims["email"] == "op@example.com"


async def test_verify_forged_rs256_rejected(rsa_keys, oidc_env, monkeypatch) -> None:
    _priv, pub = rsa_keys
    _patch_jwks(monkeypatch, pub)
    with pytest.raises(PermissionError, match="verification failed"):
        await _verify_id_token(_unsigned_forged(), _disc(), oidc_env)


async def test_verify_wrong_jwks_key_rejected(rsa_keys, oidc_env, monkeypatch) -> None:
    priv, _pub = rsa_keys
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    _patch_jwks(monkeypatch, other.public_key())
    with pytest.raises(PermissionError, match="verification failed"):
        await _verify_id_token(_rs_token(priv), _disc(), oidc_env)


async def test_verify_missing_jwks_uri_rejected(rsa_keys, oidc_env) -> None:
    priv, _pub = rsa_keys
    disc = _disc()
    disc.pop("jwks_uri")
    with pytest.raises(PermissionError, match="jwks_uri"):
        await _verify_id_token(_rs_token(priv), disc, oidc_env)


async def test_verify_alg_none_rejected(oidc_env) -> None:
    with pytest.raises(PermissionError, match="algorithm rejected"):
        await _verify_id_token(_none_token(), _disc(), oidc_env)


async def test_verify_hs256_ok(oidc_env) -> None:
    claims = await _verify_id_token(_hs_token(CLIENT_SECRET), _disc(), oidc_env)
    assert claims["sub"] == "user-1"


async def test_verify_hs256_wrong_secret_rejected(oidc_env) -> None:
    with pytest.raises(PermissionError, match="verification failed"):
        await _verify_id_token(_hs_token("not-the-secret-value-32-bytes!!!!"), _disc(), oidc_env)


async def test_verify_hs256_requires_client_secret(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("KAZMA_OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("KAZMA_OIDC_CLIENT_SECRET", "")
    monkeypatch.setenv("KAZMA_OIDC_REDIRECT_URI", REDIRECT)
    cfg = OidcConfig()
    with pytest.raises(PermissionError, match="client secret"):
        await _verify_id_token(_hs_token(CLIENT_SECRET), _disc(), cfg)


async def test_verify_alg_confusion_rejected(rsa_keys, oidc_env) -> None:
    """Header alg=HS256 HMAC'd with the RSA public key must not authenticate.

    Modern PyJWT refuses to *encode* HMAC with an asymmetric key; a confused
    attacker would mint the JWS by hand. We do the same and require verify
    to fail because HS* uses the client secret, never the JWKS public key.
    """
    import hashlib
    import hmac

    from cryptography.hazmat.primitives import serialization

    _priv, pub = rsa_keys
    pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(_claims()).encode()).rstrip(b"=").decode()
    signing_input = f"{header}.{body}".encode()
    sig = base64.urlsafe_b64encode(
        hmac.new(pem, signing_input, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    token = f"{header}.{body}.{sig}"
    with pytest.raises(PermissionError):
        await _verify_id_token(token, _disc(), oidc_env)


async def test_verify_wrong_audience_rejected(rsa_keys, oidc_env, monkeypatch) -> None:
    priv, pub = rsa_keys
    _patch_jwks(monkeypatch, pub)
    token = _rs_token(priv, _claims(aud="someone-else"))
    with pytest.raises(PermissionError, match="verification failed"):
        await _verify_id_token(token, _disc(), oidc_env)


async def test_verify_expired_rejected(rsa_keys, oidc_env, monkeypatch) -> None:
    priv, pub = rsa_keys
    _patch_jwks(monkeypatch, pub)
    token = _rs_token(priv, _claims(exp=int(time.time()) - 120))
    with pytest.raises(PermissionError, match="verification failed"):
        await _verify_id_token(token, _disc(), oidc_env)


# ── exchange_code ────────────────────────────────────────────────────────────


def _install_store(monkeypatch, store: _Store | None = None) -> _Store:
    store = store or _Store()
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    return store


async def test_exchange_code_verified_id_token(rsa_keys, oidc_env, monkeypatch) -> None:
    priv, pub = rsa_keys
    _patch_jwks(monkeypatch, pub)
    _install_store(monkeypatch)
    token = _rs_token(priv)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return _Resp(200, {"id_token": token, "token_type": "Bearer", "expires_in": 3600})

        async def get(self, url, headers=None):
            raise AssertionError("userinfo must not run when id_token verifies")

    monkeypatch.setattr(oidc_mod, "fetch_discovery", AsyncMock(return_value=_disc()))
    monkeypatch.setattr(oidc_mod.httpx, "AsyncClient", _Client)

    result = await exchange_code("code", "st")
    assert result["user_id"] == "user-1"
    assert result["username"] == "op"
    assert result["role"] == "operator"
    assert result["claims"]["sub"] == "user-1"


async def test_exchange_code_rejects_unverified_id_token(
    rsa_keys, oidc_env, monkeypatch
) -> None:
    _priv, pub = rsa_keys
    _patch_jwks(monkeypatch, pub)
    _install_store(monkeypatch)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return _Resp(
                200,
                {
                    "id_token": _unsigned_forged(_claims(role="admin", sub="attacker")),
                    "access_token": "atk",
                    "token_type": "Bearer",
                },
            )

        async def get(self, url, headers=None):
            raise AssertionError("must not fall through to userinfo after a bad id_token")

    monkeypatch.setattr(oidc_mod, "fetch_discovery", AsyncMock(return_value=_disc()))
    monkeypatch.setattr(oidc_mod.httpx, "AsyncClient", _Client)

    with pytest.raises(PermissionError, match="verification failed"):
        await exchange_code("code", "st")


async def test_exchange_code_userinfo_only_without_id_token(oidc_env, monkeypatch) -> None:
    _install_store(monkeypatch)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return _Resp(200, {"access_token": "atk", "token_type": "Bearer"})

        async def get(self, url, headers=None):
            assert "userinfo" in url
            assert headers and headers.get("Authorization") == "Bearer atk"
            return _Resp(
                200,
                {"sub": "ui-9", "email": "ui@example.com", "role": "admin"},
            )

    monkeypatch.setattr(oidc_mod, "fetch_discovery", AsyncMock(return_value=_disc()))
    monkeypatch.setattr(oidc_mod.httpx, "AsyncClient", _Client)

    result = await exchange_code("code", "st")
    assert result["user_id"] == "ui-9"
    assert result["role"] == "admin"


async def test_exchange_code_userinfo_http_error(oidc_env, monkeypatch) -> None:
    _install_store(monkeypatch)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return _Resp(200, {"access_token": "atk"})

        async def get(self, url, headers=None):
            return _Resp(401, {"error": "invalid_token"}, text="invalid_token")

    monkeypatch.setattr(oidc_mod, "fetch_discovery", AsyncMock(return_value=_disc()))
    monkeypatch.setattr(oidc_mod.httpx, "AsyncClient", _Client)

    with pytest.raises(PermissionError, match="userinfo failed"):
        await exchange_code("code", "st")


async def test_exchange_code_no_identity(oidc_env, monkeypatch) -> None:
    _install_store(monkeypatch)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None):
            return _Resp(200, {"token_type": "Bearer"})

        async def get(self, url, headers=None):
            raise AssertionError("no userinfo without access_token")

    monkeypatch.setattr(oidc_mod, "fetch_discovery", AsyncMock(return_value=_disc()))
    monkeypatch.setattr(oidc_mod.httpx, "AsyncClient", _Client)

    with pytest.raises(PermissionError, match="no verifiable identity"):
        await exchange_code("code", "st")


async def test_exchange_code_invalid_state(oidc_env, monkeypatch) -> None:
    _install_store(monkeypatch)
    with pytest.raises(PermissionError, match="Invalid OIDC state"):
        await exchange_code("code", "nope")


def test_oidc_role_from_claims_maps_common_names(oidc_env) -> None:
    assert oidc_role_from_claims({"role": "administrator"}, oidc_env) == "admin"
    assert oidc_role_from_claims({"roles": ["member"]}, oidc_env) == "operator"
    assert oidc_role_from_claims({"groups": ["readonly"]}, oidc_env) == "viewer"
    assert oidc_role_from_claims({}, oidc_env) == "operator"


def test_oidc_source_verify_is_not_optional() -> None:
    """Guard the exact previous bug: verified claims OR unverified claims."""
    src = _OIDC_PY.read_text(encoding="utf-8")
    assert "await _verify_id_token(str(id_token), disc, cfg)" in src
    assert "or claims" not in src.split("if id_token:", 1)[-1].split("elif tokens.get", 1)[0]
