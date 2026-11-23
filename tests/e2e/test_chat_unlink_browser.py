"""Unlinking a chat app from My account, in a real browser (Spaces S3-07),
English and Arabic.

A person whose Telegram is linked signs in, reaches Settings by the sidebar
and opens My account. The link's row has Unlink; it asks first, in the
page's language, then the row is gone and the bot refuses that Telegram
account at once.

The server half is ``tests/test_unlink_stops_everything.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

PASSWORD = "a-long-enough-password"
TG_USER = 555811


def _soft_nav(pg, href: str) -> None:
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _settings_loaded(pg) -> None:
    pg.locator("#settings-root").wait_for(state="attached", timeout=30000)
    pg.wait_for_function(
        "() => { const el = document.querySelector('#settings-root');"
        " return el && el._x_dataStack && !el._x_dataStack[0].loading; }", timeout=30000)


def _t(key: str, lang: str, **values: str) -> str:
    from kazma_ui.i18n import t

    return t(key, lang, **values)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_person_unlinks_telegram_from_my_account(lang):
    from kazma_core.spaces import chat_links, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    name = f"dana-unlink-{lang}"
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "chat-unlink-secret-" + "x" * 24
        scope.set_enforced(True)
        chat_links._forget_attempts_for_tests()
        person = people.create_person(name, PASSWORD, by=OWNER_ACCOUNT_ID)
        code = chat_links.issue_code(person["id"], "telegram")
        assert chat_links.redeem("telegram", TG_USER, code["code"], direct=True,
                                 target=f"telegram:{TG_USER}").outcome == "linked"
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context()
            ctx.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            signed = ctx.request.post(f"{h.base}/api/auth/login", data={"username": name, "password": PASSWORD})
            assert signed.ok, signed.text()
            pg = ctx.new_page()
            pg.goto(f"{h.base}/chat", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_account", lang)).click()
            row = pg.locator(f"[data-chat-link='telegram:{TG_USER}']")
            row.wait_for(state="visible", timeout=20000)
            unlink = pg.locator(f"[data-chat-unlink='telegram:{TG_USER}']")
            assert unlink.inner_text().strip() == _t("chat_links.unlink", lang)
            unlink.click()
            title = pg.locator("#modal-title")
            title.wait_for(state="visible", timeout=10000)
            assert title.inner_text().strip() == _t("chat_links.unlink_title", lang, platform="Telegram")
            pg.locator(".modal-footer button", has_text=_t("chat_links.unlink", lang)).first.click()
            row.wait_for(state="detached", timeout=20000)
            pg.wait_for_function("() => Alpine.store('toast').items.length > 0", timeout=20000)
            said = pg.evaluate("() => Alpine.store('toast').items.map(t => t.message).join(' | ')")
            assert _t("chat_links.unlinked", lang, platform="Telegram") in said, said
            assert chat_links.link_refusal("telegram", TG_USER) == chat_links.NOT_LINKED
            assert chat_links.address_revoked(f"telegram:{TG_USER}")
        finally:
            browser.close()
            scope.set_enforced(False)
