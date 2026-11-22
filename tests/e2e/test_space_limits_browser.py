"""Limits per space on People, in a real browser (Spaces S2-08), English and
Arabic.

The admin reaches Settings by the sidebar and opens People. A person's
personal space whose folder holds 3 MiB is listed with that beside its files
limit (Kazma's own). The admin sets the limits for spaces, then the space's
own limits through the row's editor (only the fields typed are its own; the
rest stay the limits for spaces), and takes them away again; each change is
what the server stores, and the page says it in the page's language.

The server half (the shares, the byte limits, the routes) is
``tests/test_space_quotas.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

NAME = "dana-space-limits"
MIB = 1024 * 1024


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


def _usage(pg) -> dict:
    return pg.evaluate("() => fetch('/api/people/spaces/usage').then(r => r.json())")


def _row_says(pg, space: str, name: str, text: str) -> None:
    pg.wait_for_function(
        "(args) => (document.querySelector(\"[data-space-used='\" + args[0] + \"']\") || {}).innerText"
        "?.includes(args[1])", arg=[f"{space}:{name}", text], timeout=20000)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_sets_limits_per_space_on_people(lang):
    from kazma_core.spaces import people, scope, space_limits
    from kazma_core.spaces.files import space_dir
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "space-limits-test-secret-" + "x" * 24
        scope.set_enforced(True)
        person = people.create_person(NAME, "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        space = person["personal_space"]
        files = space_dir(space) / "files"
        files.mkdir(parents=True, exist_ok=True)
        (files / "big.bin").write_bytes(b"x" * (3 * MIB))
        space_limits._sizes.clear()
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
            group = pg.locator("#people-space-usage")
            group.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_space_limits_title", lang) in group.inner_text()

            # The space's folder beside its files limit (Kazma's own).
            shown = pg.evaluate("(n) => [window.KazmaFormat.number(3, {maximumFractionDigits: 1}),"
                                " window.KazmaFormat.number(n, {maximumFractionDigits: 0})]",
                                space_limits.SHIPPED.disk_bytes // MIB)
            mib = [_t("settings.people_space_mib", lang, size=n) for n in shown]
            _row_says(pg, space, "disk_bytes",
                      _t("settings.people_space_usage_of", lang, used=mib[0], limit=mib[1]))
            # The owner's space keeps the install's folders.
            _row_says(pg, "default", "disk_bytes", _t("settings.people_space_usage_install_files", lang))
            assert pg.input_value("#space-limits-default-swarm") == str(space_limits.SHIPPED.swarm)

            # The limits for spaces.
            pg.fill("#space-limits-default-swarm", "3")
            with pg.expect_response(lambda r: r.url.endswith("/api/people/space-limits")) as saved:
                pg.click("#space-limits-default-save")
            assert saved.value.ok, saved.value.text()
            pg.locator("#space-limits-default-shipped").wait_for(state="visible", timeout=20000)
            assert _usage(pg)["limits_for_spaces"]["swarm"] == 3
            _row_says(pg, space, "swarm", _t("settings.people_space_usage_of", lang, used="0", limit="3"))

            # The space's own limits, through the row's editor: only what is typed.
            row = pg.locator(f"[data-space-usage='{space}']")
            row.get_by_label(_t("settings.people_space_limits_edit_of", lang, name=NAME)).click()
            editor = pg.locator(f"[data-space-limits-editor='{space}']")
            editor.wait_for(state="visible", timeout=10000)
            swarm = editor.locator("[data-space-limit-input='swarm']")
            assert swarm.input_value() == "" and swarm.get_attribute("placeholder") == "3"
            swarm.fill("4")
            editor.locator("[data-space-limit-input='disk_bytes']").fill("2")
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/spaces/{space}/limits")) as own:
                editor.locator("button", has_text=_t("settings.people_limit_save", lang)).click()
            assert own.value.ok, own.value.text()
            editor.wait_for(state="hidden", timeout=10000)
            _row_says(pg, space, "swarm", _t("settings.people_space_usage_of", lang, used="0", limit="4"))
            stored = {s["id"]: s for s in _usage(pg)["spaces"]}[space]
            assert stored["own_limits"] == {"swarm": 4, "disk_bytes": 2 * MIB}
            assert stored["limits"]["crawls"] == space_limits.SHIPPED.crawls  # not pinned

            # Taken away: the limits for spaces again.
            row.get_by_label(_t("settings.people_space_limits_edit_of", lang, name=NAME)).click()
            editor.wait_for(state="visible", timeout=10000)
            with pg.expect_response(lambda r: r.url.endswith(f"/api/people/spaces/{space}/limits")) as cleared:
                editor.locator("button", has_text=_t("settings.people_space_limits_clear", lang)).click()
            assert cleared.value.ok
            _row_says(pg, space, "swarm", _t("settings.people_space_usage_of", lang, used="0", limit="3"))
            assert {s["id"]: s for s in _usage(pg)["spaces"]}[space]["own_limits"] == {}
        finally:
            browser.close()
            scope.set_enforced(False)
            (files / "big.bin").unlink(missing_ok=True)
    assert refused == [], refused
