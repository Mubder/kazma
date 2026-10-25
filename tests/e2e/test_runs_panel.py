"""The Dashboard's Running work card in a browser, in English and Arabic.

A real memory-queue dead letter is turned into an incident by one real
supervisor pass. The Dashboard is reached the way a person reaches it -- by
the sidebar, a soft navigation -- and from the bell's link to the incident.
The card shows the incident with its buttons in the page's language; Retry
puts the task back on the queue through the Memory page's own control, and
the incident moves to the history with its outcome. No page error.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _dead_letter(data_dir: str) -> str:
    from kazma_core.memory.schema_v2 import ensure_ops_schema
    from kazma_core.paths import memory_ops_db

    conn = sqlite3.connect(memory_ops_db())
    try:
        ensure_ops_schema(conn)
        conn.execute("INSERT INTO memory_task_queue (id, task_type, payload_json, status, attempts, max_attempts, "
                     "created_at, updated_at, error_log) VALUES ('t-dead', 'micro_consolidation', '{}', 'failed', "
                     "6, 6, ?, ?, 'provider outage')", (time.time() - 60, time.time() - 30))
        conn.commit()
    finally:
        conn.close()
    from kazma_core.runs import supervisor
    from kazma_core.runs.incidents import get_incident_store, reset_incident_store

    reset_incident_store(None)  # bind to this server's data dir
    asyncio.run(supervisor.tick())
    return get_incident_store().live()[0]["id"]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_runs_card_shows_an_incident_and_acts_on_it(language: str) -> None:
    from kazma_core.paths import memory_ops_db
    from kazma_core.runs.incidents import get_incident_store, reset_incident_store
    from kazma_core.runs.text import action_word
    from kazma_ui.i18n import TRANSLATIONS
    from playwright.sync_api import sync_playwright

    with unified_turn_server() as h:
        incident = _dead_letter(h.data_dir)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    context = browser.new_context()
                    context.add_cookies([dict(name="kazma-lang", value=language, url=h.base)])
                    page = context.new_page()
                    errors: list[str] = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(h.base + "/memory", wait_until="domcontentloaded")
                    page.wait_for_function("() => !!window.Alpine")
                    page.evaluate("() => { window.__notReloaded = true; }")
                    page.click('nav a[href="/dashboard"], aside a[href="/dashboard"]', timeout=15000)
                    page.wait_for_function("() => location.pathname === '/dashboard'", timeout=20000)
                    card = f"#runs-incident-{incident}"
                    page.wait_for_selector(card, timeout=20000)
                    assert page.evaluate("() => window.__notReloaded === true"), "reached by the sidebar"
                    text = page.inner_text("#runs-panel")
                    # The heading is uppercased by CSS; its text is the catalog's.
                    assert TRANSLATIONS["runs.title"][language] in page.text_content("#runs-title")
                    assert "micro_consolidation" in text and "provider outage" in text
                    retry = action_word("retry", language)
                    assert retry in page.inner_text(card)
                    assert page.get_attribute(f"{card} .runs-incident-title", "translate") == "no"

                    page.click(f"{card} button:has-text('{retry}')")
                    page.wait_for_function(
                        "(id) => !document.querySelector('#runs-incidents #runs-incident-' + id)",
                        arg=incident, timeout=15000)
                    page.wait_for_selector(f"#runs-history-list #runs-incident-{incident}", state="attached",
                                           timeout=15000)
                    row = get_incident_store().get(incident)
                    assert row["state"] == "closed" and row["decided_platform"] == "web"
                    conn = sqlite3.connect(memory_ops_db())
                    try:
                        status = conn.execute("SELECT status FROM memory_task_queue WHERE id='t-dead'").fetchone()[0]
                    finally:
                        conn.close()
                    assert status == "pending", "the Memory page's own retry ran"

                    # The bell's link to an incident focuses it on the card.
                    page.goto(h.base + f"/dashboard?incident={incident}", wait_until="domcontentloaded")
                    page.wait_for_selector("#runs-history:not([hidden])", timeout=15000)
                    assert not errors, errors
                finally:
                    browser.close()
        finally:
            reset_incident_store(None)
