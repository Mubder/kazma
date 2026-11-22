"""The Activity page, in a real browser (Spaces S2-09), English and Arabic.

The admin changes the limits for spaces on People (a real request through
the whole app), then reaches Activity by the sidebar: the change is listed
under the admin's name with its route and answer, in the page's language.
A row of another space is never shown.

The server half (every route enumerated, the control) is
``tests/test_actions_name_the_person.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402


def _soft_nav(pg, href: str) -> None:
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _t(key: str, lang: str, **values: str) -> str:
    from kazma_ui.i18n import t

    return t(key, lang, **values)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_activity_page_lists_what_was_done_here(lang):
    from kazma_core.spaces import activity, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "activity-test-secret-" + "x" * 24
        scope.set_enforced(True)
        person = people.create_person("dana-activity", "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        activity.record(person["personal_space"], person["id"], "POST /api/elsewhere", [], 200)
        browser = p.chromium.launch()
        try:
            admin = browser.new_context()
            admin.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = admin.new_page()
            pg.on("response", lambda r: refused.append(f"{r.status} {r.url}")
                  if r.status in (401, 403) and "/api/" in r.url else None)
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            done = pg.evaluate(
                "() => fetch('/api/people/space-limits', {method: 'PUT', headers: "
                "{'Content-Type': 'application/json', 'X-Kazma-Space': 'default'}, "
                "body: JSON.stringify({limits: {swarm: 3}})}).then(r => r.status)")
            assert done == 200
            _soft_nav(pg, "/activity")
            pg.locator("#activity-root").wait_for(state="attached", timeout=20000)
            row = pg.locator("[data-activity-who='%s']" % OWNER_ACCOUNT_ID).first
            row.wait_for(state="visible", timeout=20000)
            text = row.inner_text()
            assert "PUT /api/people/space-limits" in text, text
            assert _t("activity.done", lang) in text, text
            assert "/api/elsewhere" not in pg.locator("#activity-list").inner_text()
            assert _t("activity.title", lang) in pg.title()
        finally:
            browser.close()
            scope.set_enforced(False)
    assert refused == [], refused
