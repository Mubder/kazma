"""The sidebar's logo follows the theme, its card names the build, its links
answer the pointer (2026-11-20, the owner's requests).

- The logo and the agent's avatar show one face each: the artwork on the dark
  theme, the two-tone mark on the light one; switching the theme on an open
  page swaps them with no reload (``tests/test_logo_by_theme.py`` holds the
  markup and the images).
- The card under the menu names the release, the build and when the server
  started ("0.21.2 · build 29841f9e7", "up since ..."); the chat page's
  session column no longer does.
- A menu item the pointer is over gets a backdrop, not only another text
  colour.

Reached by the sidebar, in English and Arabic.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

SHOTS = os.environ.get("KAZMA_TEST_SHOTS_DIR", "")


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _visible_marks(pg, selector: str) -> list[str]:
    return pg.evaluate(
        """(sel) => Array.from(document.querySelectorAll(sel))
            .filter(img => img.getClientRects().length > 0 && getComputedStyle(img).display !== 'none')
            .map(img => img.getAttribute('src'))""",
        selector,
    )


def _shot(pg, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_logo_build_card_and_hover(harness: Harness, lang: str) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            host = harness.base.split("//", 1)[1].split(":", 1)[0]
            context = browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
            context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
            pg = context.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{harness.base}/memory", wait_until="domcontentloaded", timeout=30000)
            pg.locator("a.nav-link[href='/chat']").first.click(force=True)
            pg.wait_for_function("location.pathname === '/chat'", timeout=15000)

            for theme in ("dark", "light"):
                pg.evaluate("(t) => window.syncDocumentColorScheme(t)", theme)
                pg.wait_for_function("(t) => document.documentElement.dataset.theme === t", arg=theme)
                logo = _visible_marks(pg, ".sidebar-header img.logo-img")
                avatar = _visible_marks(pg, ".sidebar-user img.user-avatar-img")
                welcome = _visible_marks(pg, "img.welcome-logo")
                suffix = "-dark-theme.png" if theme == "dark" else ".png"
                assert len(logo) == 1 and logo[0] == f"/static/img/kazma-logo{suffix}", (theme, logo)
                assert len(avatar) == 1 and avatar[0] == f"/static/img/kazma-icon{suffix}", (theme, avatar)
                assert len(welcome) <= 1 and all(w.endswith(f"kazma-icon{suffix}") for w in welcome), welcome
                pg.wait_for_timeout(500)  # the colours' own transition
                _shot(pg, f"sidebar-{lang}-{theme}")

            # The card names the release and the build, and when it started.
            card = pg.locator("#sidebar-build")
            assert card.is_visible()
            ident = pg.locator("#sidebar-build .sidebar-build-id").inner_text()
            assert "·" in ident and len(ident.split()) >= 3, ident
            # The version and the hash are isolates: in Arabic a hash that
            # starts with a digit joined the word before it.
            isolates = pg.locator("#sidebar-build .sidebar-build-id bdi[dir='ltr']").all_inner_texts()
            assert len(isolates) == 2 and isolates[0][:1].isdigit(), isolates
            pg.wait_for_function(
                "() => (document.querySelector('#sidebar-build .sidebar-build-since') || {}).textContent")
            since = pg.locator("#sidebar-build .sidebar-build-since").inner_text().strip()
            assert since, "no 'up since' line"
            assert any(ch.isdigit() for ch in since), since
            # Not in the chat's session column any more.
            assert pg.locator("#chat-build-badge, .chat-build-badge").count() == 0

            # A hovered menu item gets a backdrop.
            link = pg.locator("a.nav-link[href='/memory']").first
            before = link.evaluate("el => getComputedStyle(el).backgroundColor")
            link.hover()
            pg.wait_for_timeout(300)
            after = link.evaluate("el => getComputedStyle(el).backgroundColor")
            assert after not in ("rgba(0, 0, 0, 0)", "transparent"), after
            assert after != before, (before, after)
            _shot(pg, f"hover-{lang}")
            assert not errors, errors
            context.close()
        finally:
            browser.close()
