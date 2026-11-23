"""Stage 3 review, H1: only a person's own sign-in links a chat app.

``space_binding.account_of`` makes every API token the owner's account, and
My account's chat-app routes asked nothing more: an operator token -- one
handed to another agent for A2A, say -- asked for a link code of the
owner's account, its holder sent it from their own Telegram and was the
chat apps' admin from then on (``/skill install``, ``/model default``,
YOLO, ``/swarm config``, ``/x``); the same token listed the owner's links
and unlinked them for good (an unlinked account is never adopted again).

Once Spaces is enforced the three routes (``chat_links_api._caller``) answer
a person's own sign-in -- a browser session -- or a credential with the
admin role (the shared secret, an admin token: they can already do
everything); a token of any other role is refused (403
``own_sign_in_only``), and nothing is issued, listed or unlinked. The real
app, sign-in on, its tokens made by its own route.

Negative control: the caller of before (the account behind any sign-in) --
an operator token's code links its holder as the admin.
"""

from __future__ import annotations

import json

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.security import platform_rbac
from kazma_core.spaces import chat_apps, chat_links, identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

from tests._secured_app import SECRET, call, secured_app

JSON = {"content-type": "application/json"}
ROUTES = (
    ("POST", "/api/account/chat-links/code", {"platform": "telegram"}),
    ("GET", "/api/account/chat-links", None),
    ("DELETE", "/api/account/chat-links", {"platform": "telegram", "user_id": "70707"}),
)


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(platform_rbac, "multi_user_enabled", lambda: False)
    app = secured_app(monkeypatch)
    # create_app() reads the stored switch (off): enforced after it.
    monkeypatch.setattr(scope, "_enforced", True)
    store = get_config_store()
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_attempts_for_tests()

    def owner_link(reg: dict) -> None:
        reg["links"].append({"platform": "telegram", "user_id": "70707", "account": OWNER_ACCOUNT_ID})

    identity.update_registry(owner_link)
    chat_links.refresh_links()
    yield app
    for key in (identity.REGISTRY_KEY, chat_apps.BINDINGS_KEY, chat_links.CODES_KEY):
        store.delete(key)
    chat_links._forget_links_for_tests()


def _token(app, role: str) -> str:
    answer = call(app, "POST", "/api/settings/account/tokens",
                  body=json.dumps({"name": f"token-{role}", "role": role}).encode(),
                  headers={**JSON, "x-kazma-secret": SECRET})
    assert answer.status == 200, answer.body
    return answer.json()["token"]


def _as(app, method: str, path: str, body: dict | None, headers: dict[str, str]):
    return call(app, method, path, body=json.dumps(body).encode() if body is not None else b"",
                headers={**JSON, **headers})


@pytest.mark.parametrize("role", ["operator", "viewer"])
def test_a_token_that_is_not_the_admin_s_links_nothing(app, role) -> None:
    token = _token(app, role)
    for method, path, body in ROUTES:
        answer = _as(app, method, path, body, {"x-api-token": token})
        # A viewer is the role middleware's to refuse first.
        assert answer.status == 403 and (
            answer.json().get("error") == "own_sign_in_only" or role == "viewer"), (
            role, method, answer.status, answer.body)
    assert not get_config_store().get(chat_links.CODES_KEY, {}), "no code was issued"
    assert chat_links.links_of(OWNER_ACCOUNT_ID), "the owner's link stays"


def test_an_operator_token_s_code_never_makes_its_holder_the_admin(app) -> None:
    token = _token(app, "operator")
    answer = _as(app, "POST", "/api/account/chat-links/code", {"platform": "telegram"},
                 {"x-api-token": token})
    code = answer.json().get("code", "")
    if code:
        chat_links.redeem("telegram", "424242", code, direct=True, target="telegram:424242")
    assert not chat_links.linked_admin("telegram", "424242"), (
        "an operator token's code made its holder the chat apps' admin")


@pytest.mark.parametrize("who", ["secret", "admin"])
def test_the_secret_and_an_admin_token_link_the_owner(app, who) -> None:
    headers = {"x-kazma-secret": SECRET} if who == "secret" else {"x-api-token": _token(app, "admin")}
    issued = _as(app, "POST", "/api/account/chat-links/code", {"platform": "telegram"}, headers)
    assert issued.status == 200 and issued.json()["code"], issued.body
    listed = _as(app, "GET", "/api/account/chat-links", None, headers)
    assert [link["user_id"] for link in listed.json()["links"]] == ["70707"]


def test_negative_control_the_caller_of_before(app, monkeypatch) -> None:
    """Planted: the caller of before, the account behind any sign-in. The
    operator token's code links its holder as the admin: the check above
    fails on its own assertion."""
    from kazma_ui import chat_links_api

    monkeypatch.setattr(chat_links_api, "_own_sign_in", lambda *a, **k: True)
    with pytest.raises(AssertionError, match="made its holder the chat apps' admin"):
        test_an_operator_token_s_code_never_makes_its_holder_the_admin(app)
