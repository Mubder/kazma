"""The WhatsApp card in Settings, in a browser, reached by the sidebar
(2026-11-14).

- The card shows the webhook address Meta must call (KAZMA_PUBLIC_URL).
- "Make one" fills a long random verify token.
- Save keeps every field; the secrets come back masked, never as typed.
- Test runs WhatsApp's own diagnosis (Meta answered by a stand-in) and every
  check has a title on the card.
- The card reads in Arabic, right to left.
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

PUBLIC = "https://my.kazma.example"
PHONE_ID = "109876543210"
SECRET = "app-secret-for-browser"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def kazma(monkeypatch):
    import uvicorn
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_gateway.adapters import whatsapp_api
    from kazma_ui.app import create_app

    async def meta_answers(self):  # Meta's answer about the number, without Meta
        return {"display_phone_number": "+965 2222 3333", "verified_name": "Kazma Test",
                "quality_rating": "GREEN", "id": PHONE_ID}

    monkeypatch.setattr(whatsapp_api.WhatsAppClient, "phone_number", meta_answers)
    for name in list(os.environ):
        if name.endswith("_API_KEY") or name in ("KAZMA_MODEL", "KAZMA_PROVIDER", "KAZMA_SECRET"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    monkeypatch.setenv("KAZMA_PUBLIC_URL", PUBLIC)
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
            yield base, store
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


def _open_card(page, base: str) -> None:
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("a.nav-link[href='/settings']").first.click()
    page.wait_for_function("location.pathname === '/settings'", timeout=15000)
    page.locator("#hub-subtab-connectors").click()
    page.locator("#whatsapp-card").wait_for(state="visible", timeout=30000)


def test_the_whatsapp_card_in_a_browser(kazma) -> None:
    from playwright.sync_api import sync_playwright

    base, store = kazma
    host = base.split("//", 1)[1].split(":", 1)[0]
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
            pg = ctx.new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            _open_card(pg, base)
            card = pg.locator("#whatsapp-card")
            assert pg.locator("#wa-webhook").input_value() == f"{PUBLIC}/api/webhooks/whatsapp"

            pg.locator("#wa-token").fill("EAAG-browser-token")
            pg.locator("#wa-phone-id").fill(PHONE_ID)
            pg.locator("#wa-app-secret").fill(SECRET)
            pg.locator("#wa-make-verify").click()
            verify = pg.locator("#wa-verify-token").input_value()
            assert len(verify) == 48 and all(c in "0123456789abcdef" for c in verify)
            pg.locator("#wa-allowed").fill("+965 9999 1234")
            pg.locator("#adapter-routing-save").click()
            pg.wait_for_function(
                "fetch('/api/connectors').then(r => r.json()).then(l => l.some(c => c.name === 'whatsapp' "
                "&& (c.extras || {}).phone_number_id === '" + PHONE_ID + "'))",
                timeout=15000)
            assert store.get("connectors.whatsapp.token") == "EAAG-browser-token"
            assert store.get("connectors.whatsapp.app_secret") == SECRET
            assert store.get("connectors.whatsapp.verify_token") == verify
            assert store.get("connectors.whatsapp.allowed_users") == "+965 9999 1234"
            listed = next(c for c in httpx.get(f"{base}/api/connectors", timeout=10).json()
                          if c["name"] == "whatsapp")
            assert SECRET not in str(listed) and verify not in str(listed)

            # Test: WhatsApp's own checks, each with its title on the card.
            pg.locator("#wa-test").click()
            card.get_by_text("24-hour window", exact=True).wait_for(state="visible", timeout=30000)
            text = card.inner_text()
            for title in ("App secret", "Webhook", "Allowed users", "Kazma is listening"):
                assert title in text, title
            assert "Kazma Test" in text  # Meta's answer about the number
            assert f"{PUBLIC}/api/webhooks/whatsapp" in text

            # Arabic: the card reads right to left, in its own words.
            ar_ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ar_ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = ar_ctx.new_page()
            ar.on("pageerror", lambda exc: errors.append(str(exc)))
            _open_card(ar, base)
            assert ar.evaluate("document.documentElement.dir") == "rtl"
            ar_text = ar.locator("#whatsapp-card").inner_text()
            assert "رمز الوصول" in ar_text and "الأرقام المسموح لها" in ar_text
        finally:
            browser.close()
    assert errors == [], errors
