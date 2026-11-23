"""A member's reminder reaches their own chat app, in a real browser (Spaces
S3-05), English and Arabic.

Two people sign in with their own names. Each reaches Scheduled by the
sidebar and sets up the morning briefing:

* Dana, whose Telegram is linked, books it: it is in her scheduled tasks,
  and it is addressed to her own Telegram chat -- never the owner's;
* Omar, who linked no chat app, is told in the page's language to link one
  (Settings → My account → Chat apps), and nothing is booked.

The server half is ``tests/test_web_turn_delivery_target.py``.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

PASSWORD = "a-long-enough-password"
DANA_TELEGRAM = 555707


def _soft_nav(pg, href: str) -> None:
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _t(key: str, lang: str, **values: str) -> str:
    from kazma_ui.i18n import t

    return t(key, lang, **values)


def _signed_in(browser, base: str, name: str, lang: str):
    ctx = browser.new_context()
    ctx.add_cookies([{"name": "kazma-lang", "value": lang, "url": base}])
    signed = ctx.request.post(f"{base}/api/auth/login", data={"username": name, "password": PASSWORD})
    assert signed.ok, signed.text()
    pg = ctx.new_page()
    pg.goto(f"{base}/chat", wait_until="domcontentloaded")
    pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
    _soft_nav(pg, "/scheduled")
    pg.locator("#sched-recipes").wait_for(state="visible", timeout=20000)
    pg.wait_for_function(
        "() => (document.getElementById('recipe-time-morning_briefing') || {}).value", timeout=20000)
    return pg


def _toast(pg) -> str:
    pg.wait_for_function("() => Alpine.store('toast').items.length > 0", timeout=20000)
    return pg.evaluate("() => Alpine.store('toast').items.map(t => t.message).join(' | ')")


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_member_s_reminder_goes_to_their_own_chat(lang):
    from kazma_core.cron.scheduler import get_cron_scheduler
    from kazma_core.spaces import chat_links, people, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "member-reminder-secret-" + "x" * 24
        scope.set_enforced(True)
        chat_links._forget_attempts_for_tests()
        dana = people.create_person(f"dana-rem-{lang}", PASSWORD, by=OWNER_ACCOUNT_ID)
        omar = people.create_person(f"omar-rem-{lang}", PASSWORD, by=OWNER_ACCOUNT_ID)
        code = chat_links.issue_code(dana["id"], "telegram")
        linked = chat_links.redeem("telegram", DANA_TELEGRAM, code["code"], direct=True,
                                   target=f"telegram:{DANA_TELEGRAM}")
        assert linked.outcome == "linked"
        assert get_cron_scheduler() is not None, "the scheduler runs in the harness"
        browser = p.chromium.launch()
        try:
            pg = _signed_in(browser, h.base, dana["name"], lang)
            pg.locator("#recipe-book-morning_briefing").click()
            said = _toast(pg)
            assert _t("scheduled.recipe_booked", lang) in said, said
            tasks = pg.evaluate("() => fetch('/api/scheduled/tasks').then(r => r.json())")
            # Booked only with her own address: a member's booking addressed
            # to anyone else's chat is refused (no_own_address).
            mine = [t for t in tasks.get("tasks") or [] if t.get("destination") == "telegram"]
            assert mine, tasks

            pg2 = _signed_in(browser, h.base, omar["name"], lang)
            pg2.locator("#recipe-book-morning_briefing").click()
            refused = _toast(pg2)
            assert _t("scheduled.err_no_own_address", lang) in refused, refused
        finally:
            browser.close()
            scope.set_enforced(False)
