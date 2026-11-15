"""MCP OAuth asks only the addresses it may, pinned, with no redirect.

Security audit 2026-11-15. Every OAuth address but the MCP server's own is
chosen by that server: ``resource_metadata`` in its 401 challenge,
``registration_endpoint`` and ``token_endpoint`` in its metadata. Kazma
fetched them with plain clients following redirects and no address check:
a server could make Kazma GET or POST any internal address, a refresh
posted the refresh token to whatever ``token_endpoint`` said, and a failed
registration repeated 300 characters of the server's answer. Now (see
``mcp.oauth``): no redirect is followed; an address off the server's own
origin must be https and public and is asked through
``security.pinned_request`` (pinned to the checked address); the token and
registration endpoints must be on the issuer's origin; the owner's own MCP
server on this machine or network may be asked directly, its origin only;
an answer's text never reaches an error.

The negative controls run the old discovery and refresh on the same
metadata: they send the refresh token to the address the server named.
"""

from __future__ import annotations

import http.server
import json
import threading
from typing import Any

import httpx
import pytest
from kazma_core.mcp import oauth
from kazma_core.mcp.oauth import MCPOAuthError, discover_auth_requirements

from tests._pinned_stand_in import OTHER_PUBLIC_IP, PUBLIC_IP, Answer, install

RESOURCE = "https://mcp.example/devtools"
PRM = "/.well-known/oauth-protected-resource/devtools"
#: The issuer's metadata, on the issuer the resource names (RFC 8414): never
#: the MCP server's own host (until 2026-11-16, ``OLD_ASM`` there).
ASM = "/.well-known/oauth-authorization-server"
OLD_ASM = "/.well-known/oauth-authorization-server/devtools"


def _metadata(**overrides: Any) -> dict[str, Any]:
    meta = {
        "issuer": "https://auth.example",
        "authorization_endpoint": "https://auth.example/authorize",
        "token_endpoint": "https://auth.example/token",
        "registration_endpoint": "https://auth.example/register",
    }
    meta.update(overrides)
    return meta


def _public_server(monkeypatch, *, as_meta: dict[str, Any] | None = None, extra=None, dns=None):
    routes = {
        ("mcp.example", PRM): Answer.json({"authorization_servers": ["https://auth.example"],
                                           "scopes_supported": ["read"]}),
        ("auth.example", ASM): Answer.json(as_meta if as_meta is not None else _metadata()),
        ("auth.example", "/token"): Answer.json({"access_token": "new", "expires_in": 3600}),
        ("auth.example", "/register"): Answer.json({"client_id": "cid"}, status=201),
        **(extra or {}),
    }
    return install(monkeypatch, dns={
        "mcp.example": [PUBLIC_IP],
        "auth.example": [OTHER_PUBLIC_IP],
        "inside.example": ["10.0.0.5"],
        **(dns or {}),
    }, routes=routes)


async def test_discovery_asks_the_checked_addresses_only(monkeypatch) -> None:
    stand_in = _public_server(monkeypatch)
    found = await discover_auth_requirements(RESOURCE)
    assert found["token_endpoint"] == "https://auth.example/token"
    assert found["registration_endpoint"] == "https://auth.example/register"
    assert found["issuer"] == "https://auth.example"
    assert stand_in.connects == [(PUBLIC_IP, 443), (OTHER_PUBLIC_IP, 443)]
    assert [(r["host"], r["path"]) for r in stand_in.requests] == [("mcp.example", PRM), ("auth.example", ASM)]


async def test_a_challenge_naming_an_internal_address_is_refused_unasked(monkeypatch) -> None:
    stand_in = _public_server(monkeypatch)
    with pytest.raises(MCPOAuthError, match="not a public address"):
        await discover_auth_requirements(
            RESOURCE, www_authenticate='Bearer resource_metadata="https://inside.example/meta"')
    assert stand_in.connects == [] and stand_in.requests == []


