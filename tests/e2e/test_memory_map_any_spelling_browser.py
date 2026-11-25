"""The Memory page's map finds an Arabic name in either usual spelling (W6).

The map filters in the browser; it compared lower-cased text, so "قهوه" hid
the node "قهوة". Reached through the sidebar, in both interfaces; the line
under the map says how many things are drawn.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _seed() -> None:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db(), isolation_level=None)
    c.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(c)
        for n, (subject, value) in enumerate((("مقهى_الأمل", "قهوة تركية"), ("مستشفى_العدان", "طوارئ"),
                                              ("berlin_office", "Coffee machine"))):
            mutate_belief(c, subject, "has", value, predicate_type="set", extraction_method="user_stated",
                          tenant_id="default", now=time.time() - n, private=True)
    finally:
        c.close()


_STATS = "() => (document.getElementById('v2g-stats-line') || {}).textContent || ''"


def _drawn(pg, query: str) -> str:
    """The line under the map once the box holds *query* (its search waits for
    250 ms of quiet; the line is emptied first, so it is this search's)."""
    pg.evaluate("(q) => { document.getElementById('v2g-stats-line').textContent = '';"
                " const s = document.getElementById('v2g-search'); s.value = q;"
                " s.dispatchEvent(new Event('input', {bubbles: true})); }", query)
    pg.wait_for_function(f"() => !!({_STATS})()", timeout=15000)
    return pg.evaluate(_STATS)


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_map_finds_a_name_in_either_spelling(harness, lang):
    from playwright.sync_api import sync_playwright

    _seed()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        host = harness.base.split("//", 1)[1].split(":", 1)[0]
        context = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{harness.base}/settings", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_selector("a[href='/memory']", state="attached", timeout=30000)
            pg.evaluate("() => document.querySelector(\"a[href='/memory']\").click()")
            pg.wait_for_function(f"() => /[0-9٠-٩]/.test(({_STATS})())", timeout=30000)
            everything = pg.evaluate(_STATS)
            seen = {query: _drawn(pg, query)
                    for query in ("قهوة", "قهوه", "coffee", "COFFEE", "مستشفي", "zzzz")}
            assert seen["قهوه"] == seen["قهوة"] != everything, seen
            assert seen["COFFEE"] == seen["coffee"] != everything, seen
            assert seen["قهوه"] != seen["zzzz"] and seen["مستشفي"] != seen["zzzz"], seen
            assert errors == []
        finally:
            browser.close()
