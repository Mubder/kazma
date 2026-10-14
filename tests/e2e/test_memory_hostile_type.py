"""Database text in the Memory page's attributes is escaped (Memory audit M36).

The graph filter built ``id``, ``title``, ``data-fg`` and ``data-fk`` from
entity types and wrote them into attributes with ``_esc``, which leaves
quotes as they are; ``backfill_v2`` copies free-form V1 entity types, so a
migrated install can hold any type. Here an entity type closes its attribute
and adds an event handler: the page must render it as text, with no handler.
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

HOSTILE = 'evil" onmouseover="window.__pwned=1" x="'


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(Script(steps=[], final="ok")) as h:
        yield h


def _seed(harness: Harness) -> None:
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db())
    try:
        ensure_primary_schema(c)
        now = time.time()
        c.execute("INSERT OR IGNORE INTO entities (id, tenant_id, type, name) VALUES ('acme', 'default', ?, 'Acme')",
                  (HOSTILE,))
        c.execute("INSERT OR IGNORE INTO entities (id, tenant_id, type, name) VALUES ('lisbon', 'default', "
                  "'place', 'Lisbon')")
        c.execute("INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, confidence, "
                  "structural_importance, valid_from, ingested_at) VALUES ('bh1', 'default', 'acme', "
                  "'located_in', 'functional', 'lisbon', 0.9, 3, ?, ?)", (now, now))
        c.commit()
    finally:
        c.close()


def test_a_hostile_entity_type_is_text_in_the_graph_filter(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    _seed(harness)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_context(viewport={"width": 1400, "height": 1000}).new_page()
            pg.goto(f"{harness.base}/memory", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function(
                "(t) => [...document.querySelectorAll('#v2g-filters-entity label')]"
                ".some((l) => l.textContent.includes(t))", arg="evil", timeout=30000)
            found = pg.evaluate("""() => {
                const out = {handlers: 0, ids: []};
                for (const el of document.querySelectorAll('#v2g-filters-entity *')) {
                    for (const a of el.attributes) {
                        if (a.name.startsWith('on')) out.handlers += 1;
                    }
                    if (el.id) out.ids.push(el.id);
                }
                return out;
            }""")
            assert found["handlers"] == 0, found
            assert "v2g-ft-entity-" + HOSTILE in found["ids"], found["ids"]
            # The checkbox still toggles its filter (the id round-trips).
            pg.locator("#v2g-filters-entity label", has_text="evil").first.click()
            pg.wait_for_function("() => !!document.querySelector('#v2g-active-filters span[data-fg]')",
                                 timeout=10000)
            chip = pg.evaluate("() => document.querySelector('#v2g-active-filters span[data-fg]')"
                               ".getAttribute('data-fk')")
            assert chip == HOSTILE
            pg.mouse.move(10, 10)
            assert pg.evaluate("() => window.__pwned === undefined")
        finally:
            browser.close()
