"""Who is in a shared space, in a real browser (Spaces S2-12, piece 2), EN/AR.

The admin makes a shared space and, on People, adds two people to it. The
first is told at their next page load, reaches Settings by the sidebar in
the shared space, reads what leaving does (the facts under them there are
forgotten, their messages stay) and leaves. The admin takes the second out
on People, after the same words from the remover's side; that person is
told at their next page load.

The rules are held by ``tests/test_space_membership.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_people_page_browser import _modal_action, _settings_loaded, _soft_nav, _t  # noqa: E402


def _plain(text: str) -> str:
    return " ".join(text.replace("\u2068", "").replace("\u2069", "").split())


def _person(pg, name: str) -> dict:
    made = pg.evaluate(
        "(name) => fetch('/api/people', {method: 'POST', credentials: 'same-origin',"
        " headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({name: name, password: 'a-long-enough-password'})}).then(r => r.json())",
        name)
    assert made.get("ok"), made
    return made["person"]


def _their_page(browser, h, lang: str, person: dict, space: str):
    from kazma_core.security import web_sessions

    session = web_sessions.create_session(role="operator", actor="login", username=person["name"],
                                          user_id=person["id"])
    context = browser.new_context()
    context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base},
                         {"name": "kazma-session", "value": session, "url": h.base}])
    page = context.new_page()
    page.goto(f"{h.base}/chat?space={space}", wait_until="load")
    return page


def _told(page, title: str) -> str:
    shown = page.locator("#modal-title")
    shown.wait_for(state="visible", timeout=20000)
    assert _plain(shown.inner_text()) == title
    # The shared dialog (components/modal.html): the one #modal-title is in.
    dialog = page.locator(".modal", has=page.locator("#modal-title"))
    return _plain(dialog.inner_text())


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_people_are_added_one_leaves_and_one_is_taken_out(lang):
    from kazma_core.spaces import identity, scope
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "space-members-test-secret-" + "x" * 24
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            admin = browser.new_context()
            admin.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = admin.new_page()
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            leaver = _person(pg, f"leaver-{lang}")
            taken = _person(pg, f"taken-{lang}")
            made = pg.evaluate(
                "() => fetch('/api/people/spaces', {method: 'POST', credentials: 'same-origin',"
                " headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: 'Family'})})"
                ".then(r => r.json())")
            space = made["space"]["id"]

            # The admin adds both on People.
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
            row = pg.locator(f"#people-shared-spaces [data-shared-space='{space}']")
            row.wait_for(state="visible", timeout=20000)
            for person in (leaver, taken):
                pg.select_option(f"#people-space-add-{space}", person["id"])
                pg.click(f"[data-space-add='{space}']")
                row.locator(f"[data-space-member='{person['id']}']").wait_for(state="visible", timeout=20000)

            # The first is told at their next page load, then leaves from the
            # space's own page, after reading what leaving does.
            theirs = _their_page(browser, h, lang, leaver, space)
            body = _told(theirs, _t("account.notice_added_title", lang))
            assert "Family" in body
            _modal_action(theirs, _t("common.ok", lang))
            _soft_nav(theirs, "/settings")
            _settings_loaded(theirs)
            theirs.locator(".settings-tab", has_text=_t("settings.tab_space_data", lang)).click()
            members = theirs.locator("#space-members")
            members.wait_for(state="visible", timeout=20000)
            # The box shows once the space has loaded; its rows come with the
            # members' own read, later (CI's Linux runner read the box empty).
            listed = members.locator(f"[data-space-member='{taken['id']}']")
            listed.wait_for(state="visible", timeout=20000)
            assert taken["name"] in listed.inner_text()
            theirs.click("#space-leave")
            warning = _told(theirs, _plain(_t("settings.space_leave_title", lang, name="Family")))
            assert _plain(_t("settings.space_leave_confirm", lang)) in warning
            with theirs.expect_response(lambda r: r.url.endswith("/leave")) as left:
                _modal_action(theirs, _t("settings.space_leave", lang))
            assert left.value.ok, left.value.text()
            assert not identity.is_member(leaver["id"], space)

            # The admin takes the second out on People, after the same words.
            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
            member = row.locator(f"[data-space-member='{taken['id']}']")
            member.wait_for(state="visible", timeout=20000)
            member.locator("button", has_text=_t("settings.space_remove", lang)).click()
            warning = _told(pg, _plain(_t("settings.space_remove_title", lang, name=taken["name"],
                                          space="Family")))
            assert _plain(_t("settings.space_remove_confirm", lang)) in warning
            with pg.expect_response(lambda r: r.request.method == "DELETE" and "/members/" in r.url) as out:
                _modal_action(pg, _t("settings.space_remove", lang))
            assert out.value.ok, out.value.text()
            member.wait_for(state="detached", timeout=20000)
            assert not identity.is_member(taken["id"], space)

            # They are told at their next page load: added, then taken out.
            gone = _their_page(browser, h, lang, taken, taken["personal_space"])
            _told(gone, _t("account.notice_added_title", lang))
            _modal_action(gone, _t("common.ok", lang))
            body = _told(gone, _t("account.notice_removed_title", lang))
            assert "Family" in body
        finally:
            browser.close()
            scope.set_enforced(False)
