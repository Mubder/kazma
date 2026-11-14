"""Kazma as an app, in a real browser (REMAINING 4.1): Chrome's own
installability check finds nothing missing on a page reached by the sidebar,
the manifest it reads is Kazma's, and with the network gone a navigation gets
the offline page from the service worker.
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


def test_chrome_finds_kazma_installable_and_shows_the_offline_page(kazma) -> None:
    from playwright.sync_api import sync_playwright

    base = kazma
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
            pg = ctx.new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.goto(base + "/", wait_until="domcontentloaded")
            pg.locator("a.nav-link[href='/chat']").first.click()
            pg.wait_for_function("location.pathname === '/chat'", timeout=15000)
            # The service worker is active (registered at load, claims the page).
            pg.wait_for_function(
                "navigator.serviceWorker.getRegistration().then(r => !!(r && r.active))", timeout=20000)
            cdp = ctx.new_cdp_session(pg)
            got = cdp.send("Page.getAppManifest")
            assert got.get("url", "").endswith("/manifest.webmanifest"), got
            assert not got.get("errors"), got.get("errors")
            assert '"name": "Kazma"' in got["data"] or '"name":"Kazma"' in got["data"]
            installability = cdp.send("Page.getInstallabilityErrors")
            assert installability.get("installabilityErrors") == [], installability
            # The network goes: a navigation is answered with the offline page.
            ctx.set_offline(True)
            pg.goto(base + "/dashboard", wait_until="domcontentloaded")
            assert "Kazma is not answering right now" in pg.content()
            ctx.set_offline(False)
        finally:
            browser.close()
    assert errors == [], errors
