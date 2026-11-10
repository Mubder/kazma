"""OIDC signs in only the accounts the owner names, with their real role.

Security audit 2026-11-10. Any account the configured issuer authenticated
became a Kazma operator: no allowlist, an email trusted without
``email_verified``, only the first element of a list role claim read, no
nonce, and an unauthenticated start route that wrote a pending sign-in per
call (32 kept) with no rate limit. Now:

- ``KAZMA_OIDC_ALLOWED_EMAILS`` / ``_DOMAINS`` / ``_GROUPS``: with any set, an
  account none names is refused; with none set, unchanged, and a WARNING
  says every account of the issuer may sign in;
- an email the IdP says is unverified is refused wherever it counts;
- every element of a list role claim is read, the highest role wins;
- a nonce is sent and the id_token must carry it;
- ``/api/auth/oidc/start`` is rate-limited per client address.

The default role is unchanged (``operator``).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.security import oidc as oidc_mod
from kazma_core.security.oidc import OidcConfig, oidc_role_from_claims, oidc_sign_in_allowed

ISSUER = "https://idp.example"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("KAZMA_OIDC_ALLOWED_EMAILS", "KAZMA_OIDC_ALLOWED_DOMAINS",
                "KAZMA_OIDC_ALLOWED_GROUPS", "KAZMA_OIDC_ROLE_CLAIM", "KAZMA_OIDC_DEFAULT_ROLE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("KAZMA_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("KAZMA_OIDC_CLIENT_ID", "kazma")
    monkeypatch.setenv("KAZMA_OIDC_REDIRECT_URI", "https://app.example/api/auth/oidc/callback")
    monkeypatch.setattr(oidc_mod, "_open_issuer_warned", [False])


# ── who ─────────────────────────────────────────────────────────────────


def test_no_allowlist_admits_every_account_and_says_so(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="kazma_core.security.oidc"):
        cfg = OidcConfig()
        OidcConfig()
    assert oidc_sign_in_allowed({"sub": "x", "email": "anyone@else.example"}, cfg)
    said = [r for r in caplog.records if "Every account" in r.getMessage()]
    assert len(said) == 1, "once, at config load"


def test_an_allowlist_names_who_may(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_OIDC_ALLOWED_EMAILS", "Owner@Example.com")
    monkeypatch.setenv("KAZMA_OIDC_ALLOWED_DOMAINS", "@team.example")
    monkeypatch.setenv("KAZMA_OIDC_ALLOWED_GROUPS", "kazma-users")
    cfg = OidcConfig()
    assert oidc_sign_in_allowed({"sub": "1", "email": "owner@example.com"}, cfg)
    assert oidc_sign_in_allowed({"sub": "2", "email": "dev@team.example"}, cfg)
    assert oidc_sign_in_allowed({"sub": "3", "groups": ["staff", "Kazma-Users"]}, cfg)
    assert not oidc_sign_in_allowed({"sub": "4", "email": "stranger@gmail.com"}, cfg)
    assert not oidc_sign_in_allowed({"sub": "5", "email": "x@team.example.evil.com"}, cfg)


def test_an_unverified_email_never_matches(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_OIDC_ALLOWED_EMAILS", "owner@example.com")
    cfg = OidcConfig()
    with pytest.raises(PermissionError):
        oidc_sign_in_allowed({"sub": "1", "email": "owner@example.com", "email_verified": False}, cfg)
    assert oidc_sign_in_allowed({"sub": "1", "email": "owner@example.com", "email_verified": True}, cfg)


# ── role ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("claim,role", [
    (["employees", "kazma-admin", "admin"], "admin"),
    (["viewer", "admin"], "admin"),
    (["read", "member"], "operator"),
    (["employees"], "operator"),          # nothing maps: the default
    ("guest", "viewer"),
    (None, "operator"),
])
def test_the_highest_mapped_role_wins(claim: Any, role: str) -> None:
    claims = {"sub": "x"} if claim is None else {"sub": "x", "groups": claim}
    assert oidc_role_from_claims(claims, OidcConfig()) == role


def test_control_the_first_element_rule() -> None:
    """Negative control: the old reader took element 0 only."""
    claims = {"groups": ["viewer", "admin"]}
    first_only = str(claims["groups"][0]).lower()
    assert first_only == "viewer" and oidc_role_from_claims(claims, OidcConfig()) == "admin"


# ── the exchange ────────────────────────────────────────────────────────


class _Store:
    def __init__(self) -> None:
        self.d: dict[str, Any] = {}

    def get(self, k: str, default: Any = None) -> Any:
        return self.d.get(k, default)

    def atomic_update(self, k: str, fn: Any, category: str = "general") -> Any:
        self.d[k] = fn(self.d.get(k))
        return self.d[k]


@pytest.fixture()
def idp(monkeypatch):
    """A local identity provider: discovery, a token endpoint, and the
    verified claims the id_token carries (set per test)."""
    store = _Store()
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    state: dict[str, Any] = {"claims": {}}

    async def _discovery(_issuer: str) -> dict[str, str]:
        return {"authorization_endpoint": f"{ISSUER}/auth", "token_endpoint": f"{ISSUER}/token",
                "issuer": ISSUER, "jwks_uri": f"{ISSUER}/jwks"}

    async def _verify(token: str, disc: dict, cfg: OidcConfig) -> dict:
        return dict(state["claims"])

    def _token(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id_token": "header.payload.sig", "token_type": "Bearer"})

    real_client = httpx.AsyncClient

    def _client(*args, **kwargs):
        kwargs.pop("verify", None)
        return real_client(*args, transport=httpx.MockTransport(_token), **kwargs)

    monkeypatch.setattr(oidc_mod, "fetch_discovery", _discovery)
    monkeypatch.setattr(oidc_mod, "_verify_id_token", _verify)
    monkeypatch.setattr(oidc_mod.httpx, "AsyncClient", _client)
    return state


async def _sign_in(idp: dict, claims: dict, *, nonce: str | None = None) -> dict:
    info = await oidc_mod.build_authorize_url()
    sent = parse_qs(urlsplit(info["url"]).query)["nonce"][0]
    idp["claims"] = {**claims, "nonce": sent if nonce is None else nonce}
    return await oidc_mod.exchange_code("code", info["state"], bound_state=info["state"])


async def test_the_nonce_is_sent_and_checked(idp) -> None:
    result = await _sign_in(idp, {"sub": "u1", "preferred_username": "u1"})
    assert result["role"] == "operator"
    with pytest.raises(PermissionError, match="nonce"):
        await _sign_in(idp, {"sub": "u1", "preferred_username": "u1"}, nonce="another-sign-in")


async def test_an_account_the_allowlist_does_not_name_is_refused(idp, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_OIDC_ALLOWED_DOMAINS", "team.example")
    ok = await _sign_in(idp, {"sub": "a", "email": "me@team.example", "email_verified": True})
    assert ok["user_id"] == "a"
    with pytest.raises(PermissionError, match="may not sign in"):
        await _sign_in(idp, {"sub": "b", "email": "me@elsewhere.example"})


async def test_an_unverified_email_identity_is_refused(idp) -> None:
    with pytest.raises(PermissionError, match="not verified"):
        await _sign_in(idp, {"email": "owner@example.com", "email_verified": False})
    # A subject and a username: the email is not the identity, nothing refused.
    ok = await _sign_in(idp, {"sub": "s", "preferred_username": "s",
                              "email": "x@example.com", "email_verified": False})
    assert ok["username"] == "s"


async def test_control_without_the_allowlist_the_stranger_got_in(idp, monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_OIDC_ALLOWED_DOMAINS", "team.example")
    monkeypatch.setattr(oidc_mod, "oidc_sign_in_allowed", lambda claims, cfg: True)
    result = await _sign_in(idp, {"sub": "b", "email": "me@elsewhere.example"})
    assert result["role"] == "operator"


# ── the start route ─────────────────────────────────────────────────────


def _start_app(monkeypatch) -> TestClient:
    import kazma_ui.rate_limit as rl
    from kazma_ui.routes_direct import auth as auth_routes

    monkeypatch.setattr(rl, "_enabled", lambda: True)
    monkeypatch.setattr(rl, "_per_minute", lambda bucket, default: default)
    rl._windows.clear()

    async def _authorize() -> dict[str, str]:
        return {"url": f"{ISSUER}/auth?x=1", "state": "s" * 32, "binding_cookie": "kazma-oidc-x",
                "redirect_uri": "https://app.example/cb"}

    monkeypatch.setattr(oidc_mod, "build_authorize_url", _authorize)
    builder = SimpleNamespace(app=FastAPI())
    auth_routes.register_auth_routes(builder)
    return TestClient(builder.app, follow_redirects=False)


def test_the_start_route_is_limited_per_address(monkeypatch) -> None:
    client = _start_app(monkeypatch)
    codes = [client.get("/api/auth/oidc/start", cookies={"kazma-session": f"forged-{i}"}).status_code
             for i in range(11)]
    assert codes[:10] == [302] * 10
    assert codes[10] == 429, "a new cookie per call does not buy a new window"


def test_control_keyed_by_cookie_a_loop_was_never_limited(monkeypatch) -> None:
    """Negative control: the default key (cookie@address) on the same loop."""
    import kazma_ui.rate_limit as rl

    monkeypatch.setattr(rl, "_enabled", lambda: True)
    monkeypatch.setattr(rl, "_per_minute", lambda bucket, default: default)
    rl._windows.clear()
    app = FastAPI()
    from fastapi import Depends

    @app.get("/probe", dependencies=[Depends(rl.rate_limit("probe", 10))])
    def probe() -> dict:
        return {}

    client = TestClient(app)
    codes = {client.get("/probe", cookies={"kazma-session": f"forged-{i}"}).status_code
             for i in range(20)}
    assert codes == {200}
