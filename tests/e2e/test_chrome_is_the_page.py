"""Only a card is the card colour: chrome columns and bands are the page's,
and the header is set off from the page by a line along its bottom edge.

Since 2026-11-17 a card is one step above the page (AGENTS.md, "One colour
system"). The chat page's session list still read ``--bg-panel`` -- the card
colour -- so it showed as a lighter column in the page, close to the page
colour before the change, while the main menu beside it was the page. This
loads every page the navigation links to and every Settings tab, at desktop
and phone width, in both themes, and fails on an area painted the card
colour that is neither a card (the card shadow, or a ``.card-nested`` with
its hairline) nor inside one, when it is a column (taller than 45% of the
viewport, narrower than half) or a band across the page. The negative
control puts the old session-list rule back and must be caught.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import Harness, unified_turn_server  # noqa: E402
from tests.e2e.test_pages_load_clean import _NAV_JS, _SETTINGS_TABS_JS, _settle  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: Card-coloured columns and bands that are not cards, nor inside one.
_SCAN_JS = r"""() => {
  const probe = document.createElement('div');
  probe.style.background = 'var(--surface-card)';
  probe.style.boxShadow = 'var(--card-shadow)';
  document.body.appendChild(probe);
  const card = getComputedStyle(probe).backgroundColor;
  const cardShadow = getComputedStyle(probe).boxShadow;
  probe.remove();
  const isCard = (el) => {
    for (let n = el; n && n !== document.body; n = n.parentElement) {
      if (n.classList.contains('card-nested')) return true;
      if (getComputedStyle(n).boxShadow === cardShadow) return true;
    }
    return false;
  };
  const name = (el) => {
    const id = el.id ? '#' + el.id : '';
    const cls = (typeof el.className === 'string' && el.className.trim())
      ? '.' + el.className.trim().split(/\s+/).slice(0, 3).join('.') : '';
    return el.tagName.toLowerCase() + id + cls;
  };
  const vw = innerWidth, vh = innerHeight, out = [];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.backgroundColor !== card || cs.display === 'none' || cs.visibility === 'hidden') continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    const column = r.height > 0.45 * vh && r.width < 0.5 * vw;
    const band = r.width > 0.6 * vw && r.height > 40;
    if ((column || band) && !isCard(el)) out.push(name(el));
  }
  return out;
}"""


#: The header's bottom edge: a visible line, or why not ('' when it has one).
_HEADER_EDGE_JS = r"""() => {
  const h = document.querySelector('.page-header');
  if (!h || getComputedStyle(h).display === 'none' || !h.getBoundingClientRect().height) return '';
  const cs = getComputedStyle(h);
  const width = parseFloat(cs.borderBottomWidth) || 0;
  const color = cs.borderBottomColor;
  const clear = /rgba\([^)]*,\s*0\)$/.test(color) || color === 'transparent';
  if (width < 1 || cs.borderBottomStyle === 'none' || clear || color === cs.backgroundColor) {
    return `width ${cs.borderBottomWidth}, style ${cs.borderBottomStyle}, colour ${color}`;
  }
  return '';
}"""


def chrome_problems(pg, label: str) -> list[str]:
    found = [f"{label}: {el} is the card colour but no card" for el in pg.evaluate(_SCAN_JS)]
    edge = pg.evaluate(_HEADER_EDGE_JS)
    if edge:
        found.append(f"{label}: the header has no line along its bottom edge ({edge})")
    return found


@pytest.fixture(scope="module")
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def test_only_cards_are_the_card_colour(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for width, height, tag in ((1280, 900, "desktop"), (390, 844, "phone")):
                ctx = browser.new_context(viewport={"width": width, "height": height},
                                          is_mobile=tag == "phone", has_touch=tag == "phone")
                first = ctx.new_page()
                first.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
                _settle(first)
                pages = first.evaluate(_NAV_JS)
                first.goto(f"{harness.base}/settings", wait_until="domcontentloaded", timeout=30000)
                _settle(first)
                tabs = first.evaluate(_SETTINGS_TABS_JS)
                first.close()
                assert "/chat" in pages and len(tabs) >= 10, (pages, tabs)
                for path in sorted(set(pages)) + [f"/settings?tab={t}" for t in tabs]:
                    pg = ctx.new_page()
                    pg.goto(f"{harness.base}{path}", wait_until="domcontentloaded", timeout=30000)
                    _settle(pg)
                    for theme in ("dark", "light"):
                        pg.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
                        # The theme's colours have settled (no colour transition running; an
                        # endless animation such as a pulse is not waited for).
                        pg.wait_for_function(
                            "() => document.getAnimations().every((a) => !(a instanceof CSSTransition) || a.playState !== 'running')",
                            timeout=10000)
                        problems += chrome_problems(pg, f"{tag} {theme} {path}")
                    pg.close()
                ctx.close()
        finally:
            browser.close()
    assert not problems, "\n".join(problems)


#: What the reader touches at points down a drawer's middle: the drawer, or
#: the element in front of it.
_COVERED_JS = r"""(sel) => {
  const d = document.querySelector(sel);
  const r = d.getBoundingClientRect();
  const out = [];
  for (const f of [0.25, 0.5, 0.75, 0.9]) {
    const el = document.elementFromPoint(r.left + r.width / 2, r.top + f * r.height);
    if (!el || d.contains(el)) continue;
    const front = el.closest('.chat-input-area, .chat-input-row, .bottom-nav') || el;
    out.push(`${Math.round(f * 100)}%: ${front.tagName.toLowerCase()}.${String(front.className).split(' ')[0]}`);
  }
  return out;
}"""

#: Each phone drawer on the chat page, opened by the control a person taps.
_DRAWERS = (
    ("aside.chat-sidebar", "button.chat-sessions-btn"),
    ("aside.sidebar", "button.bottom-nav-more"),
)


def drawer_problems(pg, base: str) -> list[str]:
    found = []
    for drawer, opener in _DRAWERS:
        pg.goto(f"{base}/chat", wait_until="domcontentloaded", timeout=30000)
        _settle(pg)
        pg.locator(opener).first.click()
        # Open: the drawer has slid fully into view (its transform is done).
        pg.wait_for_function(
            """(sel) => { const r = document.querySelector(sel).getBoundingClientRect();
                return r.width > 0 && r.left >= -0.5 && r.right <= innerWidth + 0.5; }""",
            arg=drawer, timeout=10000)
        found += [f"{drawer}: covered at {x}" for x in pg.evaluate(_COVERED_JS, drawer)]
    return found


def test_phone_drawers_are_above_the_composer(harness: Harness) -> None:
    """On a phone the chat composer (sticky) sat above both drawers and their
    backdrop: the session list's footer was hidden behind it, and the
    composer stayed usable over the dimmed page."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            pg = ctx.new_page()
            assert not drawer_problems(pg, harness.base)
            ctx.close()
            # Negative control: the old stacking.
            ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            ctx.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
                const s = document.createElement('style');
                s.textContent = '.sidebar, .chat-sidebar { z-index: 200 !important; } .sidebar-backdrop { z-index: 199 !important; }';
                document.head.appendChild(s); });""")
            pg = ctx.new_page()
            old = drawer_problems(pg, harness.base)
            for drawer, _ in _DRAWERS:
                assert any(x.startswith(f"{drawer}: covered") and "chat-input-area" in x for x in old), old
            ctx.close()
        finally:
            browser.close()


def test_negative_control_a_header_with_no_edge_is_caught(harness: Harness) -> None:
    """The header as it was: transparent, with no border, running into the page."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page(viewport={"width": 1280, "height": 900})
            pg.goto(f"{harness.base}/dashboard", wait_until="domcontentloaded", timeout=30000)
            _settle(pg)
            assert not chrome_problems(pg, "fixed")
            pg.add_style_tag(content=".page-header { background: transparent; border-bottom: 0; }")
            pg.wait_for_function(
                "() => getComputedStyle(document.querySelector('.page-header')).borderBottomStyle === 'none'",
                timeout=10000)
            assert any("the header has no line" in x for x in chrome_problems(pg, "old"))
        finally:
            browser.close()


def test_negative_control_the_old_session_list_is_caught(harness: Harness) -> None:
    """The session list as it was: the card colour, with no card shadow."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page(viewport={"width": 1280, "height": 900})
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            _settle(pg)
            assert not chrome_problems(pg, "fixed")
            pg.add_style_tag(content=".chat-sidebar { background: var(--bg-panel); }")
            pg.wait_for_function(
                """() => { const p = document.createElement('div');
                    p.style.background = 'var(--surface-card)'; document.body.appendChild(p);
                    const card = getComputedStyle(p).backgroundColor; p.remove();
                    return getComputedStyle(document.querySelector('.chat-sidebar')).backgroundColor === card; }""",
                timeout=10000)
            assert any("aside.chat-sidebar" in x for x in chrome_problems(pg, "old"))
        finally:
            browser.close()
