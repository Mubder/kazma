"""The Memory page, as a person meets it, in English and Arabic (2026-11-18).

Reached by the sidebar. The page was reworked so each part says what it is
and each button what it does; these checks hold what a person sees:

* the status card answers (never stays on "Checking"), the map draws its
  dots and fits them in view once settled, the legend names the marks in
  use, and the line under the map counts things and facts in the page's
  language;
* the zoom buttons zoom and **Show all** brings every dot back in view;
* a map the server could not read says so, never "Nothing here yet";
* the clean-up and health sections are folded, and the tidy-up asks first,
  naming what it will do;
* on a phone the page does not scroll sideways.
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

FACTS = [
    ("user", "lives_in", "Rotterdam", "functional"),
    ("user", "works_at", "Acme", "functional"),
    ("user", "sister", "نورة", "set"),
    ("user", "working_on", "ShipX", "set"),
    ("shipx", "uses", "Python", "set"),
    ("نورة", "lives_in", "دبي", "functional"),
]
ENTITIES = [("نورة", "person", "نورة"), ("shipx", "project", "ShipX"), ("python", "tool", "Python")]


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _seed() -> None:
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        now = time.time()
        for eid, etype, name in ENTITIES:
            c.execute("INSERT OR IGNORE INTO entities (id, tenant_id, type, name, aliases_json, is_high_stakes, "
                      "metadata_json) VALUES (?, 'default', ?, ?, '[]', 0, '{}')", (eid, etype, name))
        c.commit()
        for i, (s, p, o, kind) in enumerate(FACTS):
            mutate_belief(c, s, p, o, predicate_type=kind, extraction_method="user_stated",
                          source_session="chat-a", source_turn=i + 1, now=now - 600 + i, private=True)
    finally:
        c.close()


WORDS = {
    "en": {"you": "You", "fact": "a fact", "things": "things", "checking": "Checking", "empty": "Nothing here yet",
           "tidy": "Delete names that hold no facts"},
    "ar": {"you": "أنت", "fact": "حقيقة", "things": "أشياء", "checking": "جارٍ التحقق", "empty": "لا شيء هنا بعد",
           "tidy": "احذف الأسماء التي لا تحمل حقائق"},
}


def _open(p, harness: Harness, lang: str, viewport: dict):
    host = harness.base.split("//", 1)[1].split(":", 1)[0]
    context = p.new_context(viewport=viewport, locale="en-US")
    context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
    pg = context.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{harness.base}/", wait_until="domcontentloaded", timeout=30000)
    pg.locator("a.nav-link[href='/memory']").first.click(force=True)
    pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
    return context, pg, errors


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_memory_page_reads_and_acts_as_it_says(harness: Harness, lang: str) -> None:
    from playwright.sync_api import sync_playwright

    _seed()
    words = WORDS[lang]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context, pg, errors = _open(browser, harness, lang, {"width": 1400, "height": 1000})
            # The map draws its dots, settles and fits them in view.
            pg.wait_for_function("() => window._v2gGetView && window._v2gGetView().dots >= 6", timeout=20000)
            pg.wait_for_function("() => window._v2gGetView().settled", timeout=30000)
            view = pg.evaluate("() => window._v2gGetView()")
            assert view["inView"] == view["dots"], view
            assert pg.locator("#v2g-loading").is_hidden()
            assert pg.locator("#v2g-failed").is_hidden()
            assert pg.locator("#v2g-empty").is_hidden()
            # The status card answered.
            pg.wait_for_function("(w) => !document.getElementById('memory-status-badge').textContent.includes(w)",
                                 arg=words["checking"], timeout=20000)
            # The legend names the marks in use, the line under the map counts them.
            legend = pg.locator("#v2g-legend").inner_text()
            assert words["you"] in legend and words["fact"] in legend, legend
            assert "site accent" not in legend
            stats = pg.locator("#v2g-stats-line").inner_text()
            assert words["things"] in stats and "sqlite" not in stats, stats
            # Zoom in zooms; Show all brings every dot back.
            before = view["scale"]
            pg.click("#v2g-zoom-in")
            pg.click("#v2g-zoom-in")
            pg.click("#v2g-zoom-in")
            assert pg.evaluate("() => window._v2gGetView().scale") > before
            pg.click("#v2g-fit")
            view = pg.evaluate("() => window._v2gGetView()")
            assert view["inView"] == view["dots"], view
            # The clean-up and health sections are folded until opened.
            assert pg.locator("#mem-tab-hygiene").is_hidden()
            assert not pg.evaluate("() => document.getElementById('memory-health').open")
            pg.click("#memory-cleanup > summary")
            pg.click("#mem-tab-hygiene")
            # The tidy-up asks first and names what it will do; Cancel does nothing.
            pg.check("#mem-tidy-empty")
            pg.click("#mem-tidy-run")
            dialog = pg.locator(".confirm-message")
            dialog.wait_for(timeout=5000)
            assert words["tidy"] in dialog.inner_text(), dialog.inner_text()
            pg.locator(".modal-footer button").first.click()
            dialog.wait_for(state="detached", timeout=5000)
            assert errors == [], errors
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_map_the_server_could_not_read_is_not_an_empty_memory(harness: Harness, lang: str) -> None:
    from playwright.sync_api import sync_playwright

    words = WORDS[lang]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            host = harness.base.split("//", 1)[1].split(":", 1)[0]
            context = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US")
            context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
            # The route answers 200 with `error` and no dots when it cannot
            # read the memory database.
            context.route("**/api/memory/v2/graph*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body='{"nodes": [], "links": [], "error": "database is locked"}'))
            pg = context.new_page()
            pg.goto(f"{harness.base}/", wait_until="domcontentloaded", timeout=30000)
            pg.locator("a.nav-link[href='/memory']").first.click(force=True)
            pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
            pg.locator("#v2g-failed").wait_for(state="visible", timeout=20000)
            assert pg.locator("#v2g-empty").is_hidden()
            assert words["empty"] not in pg.locator("#v2g-canvas-wrap").inner_text()
            context.close()
        finally:
            browser.close()


def test_the_page_does_not_scroll_sideways_on_a_phone(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    _seed()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for lang in ("en", "ar"):
                context, pg, errors = _open(browser, harness, lang, {"width": 390, "height": 844})
                pg.wait_for_function("() => window._v2gGetView && window._v2gGetView().dots >= 6", timeout=20000)
                pg.wait_for_function("() => window._v2gGetView().settled", timeout=30000)
                wide = pg.evaluate("() => document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth")
                assert wide <= 1, (lang, wide)
                view = pg.evaluate("() => window._v2gGetView()")
                assert view["inView"] == view["dots"], (lang, view)
                assert errors == [], errors
                context.close()
        finally:
            browser.close()
