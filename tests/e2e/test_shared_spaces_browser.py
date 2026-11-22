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
