"""What Kazma knows about you (the memory framework audit's F14, part H), in
English and in Arabic.

Facts are written through the product's own writer: the user's home (said
in Paris, then London: a history), an employer Kazma only read from a turn,
and the dog's name (about something else). The page is reached by the
sidebar's Memory and the Memory page's link; it lists the user's two facts
with their sources and the home's history, never the dog's; Correct puts
the user's words in place of Kazma's reading, and "No longer true" ends the
home. The database is read after each.
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
        mutate_belief(c, "user", "lives_in", "Paris", extraction_method="user_stated", source_session="chat-a",
                      source_turn=1, now=now - 90000)
        mutate_belief(c, "user", "lives_in", "London", extraction_method="user_stated", source_session="chat-a",
                      source_turn=2, now=now - 900)
        mutate_belief(c, "user", "works_at", "Initech", predicate_type="functional",
                      extraction_method="llm_inferred", source_session="chat-a", source_turn=3, now=now - 600)
        mutate_belief(c, "dog", "pet_name", "Pixel", extraction_method="user_stated", source_session="chat-a",
                      source_turn=4, now=now - 300)
    finally:
        c.close()


def _current() -> dict[str, tuple[str, str]]:
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        return {str(r[0]): (str(r[1]), str(r[2])) for r in c.execute(
            "SELECT predicate, object, extraction_method FROM beliefs WHERE subject = 'user' "
            "AND valid_until IS NULL AND invalidated_at IS NULL AND object != ''")}
    finally:
        c.close()


_CARDS = "() => document.querySelectorAll('#memory-you-list .memory-you-fact').length"


def _card(pg, value: str):
    return pg.locator("#memory-you-list .memory-you-fact", has_text=value)


def _on_the_page(harness: Harness, lang: str) -> dict:
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
            pg.locator("#memory-you-link").click()
            pg.wait_for_function("location.pathname === '/memory/you'", timeout=15000)
            pg.wait_for_function(f"({_CARDS})() === 2", timeout=30000)
            london = _card(pg, "London")
            reading = _card(pg, "Initech")
            shown = {
                "list": pg.locator("#memory-you-list").inner_text(),
                "values_marked": pg.evaluate(
                    "() => [...document.querySelectorAll('#memory-you-list .memory-you-value bdi')]"
                    ".map((e) => [e.textContent, e.getAttribute('translate')])"),
                "london_source": london.locator(".badge").inner_text(),
                "reading_source": reading.locator(".badge").inner_text(),
                "history_summary": london.locator(".memory-you-history summary").inner_text(),
                "correct_label": reading.locator(".memory-you-correct").inner_text(),
                "end_label": london.locator(".memory-you-end").inner_text(),
            }
            london.locator(".memory-you-history summary").click()
            shown["history"] = london.locator(".memory-you-history li").inner_text()
            # Correct Kazma's reading in the user's own words.
            reading.locator(".memory-you-correct").click()
            field = reading.locator(".memory-you-input")
            field.fill("Gulf Bank")
            reading.locator(".memory-you-save").click()
            pg.wait_for_function(
                "() => [...document.querySelectorAll('#memory-you-list .memory-you-value')]"
                ".some((e) => e.textContent.includes('Gulf Bank'))", timeout=15000)
            after_correct = _current()
            said = pg.locator("#memory-you-said").inner_text()
            # The home is no longer true.
            _card(pg, "London").locator(".memory-you-end").click()
            pg.locator(".modal-footer button.btn-primary").first.click()
            pg.wait_for_function(f"({_CARDS})() === 1", timeout=15000)
            return {**shown, "after_correct": after_correct, "said": said, "after_end": _current(),
                    "gulf_source": _card(pg, "Gulf Bank").locator(".badge").inner_text(), "errors": errors}
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_user_sees_corrects_and_ends_what_kazma_knows(harness: Harness, lang: str) -> None:
    _seed()
    out = _on_the_page(harness, lang)
    assert out["errors"] == []
    assert "Pixel" not in out["list"]
    assert sorted(t for t, _ in out["values_marked"]) == ["Initech", "London"]
    assert {attr for _, attr in out["values_marked"]} == {"no"}
    assert "Paris" in out["history"]
    assert out["after_correct"]["works_at"] == ("Gulf Bank", "user_explicit")
    assert "works_at" in out["after_end"] and "lives_in" not in out["after_end"]
    if lang == "en":
        assert out["london_source"] == out["gulf_source"] == "You said it"
        assert out["reading_source"] == "Kazma's reading of a conversation"
        assert out["history_summary"] == "Before: 1 value"
        assert (out["correct_label"], out["end_label"]) == ("Correct", "No longer true")
        assert out["said"].startswith("Corrected.")
    else:
        assert out["london_source"] == out["gulf_source"] == "قلتها أنت"
        assert out["reading_source"] == "قراءة Kazma لمحادثة"
        assert out["history_summary"] == "قبل ذلك: قيمة واحدة"
        assert (out["correct_label"], out["end_label"]) == ("صحّح", "لم تعد صحيحة")
        assert out["said"].startswith("صُحّحت.")


def _seed_days() -> None:
    """Events with their day, one ahead, and the user's own home with a start
    Kazma read into what they said (the review of W4, 2026-11-24)."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        now = time.time()
        read = {"extraction_method": "llm_inferred", "source_session": "chat-a", "now": now - 600}
        for turn, (pred, obj, days) in enumerate([
                ("bought", "a road bike", {"started": "2026-05-30", "happened": True}),
                ("visited", "Rome", {"started": "2026-04", "happened": True}),
                ("scheduled_event", "the jazz concert", {"planned": "2999-06-07"})], 1):
            mutate_belief(c, "user", pred, obj, predicate_type="set", source_turn=turn, **read, **days)
        mutate_belief(c, "user", "lives_in", "London", extraction_method="user_stated", source_session="chat-a",
                      source_turn=4, now=now - 900)
        mutate_belief(c, "user", "lives_in", "London", source_turn=5, started="2019", **read)
        c.commit()
    finally:
        c.close()


