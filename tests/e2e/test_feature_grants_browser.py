"""Features on People, in a real browser (Spaces S2-11), English and Arabic.

Two people: Y, who is given X Studio, and G, who keeps what a new person
starts with. The admin reaches Settings by the sidebar, opens People and
turns X Studio on for Y with its switch. Then each person loads a page in
their own browser: Y's sidebar has the X Studio entry and the X page opens;
G's sidebar has no X Studio entry, the X page refuses G, and the command
palette does not offer it either -- while both still have Documents (a
new person's own).

The server half (every part of a feature refused, the control of a grant
that only hides the menu entry) is ``tests/test_feature_grants.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

pytestmark = pytest.mark.real_features


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


def _their_page(browser, base: str, lang: str, person: dict):
    from kazma_core.security import web_sessions

    session = web_sessions.create_session(role="operator", actor="login", username=person["name"],
                                          user_id=person["id"])
    context = browser.new_context()
    context.add_cookies([{"name": "kazma-lang", "value": lang, "url": base},
                         {"name": "kazma-session", "value": session, "url": base}])
    pg = context.new_page()
    pg.goto(f"{base}/chat?space={person['personal_space']}", wait_until="domcontentloaded")
    pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
    return pg


def _sidebar_has(pg, href: str) -> bool:
    return pg.locator(f".sidebar .nav-links a[href='{href}']").count() > 0


def _palette_offers(pg, href: str) -> bool:
    return pg.evaluate(
        "(href) => import('/static/js/modules/search_pages.js').then(m => m.pageOffered(href))", href)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_gives_x_studio_to_one_person_and_not_another(lang):
    from kazma_core.spaces import people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "feature-grants-test-secret-" + "x" * 24
        scope.set_enforced(True)
        y = people.create_person(f"yara-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        g = people.create_person(f"ghada-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        browser = p.chromium.launch()
        try:
            admin = browser.new_context()
            admin.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = admin.new_page()
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
            group = pg.locator("#people-features")
            group.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_features_title", lang) in group.inner_text()

            # X Studio on for Y, with Y's own switch.
            switch = pg.locator(f"[data-person-features='{y['id']}'] input[data-feature='x_studio']")
            assert not switch.is_checked()
            assert switch.get_attribute("aria-label") == _t(
                "settings.people_feature_of", lang, feature=_t("settings.feature.x_studio", lang), name=y["name"])
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/features")) as saved:
                switch.check()
            assert saved.value.ok, saved.value.text()
            pg.wait_for_function(
                "(id) => document.querySelector(\"[data-person-features='\" + id + \"'] input[data-feature='x_studio']\").checked",
                arg=y["id"], timeout=20000)
            # The admin's own: every feature, only Development switchable.
            owner_x = pg.locator(f"[data-person-features='{OWNER_ACCOUNT_ID}'] input[data-feature='x_studio']")
            assert owner_x.is_checked() and owner_x.is_disabled()

            # Y: the entry, the page, the palette.
            yours = _their_page(browser, h.base, lang, y)
            assert _sidebar_has(yours, "/x") and _sidebar_has(yours, "/documents")
            assert _palette_offers(yours, "/x")
            _soft_nav(yours, "/x")
            assert yours.evaluate("() => fetch('/x').then(r => r.status)") == 200

            # G: no entry, the page and its routes refuse, the palette leaves it out.
            theirs = _their_page(browser, h.base, lang, g)
            assert not _sidebar_has(theirs, "/x") and _sidebar_has(theirs, "/documents")
            assert not _palette_offers(theirs, "/x") and _palette_offers(theirs, "/documents")
            refused = theirs.evaluate(
                "() => fetch('/api/x/status').then(async r => [r.status, (await r.json()).code])")
            assert refused == [403, "feature_off"]
        finally:
            browser.close()
            scope.set_enforced(False)
