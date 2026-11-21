"""Daily limits on People, in a real browser (Spaces S2-02), English and Arabic.

The admin reaches Settings by the sidebar and opens People. A person who
used 150 tokens and $0.50 today is listed with that beside the limit for
people (Kazma's own). The admin sets the limit for people, then the
person's own limit through the row's editor, and takes it away again; each
change is what the server stores, and the page says it in the page's
language.

The server half (the refusal, the per-person count, the routes) is
``tests/test_person_daily_limit.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

NAME = "dana-limit"


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


def _person(pg) -> dict:
    return pg.evaluate("(name) => fetch('/api/people').then(r => r.json())"
                       ".then(d => d.people.find(p => p.name === name))", NAME)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_admin_sets_daily_limits_on_people(lang):
    from kazma_core.observability import llm_ledger
    from kazma_core.spaces import daily_limits, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    refused: list[str] = []
    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "people-limit-test-secret-" + "x" * 24
        scope.set_enforced(True)
        person = people.create_person(NAME, "a-long-enough-password", by=OWNER_ACCOUNT_ID)
        with scope.bind_scope(person["personal_space"], person["id"]):
            llm_ledger.record_llm_call(model="m", prompt_tokens=150, cost_usd=0.5)
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
            group = pg.locator("#people-limits")
            group.wait_for(state="visible", timeout=20000)
            assert _t("settings.people_limits_title", lang) in group.inner_text()

            # Today's use beside the limit for people.
            spent = pg.locator(f"[data-spent='{person['id']}']")
            spent.wait_for(state="visible", timeout=20000)
            assert "150" in spent.inner_text()
            limit = pg.locator(f"[data-limit='{person['id']}']")
            assert _t("settings.people_limit_from_default", lang) in limit.inner_text()
            assert pg.input_value("#people-default-tokens") == str(daily_limits.SHIPPED_DEFAULT.tokens)

            # The limit for people.
            pg.fill("#people-default-tokens", "5000")
            pg.fill("#people-default-usd", "2")
            with pg.expect_response(lambda r: r.url.endswith("/api/people/default-limit")) as saved:
                pg.click("#people-default-save")
            assert saved.value.ok, saved.value.text()
            pg.wait_for_function("() => document.querySelector('#people-default-shipped')"
                                 " && getComputedStyle(document.querySelector('#people-default-shipped')).display !== 'none'",
                                 timeout=20000)
            assert pg.evaluate("() => fetch('/api/people').then(r => r.json()).then(d => d.limit_for_people)")[
                "limit"] == {"tokens": 5000, "usd": 2.0}

            # The person's own limit, through the row's editor.
            row = pg.locator(f"[data-person-limit='{person['id']}']")
            row.locator("button", has_text=_t("settings.people_limit_edit", lang)).click()
            editor = pg.locator(f"[data-limit-editor='{person['id']}']")
            editor.wait_for(state="visible", timeout=10000)
            tokens = editor.get_by_label(_t("settings.people_limit_tokens_of", lang, name=NAME))
            tokens.fill("900")
            editor.get_by_label(_t("settings.people_limit_usd_of", lang, name=NAME)).fill("0")
            with pg.expect_response(lambda r: r.url.endswith("/daily-limit")) as own:
                editor.locator("button", has_text=_t("settings.people_limit_save", lang)).click()
            assert own.value.ok, own.value.text()
            editor.wait_for(state="hidden", timeout=10000)
            pg.wait_for_function(
                "(args) => (document.querySelector(\"[data-limit='\" + args[0] + \"']\") || {}).innerText"
                "?.includes(args[1])", arg=[person["id"], _t("settings.people_limit_own", lang)], timeout=20000)
            assert "900" in limit.inner_text()
            stored = _person(pg)
            assert stored["limit"] == {"tokens": 900, "usd": 0.0} and stored["limit_source"] == "own"

            # Taken away: the limit for people again.
            row.locator("button", has_text=_t("settings.people_limit_edit", lang)).click()
            editor.wait_for(state="visible", timeout=10000)
            with pg.expect_response(lambda r: r.url.endswith("/daily-limit")) as cleared:
                editor.locator("button", has_text=_t("settings.people_limit_clear", lang)).click()
            assert cleared.value.ok
            pg.wait_for_function(
                "(args) => (document.querySelector(\"[data-limit='\" + args[0] + \"']\") || {}).innerText"
                "?.includes(args[1])", arg=[person["id"], _t("settings.people_limit_from_default", lang)],
                timeout=20000)
            assert _person(pg)["limit"] == {"tokens": 5000, "usd": 2.0}
        finally:
            browser.close()
            scope.set_enforced(False)
    assert refused == [], refused
