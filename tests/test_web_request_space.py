"""The web names its space on every call; the server checks every time (S1-06).

A page says which space it is in on each request (header ``X-Kazma-Space``,
or the ``space`` query value for a download or an event stream), and the
tenant middleware checks that the signed-in account is a member before the
handler runs. The space is never kept in the session: two tabs in two spaces
must not mix.

Through the real middleware and the real identity store. Every ``space``
rule of the route table (``kazma_ui.route_scopes``) is driven as a member of
one space asking for another. The negative control keeps the space in the
session, as one value for the browser: the second tab's choice then decides
for the first.

On an install that does not enforce Spaces, a request that names the tenant
it would run in anyway runs as before (the page's own flow, below, with the
first rule as its negative control); naming any other space needs membership.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from kazma_core.config_store import get_config_store
from kazma_core.security import platform_rbac
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_ui import auth, route_scopes, space_binding

HEADER = space_binding.SPACE_HEADER


@pytest.fixture(autouse=True)
def _fresh_identity(monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    yield
    store.delete(identity.REGISTRY_KEY)


@pytest.fixture
def people() -> dict[str, Any]:
    """Layla and Omar, each with a personal space, and a family space only
    Layla is in."""
    layla = platform_rbac.create_local_user("layla", "password-number-1")
    omar = platform_rbac.create_local_user("omar", "password-number-2")
    ids = {a["name"]: a["id"] for a in identity.list_accounts()}
    identity.create_space("", created_by=OWNER_ACCOUNT_ID, personal_of=ids["layla"],
                          space_id="layla-own")
    identity.create_space("", created_by=OWNER_ACCOUNT_ID, personal_of=ids["omar"],
                          space_id="omar-own")
    identity.create_space("Family", created_by=OWNER_ACCOUNT_ID, space_id="family")
    identity.add_member("family", ids["layla"])
    return {
        "layla": {"username": "layla", "role": "operator", "user_id": layla.user_id,
                  "actor": "login", "source": "session"},
        "omar": {"username": "omar", "role": "operator", "user_id": omar.user_id,
                 "actor": "login", "source": "session"},
        "ids": ids,
    }


def _app(monkeypatch: pytest.MonkeyPatch, principal_of_request) -> TestClient:
    """The real tenant middleware in front of a handler that says what it
    was bound to. ``principal_of_request(request)`` stands in for sign-in."""
    monkeypatch.setattr(auth, "get_request_principal", principal_of_request)
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    reached: list[str] = []

    @app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def anything(rest: str) -> dict:
        reached.append(rest)
        bound = scope.current_scope()
        return {"space": scope.require_space_id(), "asker": bound.asker if bound else None}

    client = TestClient(app)
    client.reached = reached  # type: ignore[attr-defined]
    return client


# ── Naming a space ───────────────────────────────────────────────────────


def test_a_member_runs_in_the_space_the_page_names(monkeypatch, people) -> None:
    with _app(monkeypatch, lambda request: people["layla"]) as client:
        in_family = client.get("/api/chat/sessions", headers={HEADER: "family"}).json()
        in_own = client.get("/api/chat/sessions", headers={HEADER: "layla-own"}).json()
        by_query = client.get("/api/chat/files/f1?space=family").json()
    assert in_family == {"space": "family", "asker": people["ids"]["layla"]}
    assert in_own == {"space": "layla-own", "asker": people["ids"]["layla"]}
    assert by_query["space"] == "family", "a download names its space in the address"


def test_the_header_wins_over_the_address(monkeypatch, people) -> None:
    with _app(monkeypatch, lambda request: people["layla"]) as client:
        answer = client.get("/api/chat/sessions?space=layla-own", headers={HEADER: "family"})
    assert answer.json()["space"] == "family"


@pytest.mark.parametrize("named", [
    "omar-own", "no-such-space", "Family", "../family", "FAMILY", "x" * 80,
])
def test_a_space_the_caller_is_not_in_is_refused_before_the_handler(
    monkeypatch, people, named
) -> None:
    """One answer for another person's space, the owner's, a space that does
    not exist and a malformed name: the refusal never says which."""
    with _app(monkeypatch, lambda request: people["layla"]) as client:
        answer = client.get("/api/chat/sessions", headers={HEADER: named})
        reached = list(client.reached)
    assert answer.status_code == 403, answer.text
    assert answer.json() == {
        "ok": False, "code": "not_a_member", "error": "You are not a member of that space.",
    }
    assert reached == [], "the handler never ran"


def test_the_admin_is_not_a_member_of_other_peoples_spaces(monkeypatch, people) -> None:
    """Being the admin gives no entry to a space (decision O1)."""
    secret = {"username": "operator", "role": "admin", "user_id": "shared-secret",
              "source": "secret"}
    with _app(monkeypatch, lambda request: secret) as client:
        assert client.get("/api/chat/sessions", headers={HEADER: "layla-own"}).status_code == 403
        assert client.get("/api/chat/sessions", headers={HEADER: "omar-own"}).status_code == 403
        mine = client.get("/api/chat/sessions", headers={HEADER: OWNER_SPACE_ID}).json()
        shared = client.get("/api/chat/sessions", headers={HEADER: "family"}).json()
    assert mine == {"space": "default", "asker": OWNER_ACCOUNT_ID}
    assert shared["space"] == "family", "the admin made the family space and is in it"


def test_a_sign_in_with_no_account_is_a_member_of_nothing(monkeypatch, people) -> None:
    oidc = {"username": "ana", "role": "operator", "user_id": "sub-1", "actor": "oidc",
            "source": "session"}
    with _app(monkeypatch, lambda request: oidc) as client:
        assert client.get("/api/chat/sessions", headers={HEADER: "family"}).status_code == 403


def test_an_unsigned_request_cannot_name_a_space(monkeypatch, people) -> None:
    """Before sign-in the name is ignored: nothing is bound from it."""
    with _app(monkeypatch, lambda request: None) as client:
        answer = client.get("/api/auth/status", headers={HEADER: "family"}).json()
    assert answer == {"space": "default", "asker": None}


# ── The page's own flow on an install that does not enforce Spaces ───────
#
# A page is rendered for the tenant its request ran in, and a page that
# names it back must get what it got before Spaces. The older multi-user
# mode's users and OIDC sign-ins are members of no space: checking membership
# for the space the page was rendered for refused every call after the page
# loaded (review of the foundation, 2026-11-17). On main the same calls
# answered 200.


def _rendered_space() -> str:
    """What ``<meta name="kazma-space">`` holds for the request being served
    (``app.py``'s ``kazma_space`` template global, the same expression)."""
    from kazma_core.tenant_context import get_current_tenant_id

    bound = scope.current_scope()
    return bound.space_id if bound is not None else (get_current_tenant_id() or "")


def _page_app(monkeypatch: pytest.MonkeyPatch, principal: dict[str, Any]) -> TestClient:
    """The real tenant middleware in front of a page and an API route."""
    monkeypatch.setattr(auth, "get_request_principal", lambda request: principal)
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    @app.get("/chat")
    def page() -> dict:
        return {"rendered_for": _rendered_space()}

    @app.api_route("/api/{rest:path}", methods=["GET", "POST"])
    def api(rest: str) -> dict:
        from kazma_core.tenant_context import get_current_tenant_id

        bound = scope.current_scope()
        return {"tenant": get_current_tenant_id(), "asker": bound.asker if bound else None}

    return TestClient(app)


@pytest.fixture
def older_sign_ins() -> dict[str, dict[str, Any]]:
    """Three sign-ins of an install from before Spaces. None is a member of
    any space: a local user, a local user bound to a tenant, an OIDC session."""
    sara = platform_rbac.create_local_user("sara", "password-number-3")
    nora = platform_rbac.create_local_user("nora", "password-number-4")
    ids = {a["name"]: a["id"] for a in identity.list_accounts()}
    assert identity.spaces_of(ids["sara"]) == [] and identity.spaces_of(ids["nora"]) == []
    identity.create_space("Family", created_by=OWNER_ACCOUNT_ID, space_id="family")
    return {
        "local user": {
            "principal": {"username": "sara", "role": "operator", "user_id": sara.user_id,
                          "actor": "login", "source": "session"},
            "tenant": "default", "asker": ids["sara"],
        },
        "local user with a tenant": {
            "principal": {"username": "nora", "role": "operator", "user_id": nora.user_id,
                          "actor": "login", "source": "session", "tenant_id": "acme"},
            "tenant": "acme", "asker": ids["nora"],
        },
        "oidc session": {
            "principal": {"username": "ana", "role": "operator", "user_id": "sub-1",
                          "actor": "oidc", "source": "session"},
            "tenant": "default", "asker": None,
        },
    }


_OLDER = ["local user", "local user with a tenant", "oidc session"]

#: The tenant each older sign-in's requests ran in before Spaces.
_OLD_TENANT = {"local user": "default", "local user with a tenant": "acme",
               "oidc session": "default"}

#: Each older sign-in naming a space other than the one its page is rendered for.
_OTHER_SPACES = [
    (who, other)
    for who in _OLDER
    for other in ("family", "default", "acme", "no-such-space", "Default")
    if other != _OLD_TENANT[who]
]


@pytest.mark.parametrize("who", _OLDER)
def test_a_page_that_names_the_space_it_was_rendered_for_runs_as_before(
    monkeypatch, older_sign_ins, who
) -> None:
    """Page load, then the page's calls naming the rendered space: 200 and
    the old tenant, by header and by address."""
    case = older_sign_ins[who]
    expected = {"tenant": case["tenant"], "asker": case["asker"]}
    with _page_app(monkeypatch, case["principal"]) as client:
        load = client.get("/chat")
        assert load.status_code == 200, load.text
        rendered = load.json()["rendered_for"]
        assert rendered == case["tenant"]
        unnamed = client.get("/api/chat/sessions")
        by_header = client.get("/api/chat/sessions", headers={HEADER: rendered})
        by_address = client.get(f"/api/chat/files/f1?space={rendered}")
        a_write = client.post("/api/chat/stream", headers={HEADER: rendered})
    assert unnamed.status_code == 200 and unnamed.json() == expected
    for answer in (by_header, by_address, a_write):
        assert answer.status_code == 200, f"{who}: {answer.status_code} {answer.text}"
        assert answer.json() == expected, "the same tenant and asker as naming none"


@pytest.mark.parametrize("who, other", _OTHER_SPACES)
def test_an_older_sign_in_naming_a_different_space_is_still_refused(
    monkeypatch, older_sign_ins, who, other
) -> None:
    case = older_sign_ins[who]
    assert other != case["tenant"] and _OLD_TENANT[who] == case["tenant"]
    with _page_app(monkeypatch, case["principal"]) as client:
        answer = client.get("/api/chat/sessions", headers={HEADER: other})
    assert answer.status_code == 403 and answer.json()["code"] == "not_a_member", answer.text


@pytest.mark.parametrize("who", _OLDER)
def test_enforced_naming_the_old_tenant_is_no_way_in(monkeypatch, older_sign_ins, who) -> None:
    """Once Spaces is enforced, every named space needs membership."""
    case = older_sign_ins[who]
    monkeypatch.setattr(scope, "_enforced", True)
    with _page_app(monkeypatch, case["principal"]) as client:
        answer = client.get("/api/chat/sessions", headers={HEADER: case["tenant"]})
    assert answer.status_code == 403, answer.text
    assert answer.json()["code"] == ("no_account" if case["asker"] is None else "not_a_member")


def test_the_rendered_space_runs_while_accounts_cannot_be_read(monkeypatch, older_sign_ins) -> None:
    """Not enforced, an unreadable identity store never stops the page's own
    calls: they run with no asker, as an unnamed call does. Naming another
    space cannot be checked, and is refused."""
    case = older_sign_ins["local user"]

    def _down(_principal):
        raise identity.IdentityUnavailable("away")

    with _page_app(monkeypatch, case["principal"]) as client:
        monkeypatch.setattr(space_binding, "account_of", _down)
        mine = client.get("/api/chat/sessions", headers={HEADER: "default"})
        other = client.get("/api/chat/sessions", headers={HEADER: "family"})
    assert mine.status_code == 200 and mine.json() == {"tenant": "default", "asker": None}
    assert other.status_code == 503 and other.json()["code"] == "identity_unavailable"


def _space_for_request_as_first_written(
    principal, named, *, legacy_tenant: str, needs_space: bool
) -> space_binding.Binding:
    """The foundation's first rule (commit 79629e7ad): any named space needs
    membership, the one the page was rendered for included."""
    account = space_binding.account_of(principal)
    if account is None:
        if named is not None and principal is not None:
            raise space_binding.SpaceRefused(
                "not_a_member", 403, "You are not a member of that space.")
        return space_binding.Binding(space_id=legacy_tenant or OWNER_SPACE_ID, asker=None)
    if named is not None:
        if not scope.valid_space_id(named) or not identity.is_member(account, named):
            raise space_binding.SpaceRefused(
                "not_a_member", 403, "You are not a member of that space.")
        return space_binding.Binding(space_id=named, asker=account)
    return space_binding.Binding(space_id=legacy_tenant or OWNER_SPACE_ID, asker=account)


@pytest.mark.parametrize("who", _OLDER)
def test_control_the_first_rule_refused_the_pages_own_calls(
    monkeypatch, older_sign_ins, who
) -> None:
    case = older_sign_ins[who]
    monkeypatch.setattr(space_binding, "space_for_request", _space_for_request_as_first_written)
    with _page_app(monkeypatch, case["principal"]) as client:
        load = client.get("/chat")
        rendered = load.json()["rendered_for"]
        fetch = client.get("/api/chat/sessions", headers={HEADER: rendered})
    assert load.status_code == 200 and rendered == case["tenant"]
    assert fetch.status_code == 403 and fetch.json()["code"] == "not_a_member", (
        "the first rule is expected to refuse the page's own call: it is the negative control"
    )


# ── Every space route of the table ───────────────────────────────────────


def _sample_paths() -> list[tuple[str, str]]:
    """One real request per ``space`` rule of the route table."""
    return [(method.upper(), path) for method, path in route_scopes.sample_requests("space")]


def test_the_table_has_space_routes_to_drive() -> None:
    assert len(_sample_paths()) >= 20


def test_every_space_route_refuses_a_member_of_another_space(monkeypatch, people) -> None:
    refused: list[str] = []
    wrongly_reached: list[str] = []
    with _app(monkeypatch, lambda request: people["omar"]) as client:
        for method, path in _sample_paths():
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            answer = client.request(method, path, headers={HEADER: "family"})
            if answer.status_code == 403 and answer.json().get("code") == "not_a_member":
                refused.append(path)
            else:
                wrongly_reached.append(f"{method} {path} -> {answer.status_code}")
        assert client.reached == []
    assert not wrongly_reached, wrongly_reached
    assert refused


def test_every_space_route_runs_for_a_member(monkeypatch, people) -> None:
    with _app(monkeypatch, lambda request: people["layla"]) as client:
        for method, path in _sample_paths():
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            answer = client.request(method, path, headers={HEADER: "family"})
            assert answer.status_code == 200, f"{method} {path}: {answer.status_code}"
            assert answer.json() == {"space": "family", "asker": people["ids"]["layla"]}


# ── Naming none ──────────────────────────────────────────────────────────


def test_not_enforced_a_request_that_names_none_runs_as_before(monkeypatch, people) -> None:
    with _app(monkeypatch, lambda request: people["layla"]) as client:
        answer = client.get("/api/chat/sessions").json()
    assert answer == {"space": "default", "asker": people["ids"]["layla"]}


def test_enforced_the_owners_space_is_refused_to_a_member_of_others(monkeypatch, people) -> None:
    """Layla's requests ran in ``default`` before Spaces; once enforced she
    is in her own spaces only, and naming the owner's is refused."""
    monkeypatch.setattr(scope, "_enforced", True)
    with _app(monkeypatch, lambda request: people["layla"]) as client:
        answer = client.get("/api/chat/sessions", headers={HEADER: "default"})
        reached = list(client.reached)
    assert answer.status_code == 403 and answer.json()["code"] == "not_a_member"
    assert reached == []


def test_enforced_one_space_needs_no_name(monkeypatch, people) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    with _app(monkeypatch, lambda request: people["omar"]) as client:
        answer = client.get("/api/chat/sessions").json()
    assert answer == {"space": "omar-own", "asker": people["ids"]["omar"]}


def test_enforced_several_spaces_must_say_which_on_a_space_route(monkeypatch, people) -> None:
    monkeypatch.setattr(scope, "_enforced", True)
    with _app(monkeypatch, lambda request: people["layla"]) as client:
        space_route = client.get("/api/chat/sessions")
        account_route = client.get("/api/account/spaces")
    assert space_route.status_code == 400 and space_route.json()["code"] == "space_required"
    assert account_route.status_code == 200, "a route about the person needs no space named"
    assert account_route.json()["space"] == "layla-own", "it runs in their personal space"


# ── Two tabs never mix ───────────────────────────────────────────────────


def test_two_tabs_in_two_spaces_do_not_mix(monkeypatch, people) -> None:
    """One browser session, two tabs, each naming its own space on each call."""
    with _app(monkeypatch, lambda request: people["layla"]) as client:
        seen = [
            client.get("/api/chat/sessions", headers={HEADER: tab}).json()["space"]
            for tab in ("family", "layla-own", "family", "layla-own", "family")
        ]
    assert seen == ["family", "layla-own", "family", "layla-own", "family"]


def test_control_a_space_kept_in_the_session_mixes_two_tabs(monkeypatch, people) -> None:
    """The design the plan rejects: the space is one value of the session,
    changed by a "switch" call. Tab B switches; tab A's next call lands in
    tab B's space."""
    session_space = {"value": "family"}
    app = FastAPI()

    @app.middleware("http")
    async def session_space_middleware(request: Request, call_next):
        with scope.bind_scope(session_space["value"], people["ids"]["layla"]):
            return await call_next(request)

    @app.post("/api/switch/{space_id}")
    def switch(space_id: str) -> dict:
        session_space["value"] = space_id
        return {"ok": True}

    @app.get("/api/chat/sessions")
    def sessions() -> dict:
        return {"space": scope.require_space_id()}

    with TestClient(app) as client:
        tab_a_first = client.get("/api/chat/sessions").json()["space"]
        client.post("/api/switch/layla-own")  # tab B switches
        tab_a_second = client.get("/api/chat/sessions").json()["space"]
    assert (tab_a_first, tab_a_second) == ("family", "layla-own"), (
        "the session-kept space is expected to mix the tabs: it is the negative control"
    )


# ── The caller's own spaces ──────────────────────────────────────────────


def test_the_spaces_route_lists_only_the_callers_spaces(monkeypatch, people) -> None:
    from kazma_ui.account_spaces_api import create_account_spaces_router

    monkeypatch.setattr(auth, "get_request_principal", lambda request: people["layla"])
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())
    app.include_router(create_account_spaces_router())
    with TestClient(app) as client:
        body = client.get("/api/account/spaces", headers={HEADER: "family"}).json()
    assert body["ok"] is True and body["account"] == people["ids"]["layla"]
    assert [s["id"] for s in body["spaces"]] == ["layla-own", "family"], "personal space first"
    assert body["spaces"][0]["personal"] is True and body["spaces"][1]["personal"] is False
    assert body["current"] == "family"
    assert "omar-own" not in str(body) and "default" not in [s["id"] for s in body["spaces"]]


def test_the_spaces_route_answers_503_when_accounts_cannot_be_read(monkeypatch) -> None:
    import kazma_ui.account_spaces_api as api
    from kazma_ui.account_spaces_api import create_account_spaces_router

    def _down(_principal):
        raise identity.IdentityUnavailable("away")

    monkeypatch.setattr(auth, "get_request_principal", lambda request: {"source": "secret"})
    monkeypatch.setattr(api.space_binding, "account_of", _down)
    app = FastAPI()
    app.include_router(create_account_spaces_router())
    with TestClient(app) as client:
        answer = client.get("/api/account/spaces")
    assert answer.status_code == 503 and answer.json()["code"] == "identity_unavailable"
