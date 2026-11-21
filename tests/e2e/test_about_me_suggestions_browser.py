"""Suggested About-me lines on the Memory page (the memory framework audit's
F13, decision 10), in English and in Arabic.

Three facts are written through the product's own writer: two the user said
themself (home, an allergy) and one Kazma only read from a turn (an
employer). The page is reached by the sidebar; it offers the two lines and
never the reading; About me holds nothing until Add is pressed; Add puts the
line at the end of About me, Dismiss keeps the other from coming back, and
the database is read after each.
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

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        now = time.time()
        mutate_belief(c, "user", "lives_in", "Porto", extraction_method="user_stated", source_session="chat-a",
                      source_turn=1, now=now - 900)
        mutate_belief(c, "user", "allergic_to", "shellfish", predicate_type="set", extraction_method="user_stated",
                      source_session="chat-a", source_turn=2, now=now - 600)
        mutate_belief(c, "user", "works_at", "Initech", predicate_type="functional",
                      extraction_method="llm_inferred", source_session="chat-a", source_turn=3, now=now - 300)
    finally:
        c.close()


def _about() -> str:
    from kazma_core.memory.profile import get_about

    return get_about("default")["about"]


def _decisions() -> list[str]:
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        return sorted(r[0] for r in c.execute("SELECT decision FROM memory_profile_suggestions"))
    finally:
        c.close()


_ROWS = "() => document.querySelectorAll('#v2-suggestions-list .v2-suggestion').length"


def _answer_on_the_page(harness: Harness, lang: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        host = harness.base.split("//", 1)[1].split(":", 1)[0]
        context = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.goto(f"{harness.base}/", wait_until="domcontentloaded", timeout=30000)
            pg.locator("a.nav-link[href='/memory']").first.click()
            pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
            pg.wait_for_function(f"({_ROWS})() === 2", timeout=30000)
            card = pg.locator("#v2-suggestions-card")
            shown = {
                "title": card.locator(".mem-card-title").inner_text(),
                "list": pg.locator("#v2-suggestions-list").inner_text(),
                "lines_marked": pg.evaluate(
                    "() => [...document.querySelectorAll('#v2-suggestions-list .v2-suggestion-line')]"
                    ".map((e) => [e.textContent, e.getAttribute('translate')])"),
                "add_label": card.locator("[data-decision='add']").first.inner_text(),
                "dismiss_label": card.locator("[data-decision='dismiss']").first.inner_text(),
                "about_before": _about(),
            }
            porto = pg.locator("#v2-suggestions-list .v2-suggestion", has_text="Porto")
            porto.locator("[data-decision='add']").click()
            pg.wait_for_function(f"({_ROWS})() === 1", timeout=15000)
            after_add = _about()
            pg.locator("#v2-suggestions-list .v2-suggestion [data-decision='dismiss']").click()
            pg.wait_for_function(f"({_ROWS})() === 0", timeout=15000)
            empty = pg.locator("#v2-suggestions-list").inner_text()
            # Away and back by the sidebar: neither comes back.
            pg.locator("a.nav-link[href='/chat']").first.click()
            pg.wait_for_function("location.pathname === '/chat'", timeout=15000)
            pg.locator("a.nav-link[href='/memory']").first.click()
            pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
            pg.wait_for_function(
                "() => { const el = document.getElementById('v2-suggestions-list');"
                " return !!el && !el.querySelector('.v2-suggestion') && el.textContent.trim().length > 0"
                " && !/Loading|جار/.test(el.textContent); }", timeout=15000)
            return {**shown, "after_add": after_add, "empty": empty,
                    "back": pg.locator("#v2-suggestions-list").inner_text(),
                    "decisions": _decisions(), "about_end": _about(), "errors": errors}
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_user_adds_one_suggested_line_and_dismisses_another(harness: Harness, lang: str) -> None:
    _seed()
    out = _answer_on_the_page(harness, lang)
    assert out["errors"] == []
    # Only what the user said: never the employer Kazma read.
    assert "Initech" not in out["list"]
    assert sorted(t for t, _ in out["lines_marked"]) == ["I am allergic to shellfish.", "I live in Porto."]
    assert {attr for _, attr in out["lines_marked"]} == {"no"}
    # Nothing joined About me without a yes; Add put the line at its end.
    assert out["about_before"] == ""
    assert out["after_add"] == "I live in Porto."
    assert out["about_end"] == "I live in Porto."
    assert out["decisions"] == ["added", "dismissed"]
    if lang == "en":
        assert out["title"] == "Suggested for About me"
        assert (out["add_label"], out["dismiss_label"]) == ("Add to About me", "Dismiss")
        assert out["empty"] == out["back"] == "Nothing to suggest for About me right now."
    else:
        assert out["title"] == "مقترح لـ«عني»"
        assert (out["add_label"], out["dismiss_label"]) == ("أضف إلى «عني»", "تجاهل")
        assert out["empty"] == out["back"] == "لا يوجد ما يُقترح لـ«عني» الآن."
