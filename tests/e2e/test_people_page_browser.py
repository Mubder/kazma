"""People, in a real browser (Spaces S1-25), English and Arabic.

The admin reaches Settings by the sidebar, opens People, adds a person,
resets their password (typed into a password field) and deletes them. In
between, the person loads a page in their own browser and is told, in the
page's language, that their password was reset -- once: a reload does not
tell them again.

Negative control: on an install that does not enforce Spaces, Settings has
no People tab.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

NAME = "dana-people"


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


def _modal_action(pg, label: str) -> None:
    pg.locator(".modal-footer button", has_text=label).first.click()


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_adds_resets_and_deletes_a_person(lang):
    from kazma_core.security import web_sessions
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "people-page-test-secret-" + "x" * 24
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
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
            pg.locator("#settings-people").wait_for(state="visible", timeout=20000)

            # Add.
            pg.fill("#people-new-name", NAME)
            pg.fill("#people-new-password", "a-long-enough-password")
            pg.click("#people-add")
            row = pg.locator("#settings-people .account-list-row", has_text=NAME)
            row.wait_for(state="visible", timeout=20000)
            person = pg.evaluate(
                "(name) => fetch('/api/people').then(r => r.json())"
                ".then(d => d.people.find(p => p.name === name))", NAME)

            # Reset: the new password goes into a password field.
            row.locator("button", has_text=_t("settings.people_reset", lang)).click()
            field = pg.locator(".modal input[x-model='$store.modal.inputValue']")
            field.wait_for(state="visible", timeout=10000)
            assert field.get_attribute("type") == "password"
            field.fill("another-long-password")
            # The route answers once the reset is recorded and the person's
            # sessions ended: a session made before that would end with them.
            with pg.expect_response(lambda r: r.url.endswith("/reset")) as reset:
                _modal_action(pg, _t("common.ok", lang))
            assert reset.value.ok

            # The person is told, in the page's language, once.
            session = web_sessions.create_session(role="operator", actor="login", username=NAME,
                                                  user_id=person["id"])
            them = browser.new_context()
            them.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base},
                              {"name": "kazma-session", "value": session, "url": h.base}])
            theirs = them.new_page()
            theirs.goto(f"{h.base}/chat?space={person['personal_space']}", wait_until="load")
            title = theirs.locator("#modal-title")
            title.wait_for(state="visible", timeout=20000)
            assert title.inner_text().strip() == _t("account.notice_reset_title", lang)
            _modal_action(theirs, _t("common.ok", lang))
            theirs.wait_for_function("() => !document.querySelector('.modal-overlay') ||"
                                     " getComputedStyle(document.querySelector('.modal-overlay')).display === 'none'",
                                     timeout=10000)
            # Read: the next page load finds nothing to tell, and shows nothing.
            theirs.reload(wait_until="load")
            assert theirs.evaluate("() => fetch('/api/account/notices').then(r => r.json())")["notices"] == []
            assert not theirs.locator("#modal-title").is_visible()

            # Delete.
            row.locator("button", has_text=_t("settings.delete", lang)).click()
            _modal_action(pg, _t("settings.delete", lang))
            row.wait_for(state="detached", timeout=20000)
        finally:
            browser.close()
            scope.set_enforced(False)
    assert refused == [], refused


def test_control_without_spaces_there_is_no_people_tab():
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            pg = browser.new_page()
            pg.goto(f"{h.base}/settings", wait_until="domcontentloaded")
            _settings_loaded(pg)
            assert pg.locator("#settings-people").count() == 0
            assert pg.locator(".settings-tab", has_text=_t("settings.tab_people", "en")).count() == 0
        finally:
            browser.close()
