"""A page reached by the sidebar shows its icons, as a page loaded does (2026-11-09).

``icons.js`` fills every ``[data-icon]`` span once, at DOM ready, and soft
navigation (``modules/nav.js``) swaps a new page's body in without a load:
its static icons are new nodes nobody filled. Found by the Workspace page's
refresh agent during the UI refresh: reached by the sidebar, 32 of the
Workspace's icons were blank (dialog close buttons, card-title icons), and
0 were on a direct load. ``nav.js`` now hydrates the swapped-in body and the
header title after the swap, and again after Alpine renders the page.

Each page is loaded directly and then reached by the sidebar, in English and
in Arabic; a sidebar visit may leave no more icons blank than a load does.
The negative control serves the old ``nav.js`` (its hydrate a no-op) and the
Workspace's icons are blank again.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

REPO = Path(__file__).resolve().parents[2]
NAV_JS = REPO / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules" / "nav.js"

#: Pages with static icons in their markup (Workspace had 32).
PAGES = ["/workspace", "/settings", "/swarm", "/memory", "/ide"]

#: [data-icon] spans in the page body and the header that hold no icon.
_BLANK_JS = r"""() => Array.from(document.querySelectorAll('.page-body [data-icon], .page-header [data-icon]'))
  .filter((el) => !el.querySelector('svg')).length"""


@pytest.fixture(scope="module")
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def _open(p, harness: Harness, lang: str, nav_source: str | None = None):
    from urllib.parse import urlparse

    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    context.add_cookies([{"name": "kazma-lang", "value": lang,
                          "domain": urlparse(harness.base).hostname, "path": "/"}])
    if nav_source is not None:
        context.route("**/static/js/modules/nav.js*", lambda route: route.fulfill(
            status=200, content_type="application/javascript", body=nav_source))
    pg = context.new_page()
    return browser, pg


def _settle(pg) -> None:
    pg.wait_for_function("() => !!window.Alpine", timeout=30000)
    pg.wait_for_timeout(1200)


def _blank_after_load(pg, harness: Harness, href: str) -> int:
    pg.goto(harness.base + href, wait_until="domcontentloaded", timeout=30000)
    _settle(pg)
    return pg.evaluate(_BLANK_JS)


def _blank_after_sidebar(pg, href: str) -> int:
    pg.evaluate("(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)
    pg.wait_for_function("() => !document.body.classList.contains('kazma-soft-nav')", timeout=20000)
    pg.wait_for_timeout(1200)
    return pg.evaluate(_BLANK_JS)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_page_reached_by_the_sidebar_shows_its_icons(harness: Harness, lang: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, pg = _open(p, harness, lang)
        try:
            loaded = {href: _blank_after_load(pg, harness, href) for href in PAGES}
            pg.goto(harness.base + "/dashboard", wait_until="domcontentloaded", timeout=30000)
            _settle(pg)
            problems = []
            for href in PAGES:
                blank = _blank_after_sidebar(pg, href)
                if blank > loaded[href]:
                    problems.append((href, f"{blank} blank by the sidebar, {loaded[href]} on a load"))
            assert problems == [], problems
        finally:
            browser.close()


def test_control_without_the_hydrate_the_workspace_icons_are_blank(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    source = NAV_JS.read_text(encoding="utf-8")
    marker = "export function hydrateIcons(root) {"
    assert marker in source, "the hydrate moved; update the control"
    old = source.replace(marker, marker + "\n    return;", 1)
    with sync_playwright() as p:
        browser, pg = _open(p, harness, "en", nav_source=old)
        try:
            loaded = _blank_after_load(pg, harness, "/workspace")
            pg.goto(harness.base + "/dashboard", wait_until="domcontentloaded", timeout=30000)
            _settle(pg)
            blank = _blank_after_sidebar(pg, "/workspace")
            assert blank > loaded, f"control: the old soft navigation should leave icons blank ({blank} vs {loaded})"
        finally:
            browser.close()
