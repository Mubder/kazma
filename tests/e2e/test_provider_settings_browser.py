"""Settings' provider controls in a browser, reached by the sidebar
(providers stage 1, 2026-11-08).

- Model prices lists a model used with no known price; "Set its price" and
  Save keep the price.
- The Add provider dialog tests the values as typed: Save opens only when the
  test answered (a model server on this computer, no key), and a test that
  fails leaves Save shut and offers "Save anyway".
- The card reads in Arabic, right to left.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from tempfile import TemporaryDirectory

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")
httpx = pytest.importorskip("httpx")

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

MODEL = "fake-model"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeServer(BaseHTTPRequestHandler):
    """An OpenAI-compatible model server with no key."""

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 -- http.server's name
        if self.path.endswith("/models"):
            self._send({"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/chat/completions"):
            self._send({"id": "x", "object": "chat.completion", "model": MODEL,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})
        else:
            self.send_error(404)

    def log_message(self, *args):
        return


@pytest.fixture
def kazma(monkeypatch):
    import uvicorn
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_ui.app import create_app

    for name in list(os.environ):
        if name.endswith("_API_KEY") or name in ("KAZMA_MODEL", "KAZMA_PROVIDER", "KAZMA_SECRET"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")
    fake = ThreadingHTTPServer(("127.0.0.1", _free_port()), _FakeServer)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
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
            yield base, f"http://127.0.0.1:{fake.server_port}/v1"
        finally:
            server.should_exit = True
            thread.join(timeout=10.0)
            fake.shutdown()
            store.close()
            from kazma_core.model_registry import reset_model_registry
            from kazma_core.shutdown import reset_shutdown, uninstall_shutdown_signal_hooks
            from kazma_ui.session_manager import reset_session_manager

            uninstall_shutdown_signal_hooks()
            reset_shutdown()
            reset_session_manager()
            reset_model_registry()


def test_provider_settings_in_a_browser(kazma) -> None:
    from kazma_core.observability import llm_ledger
    from playwright.sync_api import sync_playwright

    base, fake_url = kazma
    llm_ledger.record_llm_call(provider="deepseek", model="mystery-model", prompt_tokens=10, priced=False)
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

            # Model prices: the model used with no known price, priced here.
            prices = pg.locator("#model-prices")
            prices.get_by_text("mystery-model", exact=True).wait_for(state="visible", timeout=30000)
            prices.locator("button", has_text="Set its price").first.click()
            row = prices.locator(".model-price-row").last
            assert row.locator("input[type=text]").input_value() == "mystery-model"
            row.locator("input[type=number]").nth(0).fill("0.27")
            row.locator("input[type=number]").nth(1).fill("1.1")
            pg.locator("#model-prices-save").click()
            pg.wait_for_function(
                "fetch('/api/settings/model-prices').then(r => r.json()).then(d => d.prices.length === 1)",
                timeout=15000)
            saved = httpx.get(f"{base}/api/settings/model-prices", timeout=10).json()["prices"]
            assert saved == [{"model": "mystery-model", "input": 0.27, "output": 1.1}]

            # The dialog: Save opens only when the test answered.
            pg.locator("#provider-add").click()
            pg.locator("#provider-name").fill("fakeserver")
            pg.locator("#provider-base-url").fill(fake_url)
            pg.locator("#provider-models").fill(MODEL)
            assert pg.locator("#provider-save").is_disabled()
            pg.locator("#provider-test").click()
            pg.wait_for_function("!document.querySelector('#provider-save').disabled", timeout=30000)
            names = {p_["name"] for p_ in httpx.get(f"{base}/api/providers", timeout=10).json()}
            assert "fakeserver" not in names, "the test saved the provider"
            pg.locator("#provider-save").click()
            pg.wait_for_function(
                "fetch('/api/providers').then(r => r.json()).then(l => l.some(p => p.name === 'fakeserver' && p.enabled))",
                timeout=15000)

            # A test that fails: Save stays shut, "Save anyway" is offered.
            pg.locator("#provider-add").click()
            pg.locator("#provider-name").fill("deadserver")
            pg.locator("#provider-base-url").fill(f"http://127.0.0.1:{_free_port()}/v1")
            pg.locator("#provider-models").fill(MODEL)
            pg.locator("#provider-test").click()
            pg.locator("#provider-save-anyway").wait_for(state="visible", timeout=30000)
            assert pg.locator("#provider-save").is_disabled()

            # Arabic: the card reads right to left.
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = ctx.new_page()
            ar.on("pageerror", lambda exc: errors.append(str(exc)))
            ar.goto(base + "/settings?tab=providers_connectors", wait_until="domcontentloaded")
            ar.locator("#model-prices h3").wait_for(state="visible", timeout=30000)
            assert ar.evaluate("document.documentElement.dir") == "rtl"
            assert ar.locator("#model-prices h3").inner_text() == "أسعار النماذج"
        finally:
            browser.close()
    assert errors == [], errors


def test_stage_two_to_four_in_a_browser(kazma) -> None:
    """Providers stages 2-4, reached by the sidebar: the dialog reads the
    server's catalog and shows each provider type's own form; the Model
    details card and the default model; and the chat's picker changes only
    the chat it is in."""
    from playwright.sync_api import sync_playwright

    base, fake_url = kazma
    saved = httpx.post(f"{base}/api/providers", json={
        "name": "fakeserver", "base_url": fake_url, "models": [MODEL, "fake-two"], "enabled": True}, timeout=10)
    assert saved.status_code == 200, saved.text
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US").new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.goto(base + "/", wait_until="domcontentloaded")
            pg.locator("a.nav-link[href='/settings']").first.click()
            pg.wait_for_function("location.pathname === '/settings'", timeout=15000)

            # The dialog's presets are the server's catalog, each with its form.
            pg.locator("#provider-add").click()
            preset = pg.locator(".modal select.form-select").first
            preset.locator("option[value='cerebras']").wait_for(state="attached", timeout=15000)
            preset.select_option("cerebras")
            assert pg.locator("#provider-base-url").input_value() == "https://api.cerebras.ai/v1"
            preset.select_option("bedrock")
            pg.locator("#provider-region").wait_for(state="visible", timeout=5000)
            assert not pg.locator("#provider-base-url").is_visible()
            preset.select_option("openrouter")
            pg.locator(".provider-advanced summary").click()
            pg.locator("#provider-add-header").click()
            assert pg.locator(".provider-header-row").count() == 1
            pg.locator("#provider-add-key").click()
            assert pg.locator(".provider-key-row").count() == 1
            pg.keyboard.press("Escape")
            pg.locator(".modal-overlay").first.evaluate("e => e.click()")

            # Model details and the default model.
            details = pg.locator("#model-details")
            details.locator(".model-detail-row span", has_text=f"fakeserver:{MODEL}").first.wait_for(
                state="visible", timeout=30000)
            assert "Free (on your network)" in details.inner_text()
            pg.locator("#default-model-select").select_option(f"fakeserver:{MODEL}")
            pg.wait_for_function(
                f"fetch('/api/provider/active').then(r => r.json()).then(d => d.model === '{MODEL}')",
                timeout=15000)

            # The chat's picker: this chat's model, never the install's.
            pg.locator("a.nav-link[href='/chat']").first.click()
            pg.wait_for_function("location.pathname === '/chat'", timeout=15000)
            picker = pg.locator("#model-selector")
            picker.locator("option[value='fakeserver:fake-two']").wait_for(state="attached", timeout=30000)
            pg.wait_for_function(
                f"document.querySelector('#model-selector').value === 'fakeserver:{MODEL}'", timeout=15000)
            picker.select_option("fakeserver:fake-two")
            active = httpx.get(f"{base}/api/provider/active", timeout=10).json()
            assert active["model"] == MODEL, "the chat's picker changed the install's default"
            pg.locator("#new-session-btn").click()
            pg.wait_for_function(
                f"document.querySelector('#model-selector').value === 'fakeserver:{MODEL}'", timeout=15000)
        finally:
            browser.close()
    assert errors == [], errors
