"""An open tab is told when Kazma was updated, and Refresh loads the new build.

The owner's request (2026-10-16): a fix to the chat page reached the server,
but a tab opened before the deploy kept running the old page code.
``modules/build_watch.js`` compares the build that rendered the page
(``<meta name="kazma-build">``) with ``/health/live``; here ``/health/live``
is answered with a newer build, as a deploy would, in English and Arabic.
Unit test of the module: ``tests/js/test_build_watch.js``.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

TOAST = ".toast-container .toast"


def _soft_nav(pg, href: str) -> None:
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _newer_build(route) -> None:
    route.fulfill(status=200, content_type="application/json",
                  body=json.dumps({"status": "alive", "build": {"commit": "f00dbabe", "started_at": 1}}))


@pytest.mark.parametrize("lang,message,button", [
    ("en", "Kazma was updated. Refresh to load the new version.", "Refresh"),
    ("ar", "تم تحديث كاظمه. حدّث الصفحة لتحميل الإصدار الجديد.", "تحديث"),
])
def test_a_tab_hears_of_a_newer_build_and_refresh_loads_it(lang, message, button):
    from playwright.sync_api import sync_playwright

    with unified_turn_server() as h, sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            ctx = browser.new_context()
            ctx.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = ctx.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{h.base}/chat", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.KazmaBuildWatch && !!window.Alpine", timeout=30000)

            page_build = pg.evaluate(
                "() => document.querySelector('meta[name=\"kazma-build\"]').content")
            assert page_build and page_build != "unknown", page_build

            # Control: the server runs the build that rendered the page.
            assert pg.evaluate("() => window.KazmaBuildWatch.check()") is False
            assert pg.locator(TOAST).count() == 0

            # A deploy: the server now reports another build. The watcher is
            # the shell's, so it still answers after a sidebar navigation.
            _soft_nav(pg, "/memory")
            pg.route("**/health/live", _newer_build)
            assert pg.evaluate("() => window.KazmaBuildWatch.check()") is True
            toast = pg.locator(TOAST)
            toast.wait_for(state="visible", timeout=10000)
            assert toast.count() == 1
            assert message in toast.inner_text()
            refresh = toast.locator(".toast-action")
            assert refresh.inner_text().strip() == button
            # Once per tab.
            assert pg.evaluate("() => window.KazmaBuildWatch.check()") is False
            assert pg.locator(TOAST).count() == 1

            # Refresh loads the page again.
            pg.evaluate("() => { window.__beforeRefresh = true; }")
            with pg.expect_navigation(timeout=20000):
                refresh.click()
            pg.wait_for_function("() => !!window.KazmaBuildWatch", timeout=30000)
            assert pg.evaluate("() => window.__beforeRefresh === undefined")
            assert errors == [], errors
        finally:
            browser.close()
