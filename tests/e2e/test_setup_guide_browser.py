"""The first-run setup guide, in a browser, from a fresh install's first visit.

A Kazma with no model shows the chat page's card; its button opens the guide
(a soft navigation, as a person's click is). The guide finds a model server
on this computer -- a stand-in Ollama on a loopback port, answering the
OpenAI-compatible calls Kazma makes -- lists its model, switches Kazma to it,
and sends it one message; the steps after it end on "Kazma is ready" with the
model named. Then the guide is opened again from Settings in Arabic: right to
left, in Arabic, and a cloud provider with no key is refused in Arabic.
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

MODEL = "tiny-model"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeOllama(BaseHTTPRequestHandler):
    """The OpenAI-compatible calls Kazma makes to a local server."""

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
        elif self.path.endswith("/api/tags"):
            self._send({"models": [{"name": MODEL}]})
        else:
            self.send_error(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path.endswith("/chat/completions"):
            self._send({"id": "x", "object": "chat.completion", "model": MODEL,
                        "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"},
                                     "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6}})
        else:
            self.send_error(404)

    def log_message(self, *args):  # quiet
        return


def _wait_live(base: str) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/health/live", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise TimeoutError("Kazma did not start")


@pytest.fixture
def fresh_kazma(monkeypatch):
    """Kazma with no model and no key, and a stand-in Ollama on a free port."""
    import uvicorn
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_core.providers import PROVIDER_PRESETS
    from kazma_ui.app import create_app

    for name in list(os.environ):
        if name.endswith("_API_KEY") or name in ("KAZMA_MODEL", "KAZMA_PROVIDER", "KAZMA_SECRET"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("KAZMA_DB_BACKEND", "sqlite")

    fake = ThreadingHTTPServer(("127.0.0.1", _free_port()), _FakeOllama)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    monkeypatch.setitem(PROVIDER_PRESETS["ollama"], "base_url", f"http://127.0.0.1:{fake.server_port}/v1")

    with TemporaryDirectory() as tmp:
        store = ConfigStore(db_path=os.path.join(tmp, "settings.db"))
        set_config_store(store)
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            _wait_live(base)
            yield base
        finally:
            server.should_exit = True
            thread.join(timeout=10.0)
            fake.shutdown()
            store.close()
            from kazma_core.shutdown import reset_shutdown, uninstall_shutdown_signal_hooks

            uninstall_shutdown_signal_hooks()
            reset_shutdown()
            from kazma_core.model_registry import reset_model_registry
            from kazma_ui.session_manager import reset_session_manager

            reset_session_manager()
            reset_model_registry()


def test_the_guide_takes_a_fresh_install_to_a_working_model(fresh_kazma: str) -> None:
    from playwright.sync_api import sync_playwright

    base = fresh_kazma
    assert httpx.get(f"{base}/api/setup/status", timeout=10).json()["ready"] is False
    host = base.split("//", 1)[1].split(":", 1)[0]
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            # English: the chat page's card -> the guide -> a model on this computer.
            pg = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US").new_page()
            pg.on("pageerror", lambda exc: errors.append(str(exc)))
            pg.goto(base + "/")
            pg.locator("#setup-wall-start").wait_for(state="visible", timeout=30000)
            pg.locator("#setup-wall-start").click()
            pg.wait_for_function("location.pathname === '/setup'", timeout=15000)
            pg.locator("#setup-step-welcome").wait_for(state="visible", timeout=15000)
            assert pg.locator("#setup-step-welcome h2").inner_text() == "Welcome to Kazma"
            pg.locator("#setup-start").click()
            pg.locator("#setup-choose-local").click()
            ollama = pg.locator(".setup-server", has_text="Ollama")
            ollama.get_by_text("Running", exact=True).wait_for(state="visible", timeout=15000)
            ollama.locator("button").click()
            pg.wait_for_function("document.querySelector('#setup-model').value === %s" % json.dumps(MODEL),
                                 timeout=20000)
            pg.locator("#setup-use-model").click()
            pg.locator("#setup-check-ok").wait_for(state="visible", timeout=60000)
            pg.locator("#setup-next").click()
            pg.locator("#setup-to-done").click()
            done = pg.locator("#setup-step-done")
            done.wait_for(state="visible")
            assert "Kazma is ready" in done.inner_text() and MODEL in done.inner_text()
            status = httpx.get(f"{base}/api/setup/status", timeout=10).json()
            assert status["ready"] is True and status["active_provider"] == "ollama"
            assert status["active_model"] == MODEL

            # Arabic: opened again from Settings, by the button there.
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            ctx.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            ar = ctx.new_page()
            ar.on("pageerror", lambda exc: errors.append(str(exc)))
            ar.goto(base + "/settings?tab=providers_connectors")
            ar.locator("#settings-setup-guide").wait_for(state="visible", timeout=30000)
            ar.locator("#settings-setup-guide").click()
            ar.wait_for_function("location.pathname === '/setup'", timeout=15000)
            ar.locator("#setup-step-welcome").wait_for(state="visible", timeout=15000)
            assert ar.evaluate("document.documentElement.dir") == "rtl"
            assert ar.locator("#setup-step-welcome h2").inner_text() == "مرحبًا بك في كاظمه"
            # Saving Ollama before the switch let Kazma fall back to it from the
            # placeholder provider; the switch ends that notice.
            assert "Model fallback" not in ar.locator("body").inner_text()
            assert "غيّر النموذج" in ar.locator("#setup-start").inner_text()  # ready: change it
            ar.locator("#setup-start").click()
            ar.locator("#setup-choose-cloud").click()
            # The list shows the provider the guide uses: a fresh install's
            # placeholder ("custom") was chosen while the list showed OpenAI.
            assert ar.evaluate(
                "Alpine.$data(document.querySelector('.setup-guide')).provider"
            ) == ar.locator("#setup-provider").input_value() == "openai"
            ar.locator("#setup-provider").select_option("deepseek")
            assert ar.locator("a[href='https://platform.deepseek.com/api_keys']").is_visible()
            ar.locator("#setup-connect").click()
            ar.locator("#setup-error").wait_for(state="visible", timeout=15000)
            assert "أدخل مفتاح API الخاص بالمزوّد." in ar.locator("#setup-error").inner_text()
        finally:
            browser.close()
    assert errors == [], errors
