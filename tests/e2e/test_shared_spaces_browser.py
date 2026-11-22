"""Shared spaces in a real browser (Spaces S2-12, piece 1), English and Arabic.

The admin reaches Settings by the sidebar, opens People and makes the two
kinds of shared space: a plain one (the default) and one with a project
folder. Each is listed with its kind and its one member; the folder is shown
for the second. A folder of Kazma's own (its install folder) is refused, and
the page says why in its language; nothing is made.

The rules themselves are held by ``tests/test_shared_spaces.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_people_page_browser import _settings_loaded, _soft_nav, _t  # noqa: E402


def _created(pg, before: int) -> list[dict]:
    pg.wait_for_function(
        "(n) => { const el = document.querySelector('#settings-root');"
        " return el && el._x_dataStack && el._x_dataStack[0].sharedSpaces.length > n; }",
        arg=before, timeout=20000)
    return pg.evaluate("() => fetch('/api/people').then(r => r.json()).then(d => d.shared_spaces)")


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_makes_both_kinds_of_shared_space(lang, tmp_path):
    from kazma_core.paths import installed_project_root
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    folder = tmp_path / "project"
    folder.mkdir()
    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "shared-spaces-test-secret-" + "x" * 24
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = context.new_page()
            pg.on("response", lambda r: refused.append(f"{r.status} {r.url}")
                  if r.status in (401, 403) and "/api/" in r.url else None)
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
            group = pg.locator("#people-shared-spaces")
            group.wait_for(state="visible", timeout=20000)
            assert group.inner_text().count(_t("settings.people_spaces_none", lang)) == 1

            # A plain shared space: the default kind.
            assert pg.locator("#people-space-kind-plain").is_checked()
            assert not pg.locator("#people-space-folder").is_visible()
            pg.fill("#people-space-name", "Family")
            pg.click("#people-space-create")
            spaces = _created(pg, 0)
            plain = next(s for s in spaces if s["name"] == "Family")
            assert plain["kind"] == "sandboxed" and len(plain["members"]) == 1
            row = pg.locator(f"#people-shared-spaces [data-shared-space='{plain['id']}']")
            row.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_space_kind_plain", lang) in row.inner_text()

            # A folder of Kazma's own is refused, said in the page's language.
            pg.check("#people-space-kind-project")
            pg.locator("#people-space-folder").wait_for(state="visible", timeout=10000)
            pg.fill("#people-space-name", "Kazma dev")
            pg.fill("#people-space-folder", str(installed_project_root()))
            pg.click("#people-space-create")
            pg.locator(".toast", has_text=_t("settings.people_err.folder_kazma_folder", lang)).first.wait_for(
                state="visible", timeout=20000)
            assert len(pg.evaluate("() => fetch('/api/people').then(r => r.json())"
                                   ".then(d => d.shared_spaces)")) == 1

            # One with a project folder.
            pg.fill("#people-space-folder", str(folder))
            pg.click("#people-space-create")
            spaces = _created(pg, 1)
            dev = next(s for s in spaces if s["name"] == "Kazma dev")
            assert dev["kind"] == "host" and os.path.samefile(dev["project_folder"], folder)
            row = pg.locator(f"#people-shared-spaces [data-shared-space='{dev['id']}']")
            row.wait_for(state="visible", timeout=20000)
            text = row.inner_text()
            assert _t("settings.people_space_kind_project", lang) in text
            assert str(folder.resolve()) in text
        finally:
            browser.close()
            scope.set_enforced(False)
    assert refused == [], refused


@pytest.mark.real_features
@pytest.mark.parametrize("lang", ["en", "ar"])
def test_turning_development_on_for_someone_says_what_it_means(lang):
    """S2-12, piece 3: on People, turning Development on for a person other
    than the admin is confirmed after the page says what it means (N21);
    Cancel leaves it off."""
    from kazma_core.spaces import identity, scope
    from playwright.sync_api import sync_playwright

    from tests.e2e.test_people_page_browser import _modal_action

    def plain(text: str) -> str:
        return " ".join(text.replace("\u2068", "").replace("\u2069", "").split())

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "development-means-test-secret-" + "x" * 24
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = context.new_page()
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            person = pg.evaluate(
                "() => fetch('/api/people', {method: 'POST', credentials: 'same-origin',"
                " headers: {'Content-Type': 'application/json'},"
                " body: JSON.stringify({name: 'coder', password: 'a-long-enough-password'})}).then(r => r.json())")
            pid = person["person"]["id"]
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
            means = plain(_t("settings.people_development_means", lang))
            assert means in plain(pg.locator("#people-development-means").inner_text())
            box = pg.locator(f"[data-person-features='{pid}'] input[data-feature='development']")
            box.wait_for(state="visible", timeout=20000)
            assert not box.is_checked()

            dialog = pg.locator(".modal", has=pg.locator("#modal-title"))
            box.click()
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            assert plain(pg.locator("#modal-title").inner_text()) == plain(
                _t("settings.people_development_title", lang, name="coder"))
            assert means in plain(dialog.inner_text())
            _modal_action(pg, _t("common.cancel", lang))
            pg.wait_for_function(
                "(id) => !document.querySelector(\"[data-person-features='\" + id + \"'] input[data-feature='development']\").checked",
                arg=pid, timeout=10000)
            assert not identity.holds_development(pid)

            box.click()
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            with pg.expect_response(lambda r: r.url.endswith("/features")) as saved:
                _modal_action(pg, _t("settings.people_development_turn_on", lang))
            assert saved.value.ok, saved.value.text()
            assert identity.holds_development(pid)
        finally:
            browser.close()
            scope.set_enforced(False)