def _days_on_the_page(harness: Harness, lang: str) -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        host = harness.base.split("//", 1)[1].split(":", 1)[0]
        context = browser.new_context(viewport={"width": 1400, "height": 1000}, locale="en-US")
        context.add_cookies([{"name": "kazma-lang", "value": lang, "domain": host, "path": "/"}])
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/", wait_until="domcontentloaded", timeout=30000)
            pg.locator("a.nav-link[href='/memory']").first.click()
            pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
            pg.locator("#memory-you-link").click()
            pg.wait_for_function("location.pathname === '/memory/you'", timeout=15000)
            pg.wait_for_function(f"({_CARDS})() === 4", timeout=30000)
            return {v: _card(pg, v).inner_text() for v in ("a road bike", "Rome", "the jazz concert", "London")}
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize(("lang", "words"), [
    ("en", {"a road bike": "On May 30, 2026", "Rome": "In April 2026", "the jazz concert": "Planned for Jun 7, 2999",
            "London": "Since 2019", "reading": "The date is Kazma's reading."}),
    ("ar", {"a road bike": "في 30", "Rome": "خلال ", "the jazz concert": "مخطط له في ", "London": "منذ 2019",
            "reading": "التاريخ قراءة Kazma."}),
])
def test_the_page_says_when_an_event_happened(harness: Harness, lang: str, words: dict[str, str]) -> None:
    _seed_days()
    cards = _days_on_the_page(harness, lang)
    for value in ("a road bike", "Rome", "the jazz concert", "London"):
        assert words[value] in cards[value], (value, cards[value])
    assert "2026-04" not in cards["Rome"]
    # The user said where they live; since when is Kazma's reading of it.
    assert words["reading"] in cards["London"]
    assert all(words["reading"] not in cards[v] for v in ("a road bike", "Rome", "the jazz concert"))
