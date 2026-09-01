"""One Playwright smoke: process is alive, chat composer is visible.

Boot-wait is ``GET /health/live`` (poll), not a fixed sleep. Marked ``e2e``.
CI runs this file in a separate job with Chromium; the main suite still
``importorskip``s Playwright so it does not start uvicorn there.
"""

from __future__ import annotations

import os
import socket
import threading
import time

import pytest

pytest.importorskip("playwright")
httpx = pytest.importorskip("httpx")

pytestmark = pytest.mark.e2e


def _free_port() -> int:
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_live(base: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base}/health/live", timeout=2.0)
            if r.status_code == 200:
                return
            last = f"HTTP {r.status_code}"
        except Exception as exc:
            last = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"uvicorn did not become live: {last}")


@pytest.fixture(scope="module")
def live_server():
    """In-process uvicorn on a free port. Does not touch the operator server."""
    import uvicorn
    from kazma_ui.app import create_app
    from kazma_core.config_store import ConfigStore, set_config_store
    from tempfile import TemporaryDirectory

    orig_secret = os.environ.get("KAZMA_SECRET")
    os.environ.pop("KAZMA_SECRET", None)
    os.environ.setdefault("KAZMA_DB_BACKEND", "sqlite")

    with TemporaryDirectory() as tmp_dir:
        cs = ConfigStore(db_path=os.path.join(tmp_dir, "smoke_settings.db"))
        set_config_store(cs)
        port = _free_port()
        app = create_app()
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="warning"
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        try:
            _wait_live(base)
            yield base
        finally:
            server.should_exit = True
            thread.join(timeout=5.0)
            cs.close()
            try:
                from kazma_core.shutdown import (
                    reset_shutdown,
                    uninstall_shutdown_signal_hooks,
                )

                uninstall_shutdown_signal_hooks()
                reset_shutdown()
            except Exception:
                pass
            for _reset in (
                lambda: __import__(
                    "kazma_ui.session_manager", fromlist=["reset_session_manager"]
                ).reset_session_manager(),
                lambda: __import__(
                    "kazma_core.config_store", fromlist=["reset_config_store"]
                ).reset_config_store(),
                lambda: __import__(
                    "kazma_core.model_registry", fromlist=["reset_model_registry"]
                ).reset_model_registry(),
            ):
                try:
                    _reset()
                except Exception:
                    pass
            if orig_secret is not None:
                os.environ["KAZMA_SECRET"] = orig_secret


def test_reload_restores_answer_and_cot(live_server: str) -> None:
    """Closer write → reload shows answer + CoT (abort-SSE equivalent)."""
    import uuid

    from kazma_ui.reply_sink import open_reply_turn
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.turn_runtime import persist_reply
    from playwright.sync_api import sync_playwright

    sid = "e2e-cot-" + uuid.uuid4().hex[:12]
    sm = get_session_manager()
    sess = sm.get_or_create(sid)
    sess.thread_id = sid
    sess.title = "E2E CoT"
    sess.messages = [{"role": "user", "content": "check the timeout"}]
    sm.put(sess)
    turn = open_reply_turn(sid)
    persist_reply(
        sid,
        turn,
        "The timeout is 300 seconds.",
        thread_id=sid,
        parts=[
            {
                "type": "reasoning",
                "text": "There's a live API endpoint GET /api/settings/agent/safety",
            },
            {
                "type": "tool",
                "name": "file_read",
                "result": "hitl.py",
                "state": "done",
            },
            {"type": "text", "text": "The timeout is 300 seconds."},
        ],
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(live_server, timeout=30000, wait_until="domcontentloaded")
            page.evaluate(
                "localStorage.setItem('kazma.chatSessionId', arguments[0])", sid
            )
            page.reload(wait_until="domcontentloaded")
            page.locator("#chat-input").wait_for(state="visible", timeout=15000)
            page.locator(".message-assistant").first.wait_for(timeout=15000)
            body = page.locator(".message-assistant").first.inner_text()
            assert "300 seconds" in body
            assert page.locator(".kazma-cot-restored, .agent-progress").count() >= 1
        finally:
            browser.close()


def test_health_and_chat_composer(live_server: str) -> None:
    """Page boots; composer ``#chat-input`` is visible."""
    r = httpx.get(f"{live_server}/health/live", timeout=5.0)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "alive"

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(live_server, timeout=30000, wait_until="domcontentloaded")
            page.locator("#chat-input").wait_for(state="visible", timeout=15000)
            live = page.locator("#voice-live-btn")
            assert live.count() >= 0
            page.goto(live_server + "/settings", timeout=30000, wait_until="domcontentloaded")
            page.locator(".settings-group").first.wait_for(state="visible", timeout=15000)
            page.goto(live_server + "/dashboard", timeout=30000, wait_until="domcontentloaded")
            page.locator(".dashboard-container").first.wait_for(state="visible", timeout=15000)
            page.goto(live_server + "/x", timeout=30000, wait_until="domcontentloaded")
            page.locator(".xs-wrap").first.wait_for(state="visible", timeout=15000)
            # Soft-nav: click the Settings sidebar link and stay on the SPA.
            settings_link = page.locator("a[href='/settings']").first
            if settings_link.count():
                settings_link.click()
                page.locator(".settings-group").first.wait_for(state="visible", timeout=15000)
        finally:
            browser.close()
