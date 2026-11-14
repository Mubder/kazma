"""Ready-made automations in a real browser (REMAINING 4.3): the Scheduled
page, reached by the sidebar, shows a card per recipe; one click books the
news digest at the chosen time and it is in the scheduled list. Arabic too.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from tempfile import TemporaryDirectory

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")
httpx = pytest.importorskip("httpx")

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def kazma(monkeypatch, tmp_path):
    import uvicorn
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_ui.app import create_app

    import kazma_core.tools.send_message as send_message

    # Where a Web-booked task answers: a stand-in Telegram chat (nothing is sent here).
    monkeypatch.setattr(send_message, "resolve_delivery_target", lambda: "telegram:4242")
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "data"))
    for name in list(os.environ):
        if name.endswith("_API_KEY") or name in ("KAZMA_MODEL", "KAZMA_PROVIDER", "KAZMA_SECRET"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    with TemporaryDirectory() as tmp:
        store = ConfigStore(db_path=os.path.join(tmp, "settings.db"))
        set_config_store(store)
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    if httpx.get(f"{base}/health/live", timeout=2.0).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            yield base
        finally:
            server.should_exit = True
            thread.join(timeout=10.0)
            store.close()
            from kazma_core.model_registry import reset_model_registry
            from kazma_core.shutdown import reset_shutdown, uninstall_shutdown_signal_hooks
            from kazma_ui.session_manager import reset_session_manager

            uninstall_shutdown_signal_hooks()
            reset_shutdown()
            reset_session_manager()
            reset_model_registry()


def test_one_click_books_an_automation(kazma) -> None:
    from playwright.sync_api import sync_playwright

    base = kazma
    host = base.split("//", 1)[1].split(":", 1)[0]
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US").new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.goto(base + "/", wait_until="domcontentloaded")
            pg.locator("a.nav-link[href='/scheduled']").first.click()
            pg.wait_for_function("location.pathname === '/scheduled'", timeout=15000)
            pg.locator("#sched-recipes").wait_for(state="visible", timeout=15000)
            for rid in ("morning_briefing", "inbox_triage", "weekly_report", "news_digest"):
                assert pg.locator(f"#recipe-{rid}").is_visible(), rid
            pg.wait_for_function("document.getElementById('recipe-time-morning_briefing').value === '07:30'",
                                 timeout=15000)
            book = pg.locator("#recipe-book-news_digest")
            assert book.is_disabled(), "the news digest needs a topic first"
            pg.locator("#recipe-topic-news_digest").fill("electric cars")
            pg.locator("#recipe-time-news_digest").fill("06:45")
            book.click()
            pg.wait_for_function(
                "fetch('/api/scheduled/tasks').then(r => r.json()).then(d => (d.tasks || []).some("
                "t => String(t.prompt || t.text || t.summary || '').includes('electric cars')))",
                timeout=20000)

            ar_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ar_ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = ar_ctx.new_page()
            ar.on("pageerror", lambda exc: errors.append(str(exc)))
            ar.goto(base + "/", wait_until="domcontentloaded")
            ar.locator("a.nav-link[href='/scheduled']").first.click()
            ar.wait_for_function("location.pathname === '/scheduled'", timeout=15000)
            assert ar.locator("#sched-recipes-title").inner_text() == "أتمتة جاهزة"
        finally:
            browser.close()
    assert errors == [], errors