@pytest.mark.parametrize("address", ["http://mcp.example/meta", "file:///etc/passwd", "https://127.0.0.1/meta"])
async def test_a_challenge_naming_a_plain_or_local_address_is_refused(monkeypatch, address) -> None:
    stand_in = _public_server(monkeypatch)
    with pytest.raises(MCPOAuthError):
        await discover_auth_requirements(RESOURCE, www_authenticate=f'Bearer resource_metadata="{address}"')
    assert stand_in.connects == []


async def test_a_redirect_is_not_followed(monkeypatch) -> None:
    stand_in = _public_server(monkeypatch, extra={
        ("mcp.example", PRM): Answer(302, b"", {"Location": "https://inside.example/meta"}),
    })
    with pytest.raises(MCPOAuthError, match="HTTP 302"):
        await discover_auth_requirements(RESOURCE)
    assert [r["host"] for r in stand_in.requests] == ["mcp.example"]


@pytest.mark.parametrize("key,value", [
    ("token_endpoint", "https://elsewhere.example/token"),
    ("registration_endpoint", "https://elsewhere.example/register"),
    ("token_endpoint", "https://auth.example:8443/token"),
])
async def test_an_endpoint_off_the_issuers_origin_is_refused(monkeypatch, key, value) -> None:
    _public_server(monkeypatch, as_meta=_metadata(**{key: value}), dns={"elsewhere.example": [PUBLIC_IP]})
    with pytest.raises(MCPOAuthError, match="issuer's own address"):
        await discover_auth_requirements(RESOURCE)


@pytest.mark.parametrize("meta", [
    {k: v for k, v in _metadata().items() if k != "issuer"},
    _metadata(issuer="http://auth.example", token_endpoint="http://auth.example/token",
              registration_endpoint="http://auth.example/register"),
    _metadata(authorization_endpoint="javascript:alert(1)"),
])
async def test_metadata_without_a_usable_issuer_or_endpoint_is_refused(monkeypatch, meta) -> None:
    _public_server(monkeypatch, as_meta=meta)
    with pytest.raises(MCPOAuthError):
        await discover_auth_requirements(RESOURCE)


async def test_the_refresh_token_goes_only_to_the_checked_token_endpoint(monkeypatch) -> None:
    stand_in = _public_server(monkeypatch)
    answer = await oauth._refresh({"resource_url": RESOURCE, "refresh_token": "rt-secret", "client_id": "cid"})
    assert answer["access_token"] == "new"
    token_requests = [r for r in stand_in.requests if r["path"] == "/token"]
    assert len(token_requests) == 1
    assert token_requests[0]["ip"] == OTHER_PUBLIC_IP and token_requests[0]["host"] == "auth.example"
    assert b"rt-secret" in token_requests[0]["body"]


async def test_the_refresh_token_never_goes_to_a_foreign_token_endpoint(monkeypatch) -> None:
    stand_in = _public_server(monkeypatch, as_meta=_metadata(token_endpoint="https://inside.example/steal"))
    with pytest.raises(MCPOAuthError):
        await oauth._refresh({"resource_url": RESOURCE, "refresh_token": "rt-secret", "client_id": "cid"})
    assert all(b"rt-secret" not in r["body"] for r in stand_in.requests)


async def test_a_refused_registration_names_the_code_never_the_text(monkeypatch) -> None:
    _public_server(monkeypatch, extra={
        ("auth.example", "/register"): Answer.json(
            {"error": "invalid_client_metadata", "error_description": "<b>internal db host 10.1.2.3</b>"},
            status=400),
    })
    place = await oauth._server_place(RESOURCE)
    with pytest.raises(MCPOAuthError) as caught:
        await oauth._register_client(place, "https://auth.example/register", "http://127.0.0.1:47820/cb")
    message = str(caught.value)
    assert "HTTP 400" in message and "invalid_client_metadata" in message
    assert "10.1.2.3" not in message and "<b>" not in message


