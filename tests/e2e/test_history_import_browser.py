"""Bring your history in a real browser (REMAINING 4.2): the Memory page,
reached by the sidebar, reads a ChatGPT export and says what it holds, imports
it and says what it remembered. Arabic too.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import zipfile
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

    from tests._no_models import keep_models_out

    keep_models_out(monkeypatch, llm=False)
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


def test_the_memory_page_brings_a_chatgpt_export_in(kazma, tmp_path) -> None:
    from playwright.sync_api import sync_playwright

    from tests.test_history_import import chatgpt_export

    export = tmp_path / "chatgpt-export.zip"
    with zipfile.ZipFile(export, "w") as z:
        z.writestr("conversations.json", json.dumps(chatgpt_export()))
    base = kazma
    host = base.split("//", 1)[1].split(":", 1)[0]
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US").new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.goto(base + "/", wait_until="domcontentloaded")
            pg.locator("a.nav-link[href='/memory']").first.click()
            pg.wait_for_function("location.pathname === '/memory'", timeout=15000)
            card = pg.locator("#history-import")
            card.scroll_into_view_if_needed()
            assert pg.locator("#history-import-read").is_disabled(), "nothing to read before a file is chosen"
            pg.locator("#history-import-file").set_input_files(str(export))
            pg.locator("#history-import-read").click()
            summary = pg.locator("#history-import-summary")
            summary.wait_for(state="visible", timeout=30000)
            assert "A ChatGPT export: 1 conversation, 2 turns" in summary.inner_text()
            pg.locator("#history-import-start").click()
            result = pg.locator("#history-import-result")
            result.wait_for(state="visible", timeout=60000)
            assert "Remembered: 2" in result.inner_text(), result.inner_text()

            ar_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ar_ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = ar_ctx.new_page()
            ar.on("pageerror", lambda exc: errors.append(str(exc)))
            ar.goto(base + "/", wait_until="domcontentloaded")
            ar.locator("a.nav-link[href='/memory']").first.click()
            ar.wait_for_function("location.pathname === '/memory'", timeout=15000)
            assert ar.locator("#history-import-title").inner_text() == "أحضر سجلّك"
        finally:
            browser.close()
    assert errors == [], errors
