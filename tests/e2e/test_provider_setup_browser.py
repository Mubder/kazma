"""The guided provider setup in a browser, reached by the sidebar, in English
and Arabic (2026-11-25).

- A pasted key's shape names its provider; the address is chosen; nothing is
  sent to the provider until the person asks for its models, and one message
  only when they press for it; the answer and the price are shown; then the
  models to show and the default.
- What was typed is never lost: a click outside and Escape ask, "Close for
  now" keeps it, and a reload resumes it with no key in the browser's storage.
- A provider that is there already takes the key instead of a second row, and
  rows that repeat each other are tidied.
- The dialogs beside it (connector, MCP) ask before dropping typed input.

``KAZMA_SETUP_SHOTS`` names a folder to keep a screenshot of every step in.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")
httpx = pytest.importorskip("httpx")

from tests.e2e.test_provider_settings_browser import kazma  # noqa: E402,F401 -- the server fixture

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

KEY = "sk-or-demo-browser-key"
SECOND_KEY = "sk-or-demo-second-key"


class _Provider(BaseHTTPRequestHandler):
    """An OpenAI-compatible provider that records what it was asked."""

    asked: list[tuple[str, str]] = []

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _key(self) -> str:
        return (self.headers.get("Authorization") or "").removeprefix("Bearer ")

    def do_GET(self):  # noqa: N802 -- http.server's name
        self.asked.append(("GET " + self.path, self._key()))
        if self._key() == "sk-or-demo-refused-key":
            self._send(401, {"error": {"message": "no"}})
        else:
            self._send(200, {"object": "list", "data": [{"id": "model-a"}, {"id": "model-b"}]})

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.asked.append(("POST " + self.path, self._key()))
        self._send(200, {"id": "x", "object": "chat.completion", "model": "model-a",
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK, I am here."},
                                      "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10}})

    def log_message(self, *args):
        return


@pytest.fixture
def provider():
    _Provider.asked = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/v1", _Provider
    server.shutdown()


def _shot(page, name: str) -> None:
    folder = os.environ.get("KAZMA_SETUP_SHOTS")
    if folder:
        page.wait_for_timeout(350)
        page.screenshot(path=os.path.join(folder, name + ".png"))


def _open_settings(browser, base: str, lang: str, errors: list[str]):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_cookies([{"name": "kazma-lang", "value": lang, "domain": base.split("//", 1)[1].split(":", 1)[0], "path": "/"}])
    page = ctx.new_page()
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.goto(base + "/", wait_until="domcontentloaded")
    page.locator("a.nav-link[href='/settings']").first.click()
    page.wait_for_function("location.pathname === '/settings'", timeout=15000)
    page.locator("#provider-add").wait_for(state="visible", timeout=30000)
    page.locator(".pc-row").first.wait_for(state="visible", timeout=30000)
    return page


def _outside(page) -> None:
    """A click on the overlay, beside the dialog."""
    page.mouse.click(15, 450)


def _to_key(page, url: str, key: str, lang: str = "en", shots: str = "") -> None:
    page.locator("#provider-add").click()
    page.locator("#setup-search").wait_for(state="visible", timeout=15000)
    if shots:
        _shot(page, f"{shots}_1_provider_{lang}")
    page.locator("#setup-search").fill(key)
    page.locator("#setup-guess button", has_text="OpenRouter").click()
    page.locator("input[name='setup-endpoint'][value='custom']").check()
    page.locator("#setup-custom-url").fill(url)
    if shots:
        _shot(page, f"{shots}_2_address_{lang}")
    page.locator("#setup-next").click()


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_provider_is_set_up_step_by_step(kazma, provider, lang) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    base, _ = kazma
    url, server = provider
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = _open_settings(browser, base, lang, errors)
            if lang == "ar":
                assert page.evaluate("document.documentElement.dir") == "rtl"
            # Regions are the provider's own, the custom address last.
            page.locator("#provider-add").click()
            page.locator(".ps-tile[data-provider='dashscope']").click()
            choices = page.locator("input[name='setup-endpoint']")
            page.wait_for_function("document.querySelectorAll(\"input[name='setup-endpoint']\").length === 6")
            assert choices.first.get_attribute("value") == "singapore"
            assert choices.last.get_attribute("value") == "custom"
            page.locator("input[name='setup-endpoint'][value='workspace']").check()
            page.locator("#setup-field-workspace_id").wait_for(state="visible", timeout=5000)
            _shot(page, f"step_2_regions_{lang}")
            # A missing field is said, before anything goes on.
            page.locator("#setup-next").click()
            page.wait_for_function("[...document.querySelectorAll('#provider-setup .text-error')].some(e => e.offsetParent && e.textContent.trim())")
            page.locator("#setup-back").click()
            page.locator("#setup-search").fill("zzzz")
            page.locator(".ps-tile").first.wait_for(state="detached", timeout=5000)
            page.locator("#setup-search").fill("")

            # A pasted key names its provider; the key follows it.
            _shot(page, f"step_1_provider_{lang}")
            page.locator("#setup-search").fill(KEY)
            page.locator("#setup-guess button", has_text="OpenRouter").click()
            page.locator("input[name='setup-endpoint'][value='custom']").check()
            page.locator("#setup-custom-url").fill(url)
            _shot(page, f"step_2_address_{lang}")
            page.locator("#setup-next").click()
            page.locator("#setup-key").wait_for(state="visible", timeout=15000)
            assert page.locator("#setup-key").input_value() == KEY
            assert page.locator("#setup-key-link").get_attribute("href") == "https://openrouter.ai/keys"
            _shot(page, f"step_3_key_{lang}")
            assert server.asked == [], "the provider was asked before the person asked for it"

            page.locator("#setup-next").click()
            page.locator("#setup-test").wait_for(state="visible", timeout=30000)
            assert [a for a, _k in server.asked] == ["GET /v1/models"], "a message was sent unasked"
            assert page.locator("#setup-next").is_disabled()
            page.locator("#setup-model").fill("model-a")
            page.locator("#setup-test").click()
            page.locator("#setup-answer").wait_for(state="visible", timeout=30000)
            assert page.locator(".ps-answer-text").inner_text() == "OK, I am here."
            assert server.asked[1:] == [("POST /v1/chat/completions", KEY)]
            _shot(page, f"step_4_check_{lang}")
            names = {r["name"] for r in httpx.get(f"{base}/api/providers", timeout=10).json()}
            row = next(r for r in httpx.get(f"{base}/api/providers", timeout=10).json() if r["name"] == "openrouter")
            assert "openrouter" in names and not row["enabled"], "the test saved the provider"

            page.locator("#setup-next").click()
            page.locator("#setup-default").wait_for(state="visible", timeout=30000)
            page.wait_for_function("document.querySelectorAll('.ps-models input').length === 2")
            page.locator(".ps-models label", has_text="model-b").locator("input").uncheck()
            page.locator("#setup-default").select_option("model-a")
            _shot(page, f"step_5_use_{lang}")
            page.locator("#setup-next").click()
            page.locator("#provider-setup").wait_for(state="hidden", timeout=15000)
            row = next(r for r in httpx.get(f"{base}/api/providers", timeout=10).json() if r["name"] == "openrouter")
            assert row["enabled"] and row["selected_models"] == ["model-a"] and KEY not in json.dumps(row)
            assert httpx.get(f"{base}/api/provider/active", timeout=10).json()["model"] == "model-a"
            assert page.evaluate("localStorage.getItem('kazma.providerSetup.draft')") is None
            text = page.evaluate("document.body.innerText")
            assert "settings.setup" not in text and "{name}" not in text
        finally:
            browser.close()
    assert errors == [], errors


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_what_was_typed_is_never_lost(kazma, provider, lang) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    base, _ = kazma
    url, server = provider
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = _open_settings(browser, base, lang, errors)
            _to_key(page, url, KEY)
            page.locator("#setup-key").wait_for(state="visible", timeout=15000)

            # Outside: asked; "Go on" and Escape on the question keep it all.
            page.evaluate("""() => { const real = window.kazmaConfirm; window.__asks = 0;
                window.kazmaConfirm = (o) => { window.__asks += 1; return real(o); }; }""")
            modal = page.locator(".modal-overlay:has(#modal-title)")
            _outside(page)
            modal.wait_for(state="visible", timeout=5000)
            _shot(page, f"close_question_{lang}")
            modal.locator(".modal-footer .btn").first.click()
            modal.wait_for(state="detached", timeout=5000)
            assert page.locator("#setup-key").input_value() == KEY
            page.keyboard.press("Escape")
            modal.wait_for(state="visible", timeout=5000)
            page.keyboard.press("Escape")
            modal.wait_for(state="detached", timeout=5000)
            # The same key press asked nothing more: the question has not come back.
            page.evaluate("new Promise(r => requestAnimationFrame(() => setTimeout(r, 0)))")
            assert page.evaluate("window.__asks") == 2
            assert page.locator("#setup-key").is_visible() and modal.count() == 0

            # Closed for now: the same page still has the key.
            _outside(page)
            modal.locator(".modal-footer .btn").last.click()
            page.locator("#provider-setup").wait_for(state="hidden", timeout=5000)
            page.locator("#provider-add").click()
            page.locator("#setup-key").wait_for(state="visible", timeout=5000)
            assert page.locator("#setup-key").input_value() == KEY
            _shot(page, f"resumed_{lang}")

            # A reload resumes it; no key is anywhere in the browser's storage.
            stored = page.evaluate("JSON.stringify(Object.assign({}, localStorage)) + JSON.stringify(Object.assign({}, sessionStorage))")
            assert "openrouter" in stored and KEY not in stored
            page.reload(wait_until="domcontentloaded")
            page.locator(".pc-row").first.wait_for(state="visible", timeout=30000)
            page.locator("#provider-add").click()
            page.locator("#setup-custom-url").wait_for(state="visible", timeout=15000)
            assert page.locator("#setup-custom-url").input_value() == url
            page.locator("#setup-next").click()
            page.locator("#setup-key").wait_for(state="visible", timeout=15000)
            assert page.locator("#setup-key").input_value() == ""

            # Discarded: nothing is left of it.
            _outside(page)
            modal.locator("input[type=checkbox]").check()
            modal.locator(".modal-footer .btn").last.click()
            page.locator("#provider-setup").wait_for(state="hidden", timeout=5000)
            page.locator("#provider-add").click()
            page.locator("#setup-search").wait_for(state="visible", timeout=5000)
            assert page.evaluate("localStorage.getItem('kazma.providerSetup.draft')") is None
            assert server.asked == []
            # Nothing typed: it closes without a question.
            _outside(page)
            page.locator("#provider-setup").wait_for(state="hidden", timeout=5000)
        finally:
            browser.close()
    assert errors == [], errors


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_one_row_per_provider_and_the_list_is_tidied(kazma, provider, lang) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    base, _ = kazma
    url, _server = provider
    for row in ({"name": "Router A", "preset": "openrouter", "base_url": url, "api_key": KEY, "models": ["model-a"]},
                {"name": "leftover", "base_url": "https://leftover.example/v1", "enabled": False}):
        assert httpx.post(f"{base}/api/providers", json=row, timeout=10).status_code == 200
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = _open_settings(browser, base, lang, errors)
            _to_key(page, url, SECOND_KEY)
            page.locator("#setup-same").wait_for(state="visible", timeout=15000)
            _shot(page, f"same_address_{lang}")
            assert page.locator("#setup-next").is_disabled()
            page.locator(".ps-use-row").first.click()
            page.locator("#setup-next").click()
            page.locator("#setup-test").wait_for(state="visible", timeout=30000)
            page.locator("#setup-model").fill("model-a")
            page.locator("#setup-test").click()
            page.locator("#setup-answer").wait_for(state="visible", timeout=30000)
            page.locator("#setup-next").click()
            page.locator("#setup-saved").wait_for(state="visible", timeout=30000)
            page.locator("#setup-default").wait_for(state="visible", timeout=30000)
            page.locator("#setup-next").click()
            page.locator("#provider-setup").wait_for(state="hidden", timeout=15000)
            rows = httpx.get(f"{base}/api/providers", timeout=10).json()
            at = [r for r in rows if r["base_url"] == url]
            assert [r["name"] for r in at] == ["Router A"] and len(at[0]["api_keys"]) == 1

            # A second row made elsewhere (the API), and the page tidies it.
            assert httpx.post(f"{base}/api/providers", timeout=10, json={
                "name": "Router B", "preset": "openrouter", "base_url": url, "api_key": "sk-or-demo-third-key",
                "models": ["model-b"]}).status_code == 200
            page.reload(wait_until="domcontentloaded")
            page.locator("#provider-tidy-open").click()
            page.locator(".tidy-merge").wait_for(state="visible", timeout=10000)
            page.locator(".tidy-unused", has_text="leftover").locator("input").check()
            _shot(page, f"tidy_{lang}")
            page.locator("#tidy-run").click()
            page.locator(".modal-overlay:has(#modal-title) .modal-footer .btn").last.click()
            page.wait_for_function(
                "fetch('/api/providers').then(r => r.json()).then(l => !l.some(p => p.name === 'leftover'))", timeout=15000)
            rows = {r["name"]: r for r in httpx.get(f"{base}/api/providers", timeout=10).json()}
            names = set(rows)
            kept = names & {"Router A", "Router B"}
            assert len(kept) == 1, names
            row = rows[kept.pop()]
            assert len(row["api_keys"]) == 2 and set(row["models"]) >= {"model-a", "model-b"}
            page.locator("#provider-tidy-open").wait_for(state="hidden", timeout=10000)
        finally:
            browser.close()
    assert errors == [], errors


def test_a_refused_key_is_said_and_nothing_is_saved(kazma, provider) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    base, _ = kazma
    url, server = provider
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = _open_settings(browser, base, "en", errors)
            _to_key(page, url, "sk-or-demo-refused-key")
            page.locator("#setup-key").wait_for(state="visible", timeout=15000)
            page.locator("#setup-next").click()
            page.locator("#setup-error").wait_for(state="visible", timeout=30000)
            assert "refused" in page.locator("#setup-error").inner_text().lower()
            assert page.locator("#setup-key").is_visible()
            # The key's shape is another provider's: said where it is typed.
            page.locator("#setup-key").fill("gsk_demo_browser_key")
            page.locator("#setup-mismatch button", has_text="Groq").wait_for(state="visible", timeout=5000)
            row = next(r for r in httpx.get(f"{base}/api/providers", timeout=10).json() if r["name"] == "openrouter")
            assert not row["enabled"] and not row["api_key"]
        finally:
            browser.close()
    assert errors == [], errors


def test_the_dialogs_beside_it_ask_too(kazma) -> None:  # noqa: F811
    """The connector and the MCP dialogs, typed into and clicked outside."""
    from playwright.sync_api import sync_playwright

    base, _ = kazma
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = _open_settings(browser, base, "en", errors)
            modal = page.locator(".modal-overlay:has(#modal-title)")
            page.evaluate("Alpine.$data(document.querySelector('[x-data*=\"settingsApp\"]')).openHubProviderModal('openai')")
            field = page.locator("#f-hub-editing-provider-display-name")
            field.wait_for(state="visible", timeout=10000)
            # Nothing typed: closes.
            _outside(page)
            field.wait_for(state="hidden", timeout=5000)
            page.evaluate("Alpine.$data(document.querySelector('[x-data*=\"settingsApp\"]')).openHubProviderModal('openai')")
            field.fill("My OpenAI")
            _outside(page)
            modal.wait_for(state="visible", timeout=5000)
            _shot(page, "sibling_question_en")
            modal.locator(".modal-footer .btn").first.click()
            modal.wait_for(state="detached", timeout=5000)
            assert field.input_value() == "My OpenAI"
            page.keyboard.press("Escape")
            modal.wait_for(state="visible", timeout=5000)
            modal.locator(".modal-footer .btn").last.click()
            field.wait_for(state="hidden", timeout=5000)

            page.locator("a.nav-link[href='/mcp']").first.click()
            page.wait_for_function("location.pathname === '/mcp'", timeout=15000)
            page.evaluate("""() => { const el = [...document.querySelectorAll('[x-data]')]
                .find(e => Alpine.$data(e).showAddModal !== undefined); Alpine.$data(el).showAddModal = true; }""")
            server_name = page.locator("#f-new-server-name")
            server_name.fill("my-server")
            _outside(page)
            modal.wait_for(state="visible", timeout=5000)
            modal.locator(".modal-footer .btn").first.click()
            assert server_name.input_value() == "my-server"
        finally:
            browser.close()
    assert errors == [], errors
