"""Single tools per person on People, in a real browser (Spaces S2-01),
English and Arabic.

Y has X Studio. The admin reaches Settings by the sidebar, opens People,
opens Y's tools and switches posting (``x_post``) off with its own box;
the box stays off after People reloads, Y's other X tools stay on, the
identity store holds it, and Y keeps the X page (the feature is on). G's
tools hold no X Studio row (X Studio is off for G), and the admin has no
row at all (the admin has every tool).

The server half (offered and called in chat, a job and a swarm worker; the
control of hiding the tool from the offer only) is
``tests/test_person_tool_policy.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_feature_grants_browser import (  # noqa: E402
    _settings_loaded,
    _sidebar_has,
    _soft_nav,
    _t,
    _their_page,
)

pytestmark = pytest.mark.real_features


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_switches_posting_off_for_one_person(lang):
    from kazma_core.spaces import features, identity, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "person-tools-test-secret-" + "x" * 24
        scope.set_enforced(True)
        y = people.create_person(f"yara-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        g = people.create_person(f"ghada-{lang}", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        people.set_features(y["id"], {"x_studio": True}, by=OWNER_ACCOUNT_ID)
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
            group = pg.locator("#people-tools")
            group.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_tools_title", lang) in group.inner_text()
            # The admin has every tool: no row of theirs.
            assert pg.locator(f"[data-person-tools='{OWNER_ACCOUNT_ID}']").count() == 0

            # G has no X Studio, so no X Studio row to switch.
            pg.locator(f"[data-person-tools='{g['id']}'] summary").click()
            assert pg.locator(f"[data-person-tools='{g['id']}'] input[data-tool='x_post']").count() == 0
            assert pg.locator(f"[data-person-tools='{g['id']}'] input[data-tool='memory_search']").count() == 1

            # Y: posting off, with Y's own box.
            pg.locator(f"[data-person-tools='{y['id']}'] summary").click()
            box = pg.locator(f"[data-person-tools='{y['id']}'] input[data-tool='x_post']")
            assert box.is_checked()
            assert box.get_attribute("aria-label") == _t(
                "settings.people_tool_of", lang, tool="x_post", name=y["name"])
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{y['id']}/tools")) as saved:
                box.uncheck()
            assert saved.value.ok, saved.value.text()
            pg.wait_for_function(
                "(id) => { const b = document.querySelector(\"[data-person-tools='\" + id + \"'] input[data-tool='x_post']\");"
                " return b && !b.checked; }", arg=y["id"], timeout=20000)
            assert pg.locator(f"[data-person-tools='{y['id']}'] input[data-tool='x_status']").is_checked()
            grants = features.grants_of(identity.read_registry(), y["id"])
            assert grants.tools_off == {"x_post"} and grants.has("x_studio")

            # Y keeps X Studio: the entry and the page.
            yours = _their_page(browser, h.base, lang, y)
            assert _sidebar_has(yours, "/x")
            assert yours.evaluate("() => fetch('/x').then(r => r.status)") == 200
        finally:
            browser.close()
            scope.set_enforced(False)
