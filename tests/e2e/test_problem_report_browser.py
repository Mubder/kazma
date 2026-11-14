"""The problem report in a browser (2026-11-14, REMAINING 3.1): the ? beside the
bell, on a page reached by the sidebar. The report is shown whole before it is
sent; with no inbox set it is saved and the dialog says where. Arabic too.
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
def kazma(monkeypatch):
    import uvicorn
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_ui.app import create_app

    for name in list(os.environ):
        if name.endswith("_API_KEY") or name in ("KAZMA_MODEL", "KAZMA_PROVIDER", "KAZMA_SECRET"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    with TemporaryDirectory() as tmp:
        store = ConfigStore(db_path=os.path.join(tmp, "settings.db"))
        set_config_store(store)
        store.set("support.report_url", "", category="support")  # saved here, never sent
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


def test_the_report_dialog_shows_the_report_then_keeps_it(kazma) -> None:
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
            pg.locator("a.nav-link[href='/settings']").first.click()
            pg.wait_for_function("location.pathname === '/settings'", timeout=15000)
            pg.locator("#report-problem").click()
            pg.locator("#report-dialog").wait_for(state="visible", timeout=10000)
            assert pg.locator("#report-send").is_disabled(), "nothing is sent before it is shown"
            pg.locator("#report-note").fill("The PDF of my notes came out empty")
            pg.locator("#report-show").click()
            preview = pg.locator(".report-preview")
            preview.wait_for(state="visible", timeout=60000)
            text = preview.inner_text()
            assert '"note": "The PDF of my notes came out empty"' in text and '"where": "/settings"' in text
            assert '"kind": "kazma-problem-report"' in text
            pg.locator("#report-send").click()
            result = pg.locator("#report-result")
            result.wait_for(state="visible", timeout=60000)
            assert "Not sent (no inbox is set" in result.inner_text() and "problem_reports" in result.inner_text()

            ar_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ar_ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = ar_ctx.new_page()
            ar.on("pageerror", lambda exc: errors.append(str(exc)))
            ar.goto(base + "/", wait_until="domcontentloaded")
            ar.locator("a.nav-link[href='/settings']").first.click()
            ar.wait_for_function("location.pathname === '/settings'", timeout=15000)
            ar.locator("#report-problem").click()
            ar.locator("#report-dialog-title").wait_for(state="visible", timeout=10000)
            assert ar.locator("#report-dialog-title").inner_text() == "الإبلاغ عن مشكلة"
            assert ar.evaluate("document.documentElement.dir") == "rtl"
        finally:
            browser.close()
    assert errors == [], errors