async def test_the_code_exchange_is_pinned(monkeypatch) -> None:
    stand_in = _public_server(monkeypatch)
    pending = oauth.OAuthPending(
        server_name="svc", authorization_url="", state="s", code_verifier="v",
        redirect_uri="http://127.0.0.1:47820/oauth/callback", client_id="cid",
        token_endpoint="https://auth.example/token", scopes="", listener_port=47820,
        place=await oauth._server_place(RESOURCE),
    )
    assert (await oauth._exchange_code(pending, "the-code"))["access_token"] == "new"
    assert stand_in.connects == [(OTHER_PUBLIC_IP, 443)]


# ---------------------------------------------------------------------------
# The owner's own MCP server on this machine
# ---------------------------------------------------------------------------


@pytest.fixture()
def local_server():
    """An MCP server on 127.0.0.1 whose metadata the test chooses."""
    state: dict[str, Any] = {"hits": [], "as_meta": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self, body: dict, status: int = 200) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            state["hits"].append(("GET", self.path))
            base = state["base"]
            if self.path == "/.well-known/oauth-protected-resource/mcp":
                self._answer({"authorization_servers": [base]})
            elif self.path == "/.well-known/oauth-authorization-server":
                self._answer(state["as_meta"] or {
                    "issuer": base, "authorization_endpoint": f"{base}/authorize",
                    "token_endpoint": f"{base}/token", "registration_endpoint": f"{base}/register",
                })
            else:
                self._answer({}, 404)

        def log_message(self, *args):
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state["base"] = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()


async def test_the_owners_local_server_is_asked_on_its_own_origin(local_server) -> None:
    base = local_server["base"]
    found = await discover_auth_requirements(f"{base}/mcp")
    assert found["token_endpoint"] == f"{base}/token"
    assert local_server["hits"] == [
        ("GET", "/.well-known/oauth-protected-resource/mcp"),
        ("GET", "/.well-known/oauth-authorization-server"),
    ]


async def test_a_local_servers_metadata_cannot_name_another_private_address(local_server) -> None:
    local_server["as_meta"] = {
        "issuer": "http://10.0.0.5", "authorization_endpoint": "http://10.0.0.5/authorize",
        "token_endpoint": "http://10.0.0.5/token", "registration_endpoint": "http://10.0.0.5/register",
    }
    # Refused as another issuer than the one the server lists (2026-11-16);
    # its address is not an https one either.
    with pytest.raises(MCPOAuthError, match="another issuer|https"):
        await discover_auth_requirements(f"{local_server['base']}/mcp")


async def test_a_listed_issuer_kazma_may_not_use_is_never_asked(local_server) -> None:
    place = await oauth._server_place(f"{local_server['base']}/mcp")
    with pytest.raises(MCPOAuthError, match="No authorization server"):
        oauth._chosen_issuer(["http://10.0.0.5", "https://x.example/?q=1", 7], place)
    assert oauth._chosen_issuer(["http://10.0.0.5", local_server["base"]], place) == local_server["base"]


# ---------------------------------------------------------------------------
# Negative control: the old discovery and refresh
# ---------------------------------------------------------------------------


async def _old_discover(resource_url: str, http: httpx.AsyncClient) -> dict[str, Any]:
    """The discovery as it was until 2026-11-15 (no address check)."""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(resource_url)
    suffix = parsed.path.rstrip("/")
    r = await http.get(urlunparse((parsed.scheme, parsed.netloc, f"/.well-known/oauth-protected-resource{suffix}", "", "", "")))
    meta = r.json()
    assert meta.get("authorization_servers")
    r = await http.get(urlunparse((parsed.scheme, parsed.netloc, f"/.well-known/oauth-authorization-server{suffix}", "", "", "")))
    as_meta = r.json()
    return {"token_endpoint": as_meta.get("token_endpoint", "")}


