"""Once Spaces is on, the chat apps' allowlist fields go and each person's
links are listed, in a real browser (Spaces S3-02), English and Arabic.

The admin reaches Settings by the sidebar. Under Providers & Connectors →
Platform connectors, no adapter card offers "Allowed User IDs" any more:
each says the bot answers the people who linked their own account. On
People, a person whose Telegram account is linked shows it.

The server half is ``tests/test_chat_senders_are_linked.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

PASSWORD = "a-long-enough-password"
TG_USER = 555801


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
def test_the_allowlists_go_and_people_list_their_links(lang):
    from kazma_core.spaces import chat_links, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    name = f"dana-listed-{lang}"
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "chat-links-listed-secret-" + "x" * 24
        scope.set_enforced(True)
        chat_links._forget_attempts_for_tests()
        person = people.create_person(name, PASSWORD, by=OWNER_ACCOUNT_ID)
        code = chat_links.issue_code(person["id"], "telegram")
        assert chat_links.redeem("telegram", TG_USER, code["code"], direct=True).outcome == "linked"
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context()
            ctx.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = ctx.new_page()
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)

            pg.locator(".settings-tab", has_text=_t("settings.tab_providers_connectors", lang)).click()
            pg.locator("#hub-subtab-connectors").click()
            retired = pg.locator("[data-allowlist-retired]")
            retired.first.wait_for(state="visible", timeout=20000)
            assert retired.count() == 4, "Telegram, Discord, Slack and WhatsApp"
            assert retired.first.inner_text().strip() == _t("chat_links.allowlist_retired", lang)
            for field in ("#f-adapter-routing-tg-allowed", "#f-adapter-routing-discord-allowed",
                          "#f-adapter-routing-slack-allowed", "#wa-allowed"):
                assert pg.locator(field).count() == 0, field

            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
            pid = person["id"]
            links = pg.locator(f"[data-person-links='{pid}']")
            links.wait_for(state="visible", timeout=20000)
            pg.locator(f"[data-person-link='telegram:{TG_USER}']").wait_for(state="visible", timeout=20000)
            text = links.inner_text()
            assert _t("chat_links.people_links", lang) in text and str(TG_USER) in text, text
            owner = pg.locator(f"[data-person-links='{OWNER_ACCOUNT_ID}']")
            assert _t("chat_links.people_no_links", lang) in owner.inner_text()
        finally:
            browser.close()
            scope.set_enforced(False)
