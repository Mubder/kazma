"""The Memory page names a week, a month and a project from the stored key.

A June written at midnight in Kuwait is 31 May in UTC. The page used to
format that instant in the browser's zone, so the week read "May". It now
names the week, the month and the project's first and last week from their
keys. Reached by the sidebar, in a browser whose zone would get it wrong.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import Harness, Script, unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

KUWAIT_JUNE = datetime(2026, 6, 1, tzinfo=ZoneInfo("Asia/Kuwait")).timestamp()
# A September instant, so a browser that names the project from the stored
# end says September. Week 2026-W40 ends on Sunday 4 October.
SEPTEMBER = datetime(2026, 9, 15, tzinfo=ZoneInfo("Asia/Kuwait")).timestamp()

_SUMMARIES = {
    "ok": True,
    "total": 3,
    "offset": 0,
    "state": {"weeks_done": 1},
    "summaries": [
        {"id": "w", "level": "week", "period_key": "2026-W23", "period_start": KUWAIT_JUNE,
         "period_end": KUWAIT_JUNE + 7 * 86400, "title": "garden", "summary_text": "roses",
         "status": "active", "turn_count": 4, "chat_count": 1},
        {"id": "m", "level": "month", "period_key": "2026-06", "period_start": KUWAIT_JUNE,
         "period_end": KUWAIT_JUNE + 30 * 86400, "title": "notes", "summary_text": "hot",
         "status": "active", "turn_count": 8, "chat_count": 1},
        {"id": "p", "level": "project", "period_key": "project:abcd", "span_keys": ["2026-W23", "2026-W40"],
         "period_start": KUWAIT_JUNE, "period_end": SEPTEMBER, "title": "training", "summary_text": "runs",
         "status": "active", "turn_count": 12, "chat_count": 2},
    ],
}


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _open(browser, harness: Harness, lang: str, zone: str):
    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    context = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US", timezone_id=zone)
    context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
    context.route("**/api/memory/v2/summaries*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(_SUMMARIES)))
    pg = context.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{harness.base}/", wait_until="domcontentloaded", timeout=30000)
    pg.locator("a.nav-link[href='/memory']").first.click(force=True)
    pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
    return context, pg, errors


@pytest.mark.parametrize("zone", ["UTC", "Pacific/Niue"])
@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_summary_is_named_from_its_key_not_the_browsers_zone(harness: Harness, lang: str, zone: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context, pg, errors = _open(browser, harness, lang, zone)
            pg.locator(".v2-summary-when").nth(2).wait_for(timeout=20000)
            text = "\n".join(pg.locator(".v2-summary-when").all_inner_texts())
            named = pg.evaluate(
                """(ts) => {
                  const fmt = window.KazmaFormat;
                  const instant = new Intl.DateTimeFormat(fmt.locale(), { month: 'long', year: 'numeric' })
                    .format(new Date(ts * 1000));
                  return {
                    week: fmt.weekOfKey('2026-W23'),
                    month: fmt.monthOfKey('2026-06'),
                    from: fmt.monthOfKey('2026-W23'),
                    to: fmt.monthOfKey('2026-W40', 'end'),
                    instant: instant,
                  };
                }""",
                KUWAIT_JUNE,
            )
            assert named["week"] and named["week"] in text, (named, text)
            assert named["month"] in text and named["from"] in text and named["to"] in text, (named, text)
            # The browser's own zone reads the stored June instant as May.
            assert named["instant"] not in text, (named, text)
            levels = pg.locator(".v2-summary-when").evaluate_all("els => els.map(e => e.getAttribute('data-level'))")
            assert levels == ["week", "month", "project"], levels
            assert errors == [], errors
            context.close()
        finally:
            browser.close()
