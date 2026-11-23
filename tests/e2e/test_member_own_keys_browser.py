"""A member's own model key, in a real browser (Spaces S2-04).

With Spaces enforced, a member signed in with their own password reaches
Settings by the sidebar (soft navigation runs the page scripts again) and
finds "Use my own key" in My account, in the page's language: the install's
DeepSeek, on the install's key. They type a key the provider refuses: it is
said, and nothing is kept. They type a good one: it is kept sealed in their
personal space (read back from the vault), the row says "Your own key", and
the page never shows the key. "Use the install's key" asks, then forgets it.

The key is checked by the providers' real check (``check_model``), against
a stand-in DeepSeek, while the install's calls go through a LiteLLM proxy
whose own key answers anything: each check goes to DeepSeek itself with the
typed key (the review of S2-04).

Negative controls: the admin's My account has no such card (the install's
keys are the admin's, under Providers); and the check sent through the
proxy, as before the review, keeps the key DeepSeek refuses.
"""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_settings_areas_browser import _open_settings_by_sidebar, _sign_in_on  # noqa: E402

MEMBER = "rana-own-keys"
OWN_SPACE = "rana-own"
GOOD = "rana-own-deepseek-key-1234"
BAD = "rana-refused-key"
ROW = {"name": "deepseek", "preset": "deepseek", "base_url": "https://api.deepseek.invalid/v1",
       "api_key": "install-deepseek-key-for-the-browser-test", "enabled": True, "models": ["deepseek-chat"]}
DEEPSEEK = "api.deepseek.invalid"
GATEWAY = "litellm.gateway.invalid"
GATEWAY_KEY = "install-gateway-master-key"


def _stand_ins(monkeypatch) -> list[tuple[str, str]]:
    """DeepSeek and the install's LiteLLM proxy, stood in, in this process
    (the server runs here): every model call to them as (host, key). DeepSeek
    refuses :data:`BAD`; the proxy answers any key, as its own key would.
    Every other client is the real one."""
    import httpx

    sent: list[tuple[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("authorization", "").removeprefix("Bearer ")
        if request.url.path.endswith("/chat/completions"):
            sent.append((request.url.host, key))
        if request.url.host == DEEPSEEK and key == BAD:
            return httpx.Response(401, json={"error": {"message": "invalid api key"}})
        return httpx.Response(200, content=json.dumps({
            "id": "c", "model": "deepseek-chat",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}),
            headers={"content-type": "application/json"})

    stand_in = httpx.MockTransport(answer)
    real = httpx.AsyncClient

    def routed(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        if any(host in str(kwargs.get("base_url") or "") for host in (DEEPSEEK, GATEWAY)):
            kwargs["transport"] = stand_in
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", routed)
    monkeypatch.delenv("KAZMA_LITELLM", raising=False)
    monkeypatch.setenv("KAZMA_LITELLM_URL", f"https://{GATEWAY}/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", GATEWAY_KEY)
    return sent


def _t(key: str, lang: str, **values: Any) -> str:
    from kazma_ui.i18n import t

    text = t(key, lang)
    for name, value in values.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def _member() -> tuple[str, str]:
    """A local user with a personal space: (account, raw session id)."""
    from kazma_core.security import platform_rbac, web_sessions
    from kazma_core.spaces import identity
    from kazma_ui import space_binding

    user = platform_rbac.create_local_user(MEMBER, "a-long-enough-password-1")
    account = space_binding.account_of({"source": "session", "actor": "login", "user_id": user.user_id,
                                        "username": MEMBER, "role": "operator"})
    assert account, "the member has no account"
    identity.create_space("Rana", created_by=account, personal_of=account, space_id=OWN_SPACE)
    return account, web_sessions.create_session(role="operator", actor="login", username=MEMBER,
                                                user_id=user.user_id)


def _kept(account: str) -> str:
    from kazma_core.spaces import own_keys

    own_keys._forget(account)
    return own_keys._read_key(account, "deepseek")


def _toast(pg, text: str) -> None:
    pg.locator(".toast-container .toast", has_text=text).first.wait_for(
        state="visible", timeout=20000)


def _real_model_calls(real_chat: Any) -> Any:
    """The providers' own ``chat`` back inside the harness (which stands in
    for every model call at the class): the key check is the real one."""
    from kazma_core.llm_provider import LLMProvider

    return mock.patch.object(LLMProvider, "chat", real_chat)


def _open_my_keys(pg, lang: str):
    """My account's "Use my own key" card, reached by the sidebar: the
    DeepSeek row, on the install's key."""
    card = pg.locator("#own-model-keys")
    card.wait_for(state="visible", timeout=30000)
    assert card.locator("h3").inner_text().strip() == _t("settings.own_keys_title", lang)
    row = card.locator(".own-key-row[data-provider='deepseek']")
    row.wait_for(state="visible", timeout=30000)
    row.locator(".badge").filter(has_text=_t("settings.own_keys_using_install", lang)).wait_for(timeout=20000)
    return row


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_member_keeps_and_forgets_their_own_key(lang, tmp_path, monkeypatch):
    from kazma_core.llm_provider import LLMProvider
    from kazma_core.model_registry import get_model_registry
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    seen_keys: list[str] = []
    sent = _stand_ins(monkeypatch)
    real_chat = LLMProvider.chat
    with (unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p,
          _real_model_calls(real_chat)):
        _sign_in_on()
        from kazma_core.security import vault as vault_mod

        monkeypatch.setenv("KAZMA_VAULT_KEY", "e" * 64)
        vault = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
        monkeypatch.setattr(vault_mod, "_vault", vault)
        monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
        registry = get_model_registry()
        registry.upsert_provider(dict(ROW))
        account, session = _member()
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base},
                                 {"name": "kazma-session", "value": session, "url": h.base}])
            pg = context.new_page()
            pg.on("response", lambda r: seen_keys.append(r.url) if "/api/account/model-keys" in r.url else None)
            _open_settings_by_sidebar(pg, h.base, OWN_SPACE)
            row = _open_my_keys(pg, lang)
            badge = row.locator(".badge")
            use = row.locator("button", has_text=_t("settings.own_keys_use", lang))
            assert row.locator("button", has_text=_t("settings.own_keys_remove", lang)).is_hidden()

            # A key the provider refuses: said, and nothing kept.
            row.locator("input[type='password']").fill(BAD)
            use.click()
            _toast(pg, _t("settings.own_keys_err_key_refused", lang))
            assert _kept(account) == ""
            assert sent == [(DEEPSEEK, BAD)], sent

            # A good key: kept, sealed in their personal space, never shown.
            row.locator("input[type='password']").fill(GOOD)
            use.click()
            _toast(pg, _t("settings.own_keys_saved", lang, provider="deepseek"))
            badge.filter(has_text=_t("settings.own_keys_using_own", lang)).wait_for(timeout=20000)
            assert _kept(account) == GOOD
            assert sent == [(DEEPSEEK, BAD), (DEEPSEEK, GOOD)], "a check went through the proxy"
            assert row.locator("input[type='password']").input_value() == ""
            assert GOOD not in pg.content()

            # Back to the install's key: asked, then forgotten.
            row.locator("button", has_text=_t("settings.own_keys_remove", lang)).click()
            pg.locator(".modal-footer button", has_text=_t("settings.own_keys_remove", lang)).first.click()
            badge.filter(has_text=_t("settings.own_keys_using_install", lang)).wait_for(timeout=20000)
            assert _kept(account) == ""
        finally:
            browser.close()
            scope.set_enforced(False)
            vault.close()
    assert seen_keys, "the page never asked for the member's keys"


