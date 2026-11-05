"""The header's menus open on top of the page, inside the window, on every page.

Live 2026-11-05: on the Chat page the notification bell's list opened BEHIND
the chat. The header blurs what is under it (``backdrop-filter``), which makes
it its own stacking context at the page's base level, so its menus (z-index
200) could rise only inside the header: the chat panels painted after it
(``.chat-container`` is positioned, ``.chat-sidebar`` has z-index 35) covered
them. The header now sits on a layer of its own above the page body.

Each page is reached the way a person reaches it, by the sidebar (a soft
navigation), in English and in Arabic, and each header menu -- the bell and
the account menu -- is opened and probed: every sampled point of the open list
must be the list itself (``elementFromPoint``) and the list must lie inside the
window. The first control puts the header back on the base level and the Chat
page's bell list is covered again. The menus also hung from their button at
``right: 0``: in Arabic on a phone, where the header wraps its buttons, the
bell list opened past the window's left edge. A menu now opens from its
button's inline end (``inset-inline-end``), and on a narrow window from the
header's own end; the second control puts the old anchor back.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: A desktop window and a phone (the mobile layout: drawer, bottom navigation).
VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "phone": {"width": 390, "height": 844},
}

#: Both menus close on a click outside them (``@click.outside``); a click on
#: the body is outside, and on a phone a click by position could land on the
#: bottom navigation and leave the page.
_CLOSE_JS = "() => document.body.dispatchEvent(new MouseEvent('click', {bubbles: true}))"

#: The header's two menus: the button that opens each and the list it opens.
MENUS = {
    "bell": (".page-header .user-menu > .header-btn", ".page-header .notif-dropdown"),
    "account": (".page-header .user-menu-btn", ".page-header .user-menu-dropdown:not(.notif-dropdown)"),
}

#: The sidebar's own pages.
_NAV_JS = r"""() => Array.from(new Set(Array.from(
  document.querySelectorAll('.sidebar .nav-links a[href^="/"]'))
  .map((a) => a.getAttribute('href'))))
  .filter((h) => h.length > 1 && !h.includes('?') && !h.includes('#'))"""

#: Where the open list is, and what the page shows at points spread over it.
_PROBE_JS = r"""(sel) => {
  const menu = document.querySelector(sel);
  if (!menu) return { missing: true };
  const r = menu.getBoundingClientRect();
  const covered = [];
  const off = [];
  for (let i = 0; i < 5; i++) {
    for (let j = 0; j < 5; j++) {
      const x = r.left + 4 + (r.width - 8) * i / 4;
      const y = r.top + 4 + (r.height - 8) * j / 4;
      if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) { off.push([x | 0, y | 0]); continue; }
      const hit = document.elementFromPoint(x, y);
      if (!hit || !menu.contains(hit)) {
        const who = hit ? (hit.tagName.toLowerCase() + (hit.id ? '#' + hit.id : '')
          + (typeof hit.className === 'string' && hit.className ? '.' + hit.className.trim().split(/\s+/).join('.') : '')) : 'nothing';
        covered.push(who);
      }
    }
  }
  return { width: r.width, height: r.height, left: r.left, right: r.right,
           covered: Array.from(new Set(covered)), off: off.length, viewport: innerWidth };
}"""


@pytest.fixture(scope="module")
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def _soft_nav(pg, href: str) -> None:
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()",
        href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)
    pg.wait_for_function("() => !document.body.classList.contains('kazma-soft-nav')", timeout=20000)
    pg.wait_for_timeout(600)


def _open_and_probe(pg, button: str, menu: str) -> dict:
    pg.evaluate(_CLOSE_JS)  # close any open menu
    pg.wait_for_timeout(150)
    pg.click(button)
    pg.wait_for_selector(menu, state="visible", timeout=5000)
    pg.wait_for_timeout(300)  # the open animation
    found = pg.evaluate(_PROBE_JS, menu)
    pg.evaluate(_CLOSE_JS)
    pg.wait_for_selector(menu, state="hidden", timeout=5000)
    return found


def _browser_page(p, harness: Harness, lang: str, size: str = "desktop"):
    from urllib.parse import urlparse

    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport=VIEWPORTS[size])
    host = urlparse(harness.base).hostname
    context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
    pg = context.new_page()
    pg.goto(f"{harness.base}/dashboard", wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_function("() => !!window.Alpine", timeout=30000)
    pg.wait_for_timeout(1000)
    return browser, pg


@pytest.mark.parametrize("size", list(VIEWPORTS))
@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_header_menus_open_on_top_of_every_page(harness: Harness, lang: str, size: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, pg = _browser_page(p, harness, lang, size)
        try:
            assert pg.evaluate("() => document.documentElement.dir") == ("rtl" if lang == "ar" else "ltr")
            pages = pg.evaluate(_NAV_JS)
            assert "/chat" in pages, pages
            problems = []
            for href in pages:
                _soft_nav(pg, href)
                for name, (button, menu) in MENUS.items():
                    found = _open_and_probe(pg, button, menu)
                    if found.get("missing") or found["covered"] or found["off"]:
                        problems.append((href, name, found))
            assert problems == [], problems
        finally:
            browser.close()


def test_control_on_the_base_level_the_chat_covers_the_bell_list(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, pg = _browser_page(p, harness, "en")
        try:
            _soft_nav(pg, "/chat")
            pg.add_style_tag(content=".page-header { position: static !important; z-index: auto !important; }")
            found = _open_and_probe(pg, *MENUS["bell"])
            assert found["covered"], f"control: the old header should leave the list covered: {found}"
        finally:
            browser.close()


def test_control_anchored_on_the_right_an_arabic_phone_menu_leaves_the_window(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, pg = _browser_page(p, harness, "ar", "phone")
        try:
            _soft_nav(pg, "/chat")
            pg.add_style_tag(content=(
                ".page-header .user-menu { position: relative !important; }"
                ".page-header .user-menu-dropdown { inset-inline-end: auto !important; right: 0 !important; }"))
            found = _open_and_probe(pg, *MENUS["bell"])
            assert found["off"], f"control: the old anchor should open the menu past the window's edge: {found}"
        finally:
            browser.close()
