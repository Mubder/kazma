"""A member's own model key, in a real browser (Spaces S2-04).

With Spaces enforced, a member signed in with their own password reaches
Settings by the sidebar (soft navigation runs the page scripts again) and
finds "Use my own key" in My account, in the page's language: the install's
DeepSeek, on the install's key. They type a key the provider refuses: it is
said, and nothing is kept. They type a good one: it is kept sealed in their
personal space (read back from the vault), the row says "Your own key", and
the page never shows the key. "Use the install's key" asks, then forgets it.

Negative control: the admin's My account has no such card (the install's
keys are the admin's, under Providers).
"""

from __future__ import annotations

from typing import Any

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


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_member_keeps_and_forgets_their_own_key(lang, tmp_path, monkeypatch):
    import kazma_core.provider_check as provider_check
    from kazma_core.model_registry import get_model_registry
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    async def check(entry: dict[str, Any], model: str, **_k: Any) -> dict[str, Any]:
        if entry.get("api_key") == BAD:
            return {"ok": False, "error_code": "key_refused", "error": "The provider refused the key (HTTP 401)."}
        return {"ok": True}

    seen_keys: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        _sign_in_on()
        from kazma_core.security import vault as vault_mod

        monkeypatch.setenv("KAZMA_VAULT_KEY", "e" * 64)
        vault = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
        monkeypatch.setattr(vault_mod, "_vault", vault)
        monkeypatch.setattr(vault_mod, "_vault_init_attempted", True)
        monkeypatch.setattr(provider_check, "check_model", check)
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
            card = pg.locator("#own-model-keys")
            card.wait_for(state="visible", timeout=30000)
            assert card.locator("h3").inner_text().strip() == _t("settings.own_keys_title", lang)
            row = card.locator(".own-key-row[data-provider='deepseek']")
            row.wait_for(state="visible", timeout=30000)
            badge = row.locator(".badge")
            badge.filter(has_text=_t("settings.own_keys_using_install", lang)).wait_for(timeout=20000)
            use = row.locator("button", has_text=_t("settings.own_keys_use", lang))
            assert row.locator("button", has_text=_t("settings.own_keys_remove", lang)).is_hidden()

            # A key the provider refuses: said, and nothing kept.
            row.locator("input[type='password']").fill(BAD)
            use.click()
            _toast(pg, _t("settings.own_keys_err_key_refused", lang))
            assert _kept(account) == ""

            # A good key: kept, sealed in their personal space, never shown.
            row.locator("input[type='password']").fill(GOOD)
            use.click()
            _toast(pg, _t("settings.own_keys_saved", lang, provider="deepseek"))
            badge.filter(has_text=_t("settings.own_keys_using_own", lang)).wait_for(timeout=20000)
            assert _kept(account) == GOOD
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
