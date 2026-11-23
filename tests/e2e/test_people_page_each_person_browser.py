"""The People page, each person whole, in a real browser (Spaces S2-10),
English and Arabic.

The admin reaches Settings by the sidebar and opens People. For a person who
signed in with their name and password (a second browser, the real login
route) and is a member of a shared space, the page shows their features,
tools, daily limit, models, spaces, last sign-in and open sessions. The
admin ends their sessions: the person is signed out, the page says no
session is open, and the change is recorded.

The server half is ``tests/test_people_sign_ins_and_sessions.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

NAME = "dana-people-page"
PASSWORD = "a-long-enough-password"


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


def _sessions_say(pg, n: int) -> str:
    return pg.evaluate("(n) => window.kazmaCount('settings.people_sessions_open', n, {n: String(n)})", n)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_people_page_shows_each_person_whole(lang):
    from kazma_core.spaces import identity, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "people-page-test-secret-" + "x" * 24
        scope.set_enforced(True)
        person = people.create_person(NAME, PASSWORD, by=OWNER_ACCOUNT_ID)
        garden = people.create_shared_space("Garden", by=OWNER_ACCOUNT_ID)
        people.add_member(garden["id"], person["id"], by=OWNER_ACCOUNT_ID)
        browser = p.chromium.launch()
        try:
            # The person signs in, in a browser of their own.
            them = browser.new_context()
            signed_in = them.request.post(f"{h.base}/api/auth/login",
                                          data={"username": NAME, "password": PASSWORD})
            assert signed_in.ok, signed_in.text()
            assert them.request.get(f"{h.base}/api/auth/me").ok

            admin = browser.new_context()
            admin.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = admin.new_page()
            pg.on("response", lambda r: refused.append(f"{r.status} {r.url}")
                  if r.status in (401, 403) and "/api/" in r.url else None)
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()

            pid = person["id"]
            spaces = pg.locator(f"[data-person-spaces='{pid}']")
            spaces.wait_for(state="visible", timeout=20000)
            pg.wait_for_function(
                "(id) => (document.querySelector(\"[data-person-spaces='\" + id + \"']\") || {}).innerText"
                "?.includes('Garden')", arg=pid, timeout=20000)
            text = spaces.inner_text()
            assert _t("settings.people_space_usage_personal", lang) in text and "Garden" in text, text
            last = pg.locator(f"[data-person-last-sign-in='{pid}']").inner_text()
            assert _t("settings.people_last_sign_in", lang) in last
            assert _t("settings.people_never_signed_in", lang) not in last, last
            sessions = pg.locator(f"[data-person-sessions='{pid}']")
            assert sessions.inner_text().strip() == _sessions_say(pg, 1)
            # Beside them: features, tools, the daily limit and models.
            for selector in (f"[data-person-features='{pid}']", f"[data-limit='{pid}']"):
                assert pg.locator(selector).is_visible(), selector
            for selector in (f"[data-person-tools='{pid}']", f"[data-person-models='{pid}']"):
                assert pg.locator(selector).count() == 1, selector

            # End their sessions.
            details = pg.locator(f"[data-person-details='{pid}']")
            end = details.locator("[data-person-end-sessions]")
            assert end.get_attribute("aria-label") == _t("settings.people_end_sessions_of", lang, name=NAME)
            end.click()
            dialog = pg.locator(".modal-content, [role='dialog']").filter(
                has_text=_t("settings.people_end_sessions_confirm", lang)).first
            dialog.wait_for(state="visible", timeout=20000)
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/{pid}/sessions/end")) as ended:
                dialog.get_by_role("button", name=_t("settings.people_end_sessions", lang)).click()
            assert ended.value.ok, ended.value.text()
            pg.wait_for_function(
                "(args) => (document.querySelector(\"[data-person-sessions='\" + args[0] + \"']\") || {})"
                ".innerText?.trim() === args[1]", arg=[pid, _sessions_say(pg, 0)], timeout=20000)
            assert not end.is_visible()
            assert them.request.get(f"{h.base}/api/auth/me").status == 401
            assert identity.read_registry()["events"][-1]["kind"] == "sessions_ended"
        finally:
            browser.close()
            scope.set_enforced(False)
    assert refused == [], refused