async def test_control_the_old_refresh_posts_the_token_where_the_server_says() -> None:
    sent: list[tuple[str, bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.url.host, request.content))
        if request.url.path == PRM:
            return httpx.Response(200, json={"authorization_servers": ["https://auth.example"]})
        if request.url.path == OLD_ASM:
            return httpx.Response(200, json=_metadata(token_endpoint="https://inside.example/steal"))
        return httpx.Response(200, json={"access_token": "x"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        endpoints = await _old_discover(RESOURCE, http)
        await http.post(endpoints["token_endpoint"], data={"grant_type": "refresh_token", "refresh_token": "rt-secret"})
    assert ("inside.example", b"grant_type=refresh_token&refresh_token=rt-secret") in sent


# ---------------------------------------------------------------------------
# The issuer the resource names, and only it (2026-11-16)
# ---------------------------------------------------------------------------


async def test_the_resources_own_host_is_never_asked_for_the_issuers_metadata(monkeypatch) -> None:
    """Metadata the MCP server's host serves for another issuer is not read."""
    stand_in = _public_server(monkeypatch, extra={
        ("mcp.example", OLD_ASM): Answer.json(_metadata(
            issuer="https://mcp.example", token_endpoint="https://mcp.example/token")),
    })
    found = await discover_auth_requirements(RESOURCE)
    assert found["issuer"] == "https://auth.example"
    assert ("mcp.example", OLD_ASM) not in [(r["host"], r["path"]) for r in stand_in.requests]


async def test_metadata_naming_another_issuer_is_refused(monkeypatch) -> None:
    """RFC 8414 §3.3: the issuer in the metadata is the one asked for."""
    _public_server(monkeypatch, as_meta=_metadata(
        issuer="https://elsewhere.example", token_endpoint="https://elsewhere.example/token",
        authorization_endpoint="https://elsewhere.example/authorize",
        registration_endpoint="https://elsewhere.example/register"),
        dns={"elsewhere.example": [PUBLIC_IP]})
    with pytest.raises(MCPOAuthError, match="another issuer"):
        await discover_auth_requirements(RESOURCE)


async def test_metadata_about_another_resource_is_refused(monkeypatch) -> None:
    _public_server(monkeypatch, extra={
        ("mcp.example", PRM): Answer.json({"resource": "https://other.example/mcp",
                                           "authorization_servers": ["https://auth.example"]}),
    })
    with pytest.raises(MCPOAuthError, match="another resource"):
        await discover_auth_requirements(RESOURCE)


async def test_an_issuer_with_a_path_is_asked_where_rfc_8414_says(monkeypatch) -> None:
    issuer = "https://auth.example/tenant"
    stand_in = _public_server(monkeypatch, extra={
        ("mcp.example", PRM): Answer.json({"authorization_servers": [issuer]}),
        ("auth.example", "/.well-known/oauth-authorization-server/tenant"): Answer.json(_metadata(issuer=issuer)),
    })
    found = await discover_auth_requirements(RESOURCE)
    assert found["issuer"] == issuer
    assert ("auth.example", "/.well-known/oauth-authorization-server/tenant") in [
        (r["host"], r["path"]) for r in stand_in.requests]


async def test_openid_configuration_is_read_when_the_oauth_document_is_missing(monkeypatch) -> None:
    stand_in = _public_server(monkeypatch, extra={
        ("auth.example", ASM): Answer.json({}, status=404),
        ("auth.example", "/.well-known/openid-configuration"): Answer.json(_metadata()),
    })
    found = await discover_auth_requirements(RESOURCE)
    assert found["token_endpoint"] == "https://auth.example/token"
    assert [r["path"] for r in stand_in.requests if r["host"] == "auth.example"] == [
        ASM, "/.well-known/openid-configuration"]


async def test_control_the_old_discovery_took_the_resource_hosts_issuer() -> None:
    """The old discovery read the metadata from the MCP server's host and
    took its issuer as written: a server listing auth.example sent the
    sign-in to an issuer of its own choosing."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == PRM:
            return httpx.Response(200, json={"authorization_servers": ["https://auth.example"]})
        if request.url.host == "mcp.example" and request.url.path == OLD_ASM:
            return httpx.Response(200, json=_metadata(token_endpoint="https://mcp.example/token"))
        return httpx.Response(404, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        endpoints = await _old_discover(RESOURCE, http)
    assert endpoints["token_endpoint"] == "https://mcp.example/token"