def test_control_a_check_through_the_proxy_keeps_a_key_deepseek_refuses(tmp_path, monkeypatch):
    """The check as it was before the review of S2-04 (sent the way the
    install's calls go): the proxy's own key answers, and the member's
    refused key is kept."""
    import kazma_core.provider_check as provider_check
    from kazma_core.llm_provider import LLMProvider
    from kazma_core.model_registry import get_model_registry
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    sent = _stand_ins(monkeypatch)
    monkeypatch.setattr(provider_check, "_go_direct", lambda client, url, key: None)
    real_chat = LLMProvider.chat
    with (unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p,
          _real_model_calls(real_chat)):
        _sign_in_on()
        from kazma_core.security import vault as vault_mod

        monkeypatch.setenv("KAZMA_VAULT_KEY", "e" * 64)
        vault = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
        monkeypatch.setattr(vault_mod, "_vault", vault)
        monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
        get_model_registry().upsert_provider(dict(ROW))
        account, session = _member()
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": "en", "url": h.base},
                                 {"name": "kazma-session", "value": session, "url": h.base}])
            pg = context.new_page()
            _open_settings_by_sidebar(pg, h.base, OWN_SPACE)
            row = _open_my_keys(pg, "en")
            row.locator("input[type='password']").fill(BAD)
            row.locator("button", has_text=_t("settings.own_keys_use", "en")).click()
            _toast(pg, _t("settings.own_keys_saved", "en", provider="deepseek"))
            assert _kept(account) == BAD
            assert sent == [(GATEWAY, GATEWAY_KEY)], sent
        finally:
            browser.close()
            scope.set_enforced(False)
            vault.close()


def test_control_the_admins_account_has_no_own_key_card():
    from kazma_core.spaces import scope
    from kazma_core.spaces.scope import OWNER_SPACE_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        _sign_in_on()
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            pg = browser.new_context().new_page()
            _open_settings_by_sidebar(pg, h.base, OWNER_SPACE_ID)
            pg.locator("input[x-model='passwordForm.new_password']").wait_for(state="attached", timeout=20000)
            assert pg.locator("#own-model-keys").count() == 0
        finally:
            browser.close()
            scope.set_enforced(False)
