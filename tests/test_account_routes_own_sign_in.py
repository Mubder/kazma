"""The account-route class: an API token never acts for the owner's own
account beyond what its role allows.

Every API token is the owner's account (``space_binding.account_of``). H1
refused a token below admin on My account's chat-link routes only; the rest
of the person's own routes still took one as the owner. An operator token --
one handed to another agent for A2A, say -- marked the owner's notices read
(``POST /api/account/notices/seen``), subscribed its own endpoint to the
owner's finished answers and approval cards (``POST /api/push/subscribe``),
cleared the owner's bell (``POST /api/alerts/read``), acknowledged and
snoozed the owner's problems.

Once Spaces is enforced, every route the built app classes as the person's
own (``route_scopes`` ACCOUNT) refuses an API token below admin, in the
middleware's one role rule (``auth.token_acts_for_the_owner``, asked by
``auth.principal_allows``): 403 ``own_sign_in_only``, or the role
refusal for an admin-only route. ``auth.TOKEN_MAY_ASK`` declares the few
that describe the credential or the whole install, each with why. The
shared secret and an admin token act as before; before Spaces nothing
changes.

The class gate enumerates the built app's routes, so a route added to the
person's own later is held the day it is added. Through the real app, sign-in
on, the tokens made by its own route.

Negative control: today's route -- the rule taken out -- lets an operator
token mark the owner's notices read.
"""

from __future__ import annotations

import json

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.security import platform_rbac
from kazma_core.spaces import identity, people, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._secured_app import SECRET, call, secured_app
from tests.test_route_scopes import served_routes

JSON = {"content-type": "application/json"}


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(platform_rbac, "multi_user_enabled", lambda: False)
    app = secured_app(monkeypatch)
    # create_app() reads the stored switch (off): enforced after it.
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    store.delete(identity.REGISTRY_KEY)
    yield app
    store.delete(identity.REGISTRY_KEY)


def _token(app, role: str) -> str:
    answer = call(app, "POST", "/api/settings/account/tokens",
                  body=json.dumps({"name": f"token-{role}", "role": role}).encode(),
                  headers={**JSON, "x-kazma-secret": SECRET})
    assert answer.status == 200, answer.body
    return answer.json()["token"]


def _own_routes(app) -> list[tuple[str, str]]:
    from kazma_ui.route_scopes import ACCOUNT, scope_of

    return sorted({(m, p) for m, p in served_routes(app)
                   if m not in ("HEAD", "OPTIONS", "MOUNT", "WEBSOCKET") and scope_of(m, p) == ACCOUNT})


def _filled(path: str) -> str:
    return "/".join("x1" if part.startswith("{") else part for part in path.split("/"))


# ── the class gate ──────────────────────────────────────────────────────


def test_every_route_of_the_person_s_own_refuses_an_operator_token(app) -> None:
    from kazma_ui.auth import TOKEN_MAY_ASK

    routes = _own_routes(app)
    assert ("POST", "/api/account/notices/seen") in routes, "the account routes were not listed"
    token = _token(app, "operator")
    let_through = []
    for method, path in routes:
        if (method, path) in TOKEN_MAY_ASK:
            continue
        answer = call(app, method, _filled(path), body=b"{}" if method != "GET" else b"",
                      headers={**JSON, "x-api-token": token})
        if answer.status != 403:
            let_through.append(f"{method} {path} -> {answer.status}")
    assert not let_through, (
        "An API token below admin acted as the owner on the person's own routes; refuse it "
        "(auth.token_acts_for_the_owner) or declare the route in auth.TOKEN_MAY_ASK with why:\n  "
        + "\n  ".join(let_through))


def test_every_declared_route_exists_and_describes_no_account(app) -> None:
    from kazma_ui.auth import TOKEN_MAY_ASK

    routes = set(_own_routes(app))
    assert set(TOKEN_MAY_ASK) <= routes, sorted(set(TOKEN_MAY_ASK) - routes)
    token = _token(app, "operator")
    for method, path in TOKEN_MAY_ASK:
        assert call(app, method, path, headers={"x-api-token": token}).status == 200, (method, path)


# ── the witness: the owner's notices ───────────────────────────────────


def _a_notice_for_the_owner() -> int:
    def told(reg: dict) -> None:
        people.record_event(reg, "chat_linked", OWNER_ACCOUNT_ID, "telegram:70707", detail="telegram")

    identity.update_registry(told)
    (notice,) = people.notices_for(OWNER_ACCOUNT_ID)
    return notice["seq"]


def test_an_operator_token_never_reads_the_owner_s_notices_as_read(app) -> None:
    seq = _a_notice_for_the_owner()
    token = _token(app, "operator")
    answer = call(app, "POST", "/api/account/notices/seen", body=json.dumps({"through": seq}).encode(),
                  headers={**JSON, "x-api-token": token})
    assert answer.status == 403 and answer.json().get("error") == "own_sign_in_only", (
        f"an operator token acted as the owner: {answer.status} {answer.body!r}")
    assert people.notices_for(OWNER_ACCOUNT_ID), "an operator token marked the owner's notices read"


@pytest.mark.parametrize("who", ["secret", "admin"])
def test_the_secret_and_an_admin_token_act_as_before(app, who) -> None:
    seq = _a_notice_for_the_owner()
    headers = {"x-kazma-secret": SECRET} if who == "secret" else {"x-api-token": _token(app, "admin")}
    answer = call(app, "POST", "/api/account/notices/seen", body=json.dumps({"through": seq}).encode(),
                  headers={**JSON, **headers})
    assert answer.status == 200, answer.body
    assert not people.notices_for(OWNER_ACCOUNT_ID)


def test_before_spaces_nothing_changes(app, monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", False)
    token = _token(app, "operator")
    answer = call(app, "GET", "/api/account/spaces", headers={"x-api-token": token})
    assert answer.status == 200, answer.body


def test_negative_control_today_s_route(app, monkeypatch) -> None:
    """Planted: today's route, the rule taken out. The operator token marks
    the owner's notices read: the check above fails on its own assertion."""
    from kazma_ui import auth

    monkeypatch.setattr(auth, "token_acts_for_the_owner", lambda *a, **k: False)
    with pytest.raises(AssertionError, match="acted as the owner|marked the owner's notices read"):
        test_an_operator_token_never_reads_the_owner_s_notices_as_read(app)


def test_negative_control_the_gate_finds_a_route_that_lets_a_token_through(app, monkeypatch) -> None:
    from kazma_ui import auth

    monkeypatch.setattr(auth, "token_acts_for_the_owner", lambda *a, **k: False)
    with pytest.raises(AssertionError, match="acted as the owner on the person's own routes"):
        test_every_route_of_the_person_s_own_refuses_an_operator_token(app)
