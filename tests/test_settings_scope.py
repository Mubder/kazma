"""Spaces S1-23: every settings key is the install's, a space's or a
person's, and Settings splits into My account, This space and Install.

* The class gate, extended: every key product code writes (the scan of
  ``tests/test_settings_restore_classes.py``), every key ``kazma.yaml``
  ships and every backup rule's pattern has a scope
  (``kazma_core.settings_scope``); a key with none fails, and so does a
  scope rule that names nothing.
* Once Spaces is enforced the install's writers -- Settings' generic save,
  a batch save, the agent's ``config_save`` -- write install keys only: a
  space's key written there would land in the owner's space whatever space
  the request is in.
* Once enforced, a member reaches their own part of Settings (the page,
  their password, their sessions) and is shown My account and This space;
  Install, the API tokens and the platform users stay the admin's, and the
  page carries none of Install's markup or values.

Each rule has its control. The browser half (EN and AR, a member signed in)
is ``tests/e2e/test_settings_areas_browser.py``.
"""

from __future__ import annotations

from functools import cache

import pytest
from kazma_core.security.platform_rbac import role_allows
from kazma_core.settings_restore import KEY_RULES
from kazma_core.settings_scope import (
    ACCOUNT,
    INSTALL,
    SCOPE_RULES,
    SPACE,
    install_writer_refusal,
    _scope_of_key,
)
from kazma_core.settings_validation import shipped_values
from kazma_core.spaces import scope


@pytest.fixture
def enforced(monkeypatch):
    monkeypatch.setattr(scope, "_enforced", True)


@cache
def _every_key() -> frozenset[str]:
    """Every key product code writes, every shipped key, every backup rule."""
    from tests.test_settings_restore_classes import DYNAMIC_WRITES, _scan

    found, _unread = _scan()
    keys = {key for written in found.values() for key in written}
    keys |= {key for patterns in DYNAMIC_WRITES.values() if isinstance(patterns, tuple) for key in patterns}
    keys |= set(shipped_values())
    keys |= {rule.pattern for rule in KEY_RULES}
    return frozenset(keys)


def unscoped(keys) -> list[str]:
    """The gate's check: the keys no scope rule names."""
    return sorted(key for key in keys if _scope_of_key(key) is None)


# ── the class gate ───────────────────────────────────────────────────────


def test_every_settings_key_has_a_scope() -> None:
    keys = _every_key()
    assert len(keys) > 150, f"the scan found too few keys to be working: {len(keys)}"
    assert unscoped(keys) == [], (
        "Settings keys with no scope (kazma_core/settings_scope.py): say whether each is the "
        "install's, a space's or a person's")


def test_control_a_key_with_no_scope_is_found() -> None:
    assert unscoped({"planted.new_setting", "agent.name"}) == ["planted.new_setting"]


def test_no_scope_rule_names_nothing() -> None:
    keys = _every_key()
    stale = [rule.pattern for rule in SCOPE_RULES
             if not any(_scope_of_key(key) is not None and (key == rule.pattern or key.startswith(rule.pattern)
                                                           or key == rule.pattern.rstrip("."))
                        for key in keys)]
    assert stale == [], stale
    assert len({rule.pattern for rule in SCOPE_RULES}) == len(SCOPE_RULES)
    assert {rule.scope for rule in SCOPE_RULES} == {INSTALL, SPACE, ACCOUNT}


def test_a_space_s_and_a_person_s_keys_are_theirs() -> None:
    assert _scope_of_key("connectors.x.bearer_token") == SPACE
    assert _scope_of_key("tenant.s0a0a0a0a0a0a.connectors.x.bearer_token") == SPACE
    assert _scope_of_key("tenant.s0a0a0a0a0a0a.connectors.github.oauth_token") == SPACE
    assert _scope_of_key("email.accounts_by_space") == SPACE
    assert _scope_of_key("long_task.thread-1") == SPACE
    assert _scope_of_key("account.tokens") == ACCOUNT
    assert _scope_of_key("web_session.abc") == ACCOUNT
    assert _scope_of_key("connectors.x.reply.policy") == INSTALL  # the reply bot's policy is the admin's
    assert _scope_of_key("connectors.telegram.token") == INSTALL
    assert _scope_of_key("agent.name") == INSTALL


