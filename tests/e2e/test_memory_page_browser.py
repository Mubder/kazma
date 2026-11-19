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
* on a phone the page does not scroll sideways;
* a memory kept whole (2026-11-19, ``memory/limits.py``) is listed bounded,
  and "The whole memory" reads it to its last word, a part at a time; a
  short memory of several lines offers no such fold.
"""

from __future__ import annotations

import re
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
    if viewport["width"] < 768:
        # A phone keeps the sidebar in a drawer: a person opens it with the
        # bottom bar's "More", then taps Memory once it has slid in. The tap
        # is tried again while the page's script is still starting.
        in_view = ("() => { const a = document.querySelector(\"aside.sidebar a.nav-link[href='/memory']\");"
                   " const r = a && a.getBoundingClientRect();"
                   " return !!r && r.width > 0 && r.left >= 0 && r.right <= innerWidth; }")
        for _ in range(5):
            pg.locator("button.bottom-nav-more").first.click()
            try:
                pg.wait_for_function(in_view, timeout=3000)
                break
            except Exception:  # noqa: BLE001 - the drawer did not open yet: tap again
                continue
        pg.wait_for_function(in_view, timeout=3000)
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


#: A long turn: past the list's bounds and past one page of the reader
#: (``limits.MEMORY_READ_PAGE``), with a word only its very end holds.
LONG_QUESTION = "Please read my notes on the pump housings and tell me what to change. " * 140
LONG_ANSWER = "Here is what I would change in the housings, step by step. " * 160 + "The last word is ZEPHYRQUILL."
SHORT_QUESTION = "first line\n\n\nsecond   line\n\n\nthird line"


def _seed_memories() -> tuple[str, str]:
    """A long memory and a short one of several lines, stored as the writer
    stores them (``dual_write.episode_row``: whole), with no embedder."""
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        ids = []
        for i, (session, question, answer) in enumerate((
            ("chat-long", LONG_QUESTION, LONG_ANSWER),
            ("chat-short", SHORT_QUESTION, "noted:\n\n- one\n\n- two"),
        )):
            row = episode_row(session_id=session, turn_number=1, user_text=question,
                              assistant_text=answer, tenant_id="default", tier="episodic",
                              source="turn_reconcile")
            c.execute(
                "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                "summary_text, tier, structural_importance, created_at) "
                "VALUES (?, 'default', ?, 1, ?, ?, '', 'episodic', 1, ?)",
                (row["id"], session, row["user_text"], row["assistant_text"], time.time() - 60 * (i + 1)),
            )
            ids.append(row["id"])
        c.commit()
    finally:
        c.close()
    assert len(row["user_text"]) == len(SHORT_QUESTION)  # stored whole
    return ids[0], ids[1]


WHOLE = {"en": "The whole memory", "ar": "الذاكرة كاملة"}

#: Elements on the Memory page marked ``hidden`` that are drawn anyway (a
#: class or an inline style's ``display`` beats the attribute without the
#: page's ``.memory-page [hidden]`` rule).
HIDDEN_BUT_SHOWN = """() => Array.from(document.querySelectorAll('.memory-page [hidden]'))
  .filter((el) => getComputedStyle(el).display !== 'none')
  .map((el) => el.id || String(el.className))"""
DROP_HIDDEN_RULE = """() => { for (const sheet of document.styleSheets) {
  let rules; try { rules = sheet.cssRules; } catch (e) { continue; }
  for (let i = rules.length - 1; i >= 0; i--) {
    if (rules[i].selectorText === '.memory-page [hidden]') sheet.deleteRule(i);
  } } }"""


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_long_memory_is_listed_bounded_and_read_whole(harness: Harness, lang: str) -> None:
    from playwright.sync_api import sync_playwright

    long_id, short_id = _seed_memories()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context, pg, errors = _open(browser, harness, lang, {"width": 1400, "height": 1000})
            listing = pg.locator("#v2-memories-list")
            fold = listing.locator(f"details.v2-whole[data-id='{long_id}']")
            fold.wait_for(state="attached", timeout=20000)
            # The short memory of several lines is shown whole: no fold.
            assert listing.locator(f"details.v2-whole[data-id='{short_id}']").count() == 0
            summary = fold.locator("summary")
            assert WHOLE[lang] in summary.inner_text(), summary.inner_text()
            # How much more, written as the page writes numbers ("18,770";
            # it read "18770").
            assert re.search(r"[0-9]{1,3}[,٬][0-9]{3}", summary.inner_text()), summary.inner_text()
            # The list shows the start, bounded; the end is not on the page yet.
            assert "ZEPHYRQUILL" not in listing.inner_text()
            summary.scroll_into_view_if_needed()
            summary.click()
            text = fold.locator(".v2-whole-text")
            shown = ("([id, n]) => (document.querySelector(`#v2-memories-list details.v2-whole[data-id='${id}'] "
                     ".v2-whole-text`) || {textContent: ''}).textContent.length > n")
            pg.wait_for_function(shown, arg=[long_id, 0], timeout=15000)
            nxt = fold.locator(".v2-whole-next")
            pages = 1
            while nxt.is_visible() and pages < 10:
                before = len(text.text_content() or "")
                nxt.click()
                pg.wait_for_function(shown, arg=[long_id, before], timeout=15000)
                pages += 1
            assert pages >= 2  # more than one part: the reader pages
            # At the end "Show the next part" goes away: its `hidden` lost to
            # the .btn display rule, and it stayed, doing nothing.
            assert nxt.is_hidden()
            whole = text.text_content()
            assert whole.startswith("User: Please read my notes") and whole.rstrip().endswith("ZEPHYRQUILL.")
            assert len(whole) == len(f"User: {LONG_QUESTION.strip()}\n\nAssistant: {LONG_ANSWER.strip()}")
            # Nothing on the page that says it is hidden shows.
            assert pg.evaluate(HIDDEN_BUT_SHOWN) == []
            # Negative control: without the page's [hidden] rule the finished
            # reader's button is on screen again.
            pg.evaluate(DROP_HIDDEN_RULE)
            assert pg.evaluate(HIDDEN_BUT_SHOWN) != [] and nxt.is_visible()
            assert errors == [], errors
            context.close()
        finally:
            browser.close()
