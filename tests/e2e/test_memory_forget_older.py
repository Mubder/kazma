"""A memory older than the first page is forgotten from the Memory page
(Memory audit M23), in English and in Arabic.

The panel listed the newest 30 memories and had no way to the rest, so an
older one could not be forgotten from the page. Here 35 memories are stored;
the oldest is reached once by the Older button and once by searching its
words, forgotten through the page's own dialog, and checked in the forget
ledger and the episode's tier.
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

OLDEST = "ep-old-000"


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _seed(n: int = 35) -> None:
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        now = time.time()
        for i in range(n):
            question = "my locker code at the gym is 4417" if i == 0 else f"note {i} about the garden plan"
            c.execute(
                "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
                "created_at) VALUES (?, 'default', ?, 1, ?, 'Noted.', 'episodic', ?)",
                (f"ep-old-{i:03d}", f"chat-old-{i}", question, now - (n - i) * 3600),
            )
        c.commit()
    finally:
        c.close()


def _state() -> tuple[str, int]:
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        tier = c.execute("SELECT tier FROM episodes WHERE id = ?", (OLDEST,)).fetchone()[0]
        ledger = c.execute("SELECT COUNT(*) FROM memory_forgotten WHERE episode_id = ?", (OLDEST,)).fetchone()[0]
        return tier, int(ledger)
    finally:
        c.close()


def _forget_oldest(harness: Harness, lang: str, *, by_search: bool) -> dict:
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
            pg.goto(f"{harness.base}/memory", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function(
                "() => document.querySelectorAll('#v2-memories-list .v2-memory-forget').length === 30",
                timeout=30000)
            first_page = pg.evaluate(
                "() => [...document.querySelectorAll('#v2-memories-list .v2-memory-forget')]"
                ".map((b) => b.getAttribute('data-id'))")
            range_first = pg.locator("#v2-memories-range").inner_text()
            if by_search:
                pg.locator("#v2-memories-search").fill("locker gym")
            else:
                pg.locator("#v2-memories-older").click()
            pg.wait_for_function(
                "(id) => !!document.querySelector('#v2-memories-list .v2-memory-forget[data-id=\"' + id + '\"]')",
                arg=OLDEST, timeout=15000)
            range_after = pg.locator("#v2-memories-range").inner_text()
            older_disabled = pg.locator("#v2-memories-older").is_disabled()
            pg.locator(f'#v2-memories-list .v2-memory-forget[data-id="{OLDEST}"]').click()
            confirm = pg.locator(".modal-footer button.btn-danger")
            confirm.wait_for(state="visible", timeout=10000)
            confirm_text = confirm.inner_text()
            confirm.click()
            pg.wait_for_function(
                "(id) => !document.querySelector('#v2-memories-list .v2-memory-forget[data-id=\"' + id + '\"]')",
                arg=OLDEST, timeout=15000)
            placeholder = pg.locator("#v2-memories-search").get_attribute("placeholder")
            return {"first_page": first_page, "range_first": range_first, "range_after": range_after,
                    "older_disabled": older_disabled, "confirm": confirm_text, "placeholder": placeholder,
                    "errors": errors}
        finally:
            context.close()
            browser.close()


@pytest.mark.parametrize(("lang", "by_search"), [("en", False), ("ar", True)])
def test_the_oldest_memory_is_forgotten_from_the_page(harness: Harness, lang: str, by_search: bool) -> None:
    _seed()
    assert _state() == ("episodic", 0)
    out = _forget_oldest(harness, lang, by_search=by_search)
    assert out["errors"] == []
    # Negative control: the first page -- all the panel used to show -- does
    # not hold the oldest memory.
    assert OLDEST not in out["first_page"]
    assert _state() == ("forgotten", 1)
    if lang == "en":
        assert out["range_first"] == "1–30 of 35"
        assert out["range_after"] == "31–35 of 35" and out["older_disabled"]
        assert out["placeholder"] == "Search memories by their words"
    else:
        assert out["range_first"] == "1–30 من 35"
        assert out["range_after"] == "1–1 من 1"
        assert out["placeholder"] == "ابحث في الذكريات بكلماتها"
        assert out["confirm"] != "Forget"
