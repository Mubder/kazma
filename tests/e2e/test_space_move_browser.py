"""Moving a space, in a real browser (Spaces S4-06), English and Arabic.

The admin makes a shared space, reaches Settings by the sidebar in it and
opens This space's data: its owner is offered "Move this space". They read
what the move does, choose a password (typed twice into password fields) and
get the bundle, sealed. They delete the space, then bring the bundle in on
People -> Shared spaces: the file, its password and who owns it here. The
space is back, owned by the person chosen. A wrong password is refused, in
the page's language, and nothing is brought in.

The rules are held by ``tests/test_space_bundle_round_trip.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_people_page_browser import _modal_action, _settings_loaded, _soft_nav, _t  # noqa: E402

PASSWORD = "the-family-move-password"


def _json(pg, script: str, arg=None):
    return pg.evaluate(script, arg)


def _plain(text: str) -> str:
    return " ".join(text.replace("\u2068", "").replace("\u2069", "").split())


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_owner_moves_a_space_and_the_admin_brings_it_in(lang):
    from kazma_core.spaces import identity, scope
    from playwright.sync_api import sync_playwright

    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "space-move-test-secret-" + "x" * 26
        scope.set_enforced(True)
        browser = p.chromium.launch()
        try:
            context = browser.new_context(accept_downloads=True)
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = context.new_page()
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            person = _json(pg, (
                "(name) => fetch('/api/people', {method: 'POST', credentials: 'same-origin',"
                " headers: {'Content-Type': 'application/json'},"
                " body: JSON.stringify({name: name, password: 'a-long-enough-password'})})"
                ".then(r => r.json())"), f"mover-{lang}")["person"]
            made = _json(pg, (
                "(name) => fetch('/api/people/spaces', {method: 'POST', credentials: 'same-origin',"
                " headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: name})})"
                ".then(r => r.json())"), f"Family {lang}")
            space = made["space"]["id"]
            pg.on("response", lambda r: refused.append(f"{r.status} {r.url}")
                  if r.status in (401, 403) and "/api/" in r.url else None)

            # In the shared space, by the sidebar: This space's data offers its
            # owner "Move this space".
            pg.goto(f"{h.base}/chat?space={space}", wait_until="domcontentloaded")
            pg.wait_for_function("(s) => window.kazmaSpace && window.kazmaSpace.id === s", arg=space, timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_space_data", lang)).click()
            move = pg.locator("#space-data-move")
            move.wait_for(state="visible", timeout=20000)
            assert move.inner_text().strip() == _t("settings.space_data_move", lang)
            move.click()
            title = pg.locator("#modal-title")
            title.wait_for(state="visible", timeout=10000)
            assert _plain(title.inner_text()) == _plain(_t("settings.space_data_move_title", lang, name=f"Family {lang}"))
            dialog = pg.locator(".modal", has=title)
            assert _plain(_t("settings.space_data_move_confirm", lang)) in _plain(dialog.inner_text())
            _modal_action(pg, _t("settings.space_data_move_go", lang))
            field = pg.locator(".modal input[x-model='$store.modal.inputValue']")
            field.wait_for(state="visible", timeout=10000)
            assert field.get_attribute("type") == "password"
            assert _plain(_t("settings.space_data_move_password", lang)) in _plain(dialog.inner_text())
            field.fill(PASSWORD)
            _modal_action(pg, _t("common.ok", lang))
            pg.locator(".modal", has_text=_t("settings.space_data_export_again", lang)).wait_for(
                state="visible", timeout=10000)
            field.fill(PASSWORD)
            with pg.expect_download() as download:
                _modal_action(pg, _t("common.ok", lang))
            assert download.value.suggested_filename == f"kazma-space-{space}.kazmaspace"
            bundle = download.value.path()
            raw = Path(bundle).read_bytes()
            assert raw[:8] == b"KZSEALED"  # sealed whole
            assert b"secrets.jsonl" not in raw  # no entry name in the clear

            # The owner deletes it here.
            pg.locator("#space-data-delete").click()
            with pg.expect_response(lambda r: r.request.method == "DELETE" and f"/api/spaces/{space}" in r.url) as gone:
                _modal_action(pg, _t("settings.space_data_delete", lang))
            assert gone.value.ok, gone.value.text()
            pg.wait_for_function("(s) => !window.kazmaSpace || window.kazmaSpace.id !== s", arg=space, timeout=20000)
            assert space not in identity.read_registry()["spaces"]

            # People -> Shared spaces: bring it in.
            pg.goto(f"{h.base}/chat?space=default", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            _soft_nav(pg, "/settings")
            _settings_loaded(pg)
            pg.locator(".settings-tab", has_text=_t("settings.tab_people", lang)).click()
            form = pg.locator("#people-bring-in")
            form.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_bring_in_title", lang) in pg.locator("#people-shared-spaces").inner_text()
            go = pg.locator("#people-bring-in-go")
            assert go.is_disabled()  # nothing chosen yet
            pg.set_input_files("#people-bring-in-file", bundle)
            pg.select_option("#people-bring-in-owner", person["id"])
            # A wrong password: refused in the page's language, nothing brought in.
            pg.fill("#people-bring-in-password", "not-the-password-at-all")
            with pg.expect_response(lambda r: r.url.endswith("/api/spaces/import")) as wrong:
                go.click()
            assert wrong.value.status == 400
            pg.locator(".toast", has_text=_t("settings.space_data_err.wrong_password", lang)).first.wait_for(
                state="visible", timeout=10000)
            assert space not in identity.read_registry()["spaces"]
            pg.fill("#people-bring-in-password", PASSWORD)
            with pg.expect_response(lambda r: r.url.endswith("/api/spaces/import")) as taken:
                go.click()
            assert taken.value.status == 201, taken.value.text()
            row = pg.locator(f"#people-shared-spaces [data-shared-space='{space}']")
            row.wait_for(state="visible", timeout=20000)
            owner = row.locator(f"[data-space-member='{person['id']}']")
            owner.wait_for(state="visible", timeout=20000)
            assert _t("settings.space_role_owner", lang) in owner.inner_text()
            assert identity.is_member(person["id"], space)
        finally:
            browser.close()
            scope.set_enforced(False)
    assert refused == [], refused
