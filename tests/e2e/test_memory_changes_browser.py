"""Changes to what the user told Kazma, on the Memory page (the memory
framework audit's F1, second half), in English and in Arabic.

Two changes are made through the product's own writers: an update (the
user's "Paris" replaced by their later "Rotterdam") and a correction Kazma
was not sure was the user's (the model read "Initech" from a message with no
subject; the trust gate kept "Acme" and the change waits). The page is
reached by the sidebar; the update is undone and the correction confirmed
from the card, and the database is read after each.
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
    from kazma_core.memory import changes
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        now = time.time()
        mutate_belief(c, "user", "lives_in", "Paris", extraction_method="user_stated", source_session="chat-a",
                      source_turn=1, now=now - 900)
        mutate_belief(c, "user", "lives_in", "Rotterdam", extraction_method="user_stated",
                      source_session="chat-a", source_turn=2, now=now - 600)
        mutate_belief(c, "user", "works_at", "Acme", predicate_type="functional", extraction_method="user_stated",
                      source_session="chat-a", source_turn=1, now=now - 900)
        refused = mutate_belief(c, "user", "works_at", "Initech", predicate_type="functional",
                                extraction_method="llm_inferred", source_session="chat-a", source_turn=3,
                                now=now - 300)
        assert refused.get("blocked") == "lower_trust_source"
        assert changes.record_pending(c, tenant_id="default", refused=refused, new_value="Initech",
                                      user_text="Now at Initech, started Monday.", session="chat-a", turn=3)
        assert len(changes.list_changes(c, tenant_id="default")) == 2
    finally:
        c.close()


def _current(predicate: str) -> list[tuple[str, str]]:
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        return [tuple(r) for r in c.execute(
            "SELECT object, extraction_method FROM beliefs WHERE subject = 'user' AND predicate = ? "
            "AND valid_until IS NULL AND invalidated_at IS NULL", (predicate,))]
    finally:
        c.close()


def _decide_on_the_page(harness: Harness, lang: str) -> dict:
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
            pg.wait_for_function("() => document.querySelectorAll('#v2-changes-list .v2-change').length === 2",
                                 timeout=30000)
            update = pg.locator("#v2-changes-list .v2-change[data-kind='update']")
            pending = pg.locator("#v2-changes-list .v2-change[data-kind='pending']")
            shown = {"update": update.inner_text(), "pending": pending.inner_text(),
                     "values_marked": pg.evaluate(
                         "() => [...document.querySelectorAll('#v2-changes-list bdi[translate=\"no\"]')]"
                         ".map((b) => b.textContent)"),
                     "confirm_label": pending.locator("[data-decision='confirm']").inner_text()}
            update.locator("[data-decision='undo']").click()
            pg.wait_for_function(
                "() => !document.querySelector(\"#v2-changes-list .v2-change[data-kind='update']\")", timeout=15000)
            after_undo = _current("lives_in")
            pending.locator("[data-decision='confirm']").click()
            # The confirmed correction is now an update the user can undo.
            pg.wait_for_function(
                "() => !document.querySelector(\"#v2-changes-list .v2-change[data-kind='pending']\")"
                " && !!document.querySelector(\"#v2-changes-list .v2-change[data-kind='update']\")",
                timeout=15000)
            after_confirm = _current("works_at")
            return {**shown, "after_undo": after_undo, "after_confirm": after_confirm,
                    "last": pg.locator("#v2-changes-list").inner_text(), "errors": errors}
        finally:
            context.close()
            browser.close()


def _seed_first_reading() -> None:
    """The model's first reading of the user's home (no fact of theirs),
    written as its inference and shown as Kazma's reading, never asked
    (rule A, 2026-11-18)."""
    from kazma_core.memory import changes
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        written = mutate_belief(c, "user", "lives_in", "rotterdam", predicate_type="functional",
                                extraction_method="llm_inferred", source_session="chat-a", source_turn=4)
        assert changes.record_first_reading(c, tenant_id="default", written=written, predicate="lives_in",
                                            new_value="rotterdam", user_text="We moved to Rotterdam last month.",
                                            session="chat-a", turn=4)
    finally:
        c.close()


def _answer_a_first_reading(harness: Harness, lang: str, decision: str) -> dict:
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
            pg.wait_for_function("() => document.querySelectorAll('#v2-changes-list .v2-change').length === 1",
                                 timeout=30000)
            card = pg.locator("#v2-changes-list .v2-change[data-kind='reading']")
            shown = {"card": card.inner_text(),
                     "yes": card.locator("[data-decision='confirm']").inner_text(),
                     "no": card.locator("[data-decision='dismiss']").inner_text()}
            card.locator(f"[data-decision='{decision}']").click()
            pg.wait_for_function(
                "() => !document.querySelector(\"#v2-changes-list .v2-change[data-kind='reading']\")", timeout=15000)
            return {**shown, "after": _current("lives_in"), "errors": errors}
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize(("lang", "decision", "after"), [
    ("en", "dismiss", []),
    ("ar", "confirm", [("Rotterdam", "user_explicit")]),
])
def test_the_user_answers_kazmas_reading(harness: Harness, lang: str, decision: str, after: list) -> None:
    _seed_first_reading()
    assert _current("lives_in") == [("rotterdam", "llm_inferred")]
    out = _answer_a_first_reading(harness, lang, decision)
    assert out["errors"] == []
    assert out["after"] == after
    if lang == "en":
        assert "Kazma's reading of your message" in out["card"] and "Kazma read: Rotterdam" in out["card"]
        assert "Home" in out["card"]  # the fact's name in the page's words
        assert (out["yes"], out["no"]) == ("This is right", "Not me")
    else:
        assert "قراءة Kazma لرسالتك" in out["card"] and "Rotterdam" in out["card"]
        assert "مكان السكن" in out["card"] and "lives in" not in out["card"]
        assert (out["yes"], out["no"]) == ("هذا صحيح", "ليس عني")


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_user_undoes_an_update_and_confirms_a_correction(harness: Harness, lang: str) -> None:
    _seed()
    # Before the page: Kazma replaced Paris and kept Acme.
    assert _current("lives_in") == [("Rotterdam", "user_stated")]
    assert _current("works_at") == [("Acme", "user_stated")]
    out = _decide_on_the_page(harness, lang)
    assert out["errors"] == []
    assert {"Paris", "Rotterdam", "Acme", "Initech"} <= set(out["values_marked"])
    assert out["after_undo"] == [("Paris", "user_stated")]
    assert out["after_confirm"] == [("Initech", "user_explicit")]
    assert "Initech" in out["last"] and "Acme" in out["last"]
    if lang == "en":
        assert "Changed from what you said" in out["update"]
        assert "Is this a change?" in out["pending"]
        assert out["confirm_label"] == "Yes, change it"
    else:
        assert "تغيّرت بناءً على كلامك" in out["update"]
        assert "هل هذا تغيير؟" in out["pending"]
        assert out["confirm_label"] == "نعم، غيّرها"
