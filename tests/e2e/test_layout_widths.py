"""Panels lay out by the width they have, not the window's (2026-09-28).

On the live install at a 918-pixel window with the sidebar open, the IDE left
its editor about 40 pixels between the file tree and the AI chat, and the
Settings providers panel overflowed and clipped its detail pane: both
switched layouts on the WINDOW's width while the sidebar took 220 of it.
Container queries now decide on each panel's own width. Each check has a
control: with the container switched off, the old layout comes back.
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

VIEWPORT = {"width": 918, "height": 800}
NO_CONTAINER = "{ container-type: normal !important; }"


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def _page(p, harness: Harness, path: str):
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport=VIEWPORT)
    pg = context.new_page()
    pg.goto(f"{harness.base}{path}", wait_until="domcontentloaded", timeout=30000)
    return browser, context, pg


def _editor_width(pg) -> float:
    return pg.evaluate("() => document.querySelector('.ide-main').getBoundingClientRect().width")


def test_the_ide_editor_keeps_its_room(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, context, pg = _page(p, harness, "/ide")
        try:
            pg.wait_for_selector(".ide-main", timeout=30000)
            pg.evaluate("() => { const el = document.querySelector('[x-data=\"ideApp()\"]');"
                        " if (el && el._x_dataStack) el._x_dataStack[0].chatOpen = true; }")
            pg.wait_for_function("() => document.querySelector('.ide-layout.with-chat') !== null",
                                 timeout=10000)
            width = _editor_width(pg)
            assert width >= 350, f"the editor got {width:.0f}px"
            chat = pg.evaluate("() => document.querySelector('.ide-chat').getBoundingClientRect().width")
            assert chat >= 300, f"the chat got {chat:.0f}px"
            # The file tree stays beside the editor, which starts on the
            # first screen: the first fix stacked everything at this width
            # and the uncapped tree pushed the editor 2,400px down (live).
            box = pg.evaluate(
                "() => { const t = document.querySelector('.ide-tree').getBoundingClientRect();"
                " const m = document.querySelector('.ide-main').getBoundingClientRect();"
                " return {treeRight: t.right, editorLeft: m.left, editorTop: m.top}; }"
            )
            assert box["treeRight"] <= box["editorLeft"] + 1, box
            assert box["editorTop"] < VIEWPORT["height"], box

            # Control: without the container, the window-width rules squeeze it.
            pg.add_style_tag(content=".ide-container " + NO_CONTAINER)
            squeezed = _editor_width(pg)
            assert squeezed < 200, f"control: the old layout left the editor {squeezed:.0f}px"
        finally:
            context.close()
            browser.close()


def test_the_providers_panel_does_not_overflow(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    overflow = "() => { const g = document.querySelector('.pc-grid');" \
               " return g.scrollWidth - g.clientWidth; }"
    with sync_playwright() as p:
        browser, context, pg = _page(p, harness, "/settings")
        try:
            pg.wait_for_selector(".pc-grid", state="attached", timeout=30000)
            pg.wait_for_function("() => document.querySelector('.pc-grid').clientWidth > 0",
                                 timeout=15000)
            assert pg.evaluate(overflow) <= 1

            pg.add_style_tag(content=".pc-scope " + NO_CONTAINER)
            assert pg.evaluate(overflow) > 1, "control: the old layout overflowed"
        finally:
            context.close()
            browser.close()