# ── the install's writers ────────────────────────────────────────────────


def test_the_install_s_writers_refuse_a_space_s_key_once_enforced(enforced) -> None:
    assert "belongs to a space" in install_writer_refusal("connectors.x.bearer_token")
    assert "belongs to a person" in install_writer_refusal("account.tokens")
    assert install_writer_refusal("agent.name") is None


def test_without_spaces_the_install_s_writers_write_as_before() -> None:
    assert install_writer_refusal("connectors.x.bearer_token") is None
    assert install_writer_refusal("account.tokens") is None


@pytest.fixture
def settings_client(monkeypatch):
    """The product's settings router, its writes to a private store."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_ui.settings import create_settings_router

    store = get_config_store()
    app = FastAPI()
    app.include_router(create_settings_router(SimpleNamespace(), store, templates=None))
    with TestClient(app) as client:
        yield client, store


def test_settings_generic_save_refuses_a_space_s_key(settings_client, enforced) -> None:
    client, store = settings_client
    store.delete("connectors.x.drafting.post_length")
    refused = client.put("/api/settings/single", json={"key": "connectors.x.drafting.post_length",
                                                       "value": "short"})
    assert refused.status_code == 400 and "belongs to a space" in refused.text, refused.text
    assert store.get("connectors.x.drafting.post_length") is None
    batch = client.put("/api/settings", json=[{"key": "agent.name", "value": "Kazma"},
                                              {"key": "connectors.x.drafting.post_length", "value": "short"}])
    assert batch.status_code == 400 and store.get("connectors.x.drafting.post_length") is None
    try:
        assert client.put("/api/settings/single", json={"key": "agent.name", "value": "Kazma"}).status_code == 200
    finally:
        store.delete("agent.name")


def test_control_without_spaces_the_generic_save_writes_it(settings_client) -> None:
    client, store = settings_client
    try:
        got = client.put("/api/settings/single", json={"key": "connectors.x.drafting.post_length",
                                                       "value": "short"})
        assert got.status_code == 200, got.text
        assert store.get("connectors.x.drafting.post_length") == "short"
    finally:
        store.delete("connectors.x.drafting.post_length")


def test_nothing_writes_the_identity_store_or_the_switch_before_spaces(settings_client) -> None:
    """Before Spaces is enforced too: written as a setting, the switch would
    turn Spaces on before the checks that must pass first, and the store
    would take a person into a space with no record (S1-30)."""
    import asyncio

    from kazma_core.agent.tool_refusal import RefusedBeforeEffect
    from kazma_core.spaces import identity, scope

    from tests._chat_space_cases import _registry

    assert not scope.spaces_enforced()
    client, store = settings_client
    for key, value in ((identity.ENFORCED_KEY, True), (identity.REGISTRY_KEY, {"accounts": {}})):
        assert "Kazma's own record" in (install_writer_refusal(key) or "")
        single = client.put("/api/settings/single", json={"key": key, "value": value})
        assert single.status_code == 400, single.text
        batch = client.put("/api/settings", json=[{"key": key, "value": value}])
        assert batch.status_code == 400, batch.text
        assert store.get(key) is None
        tool = _registry().get_tool("config_save")
        with pytest.raises(RefusedBeforeEffect):
            result = tool.func(key=key, value="true")
            if asyncio.iscoroutine(result):
                asyncio.run(result)
    assert not scope.spaces_enforced()


def test_control_the_rule_of_before_lets_the_switch_through(settings_client, monkeypatch) -> None:
    from kazma_core import settings_scope
    from kazma_core.spaces import identity

    monkeypatch.setattr(settings_scope, "_IDENTITY_KEYS", ())
    monkeypatch.setattr("kazma_ui.settings.install_writer_refusal", settings_scope.install_writer_refusal)
    client, store = settings_client
    try:
        got = client.put("/api/settings/single", json={"key": identity.ENFORCED_KEY, "value": True})
        assert got.status_code == 200, got.text
        assert store.get(identity.ENFORCED_KEY) is True
    finally:
        store.delete(identity.ENFORCED_KEY)
        from kazma_core.spaces import scope

        scope.set_enforced(False)


def test_the_agent_s_config_save_refuses_a_space_s_key(enforced) -> None:
    import asyncio

    from kazma_core.agent.tool_refusal import RefusedBeforeEffect

    from tests._chat_space_cases import _registry

    tool = _registry().get_tool("config_save")
    with pytest.raises(RefusedBeforeEffect, match="belongs to a space"):
        result = tool.func(key="email.accounts", value="[]")
        if asyncio.iscoroutine(result):
            asyncio.run(result)


# ── who reaches Settings ─────────────────────────────────────────────────


def test_a_member_reaches_their_own_part_of_settings_once_enforced(enforced) -> None:
    for role in ("operator", "viewer"):
        assert role_allows(role, "/settings", "GET")
        assert role_allows(role, "/api/settings/account/password", "PUT")
        assert role_allows(role, "/api/settings/account/sessions", "GET")
        # The owner's API tokens, the platform users and every install
        # setting stay the admin's.
        assert not role_allows(role, "/api/settings/account/tokens", "GET")
        assert not role_allows(role, "/api/settings/account/tokens", "POST")
        assert not role_allows(role, "/api/settings/single", "PUT")
        assert not role_allows(role, "/api/settings", "GET")
        assert not role_allows(role, "/settings", "POST")


def test_control_without_spaces_settings_stays_the_admin_s() -> None:
    assert not role_allows("operator", "/settings", "GET")
    assert not role_allows("operator", "/api/settings/account/password", "PUT")
    assert role_allows("admin", "/settings", "GET")


@pytest.fixture(scope="module")
def app_client():
    from fastapi.testclient import TestClient
    from kazma_ui.app import create_app

    with TestClient(create_app(), client=("127.0.0.1", 40000), base_url="http://127.0.0.1:9090") as client:
        yield client


def _page(app_client, monkeypatch, *, admin: bool) -> str:
    from kazma_ui import auth

    monkeypatch.setattr(scope, "_enforced", True)
    monkeypatch.setattr(auth, "admin_decision", lambda request: "ok" if admin else "forbidden")
    got = app_client.get("/settings")
    assert got.status_code == 200, got.status_code
    return got.text


#: Install markup a member's page never carries: its tablist, the hub's
#: first sub-tab, the API token form, the agent's system prompt field.
_INSTALL_MARKS = ('id="settings-area-install"', 'id="hub-subtab-providers"', 'x-model="tokenName"',
                  "createPlatformUser()")


def test_a_member_is_shown_my_account_and_this_space(app_client, monkeypatch) -> None:
    html = _page(app_client, monkeypatch, admin=False)
    assert 'id="settings-area-account"' in html and 'id="settings-area-space"' in html
    assert 'data-areas="account,space"' in html
    assert [mark for mark in _INSTALL_MARKS if mark in html] == []
    # The password and sessions are theirs.
    assert 'x-model="passwordForm.new_password"' in html


def test_the_admin_is_shown_the_three_areas(app_client, monkeypatch) -> None:
    html = _page(app_client, monkeypatch, admin=True)
    assert 'data-areas="account,space,install"' in html
    assert all(mark in html for mark in _INSTALL_MARKS)


def test_control_a_page_built_for_everyone_shows_a_member_the_install(app_client, monkeypatch) -> None:
    from kazma_ui import settings

    monkeypatch.setattr(settings, "_settings_areas", lambda request: ["account", "space", "install"])
    html = _page(app_client, monkeypatch, admin=False)
    assert all(mark in html for mark in _INSTALL_MARKS)
