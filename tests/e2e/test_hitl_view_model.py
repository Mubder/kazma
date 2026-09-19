"""F0 — HITL view-model Playwright: incidents 2 and 3 against today's tree.

Plan: ``docs/plans/HITL_VIEW_MODEL.md``. These tests encode the *desired*
refresh behaviour. They may fail on current ``main`` (hydrate paints
``awaiting`` before ``/status``). They are NOT in the Playwright CI job yet;
A1 is the PR that must turn them green.

Does not start the operator's live server. In-process uvicorn, isolated
``KAZMA_DATA_DIR``.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from tempfile import TemporaryDirectory

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
    import uvicorn
    from kazma_core.config_store import ConfigStore, set_config_store
    from kazma_ui.app import create_app

    orig_secret = os.environ.get("KAZMA_SECRET")
    orig_data = os.environ.get("KAZMA_DATA_DIR")
    os.environ.pop("KAZMA_SECRET", None)
    os.environ.setdefault("KAZMA_DB_BACKEND", "sqlite")

    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        os.environ["KAZMA_DATA_DIR"] = tmp_dir
        cs = ConfigStore(db_path=os.path.join(tmp_dir, "e2e_hitl_settings.db"))
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
            else:
                os.environ.pop("KAZMA_SECRET", None)
            if orig_data is not None:
                os.environ["KAZMA_DATA_DIR"] = orig_data
            else:
                os.environ.pop("KAZMA_DATA_DIR", None)


def _seed_session(*, pending: bool) -> tuple[str, str]:
    """Persist one user + assistant turn. Returns (session_id, interrupt_id)."""
    from kazma_core.safety.hitl_gates import GateRow, register_gate
    from kazma_ui.reply_sink import open_reply_turn
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.turn_runtime import persist_reply

    sid = "e2e-hitl-" + uuid.uuid4().hex[:12]
    iid = "g-" + uuid.uuid4().hex[:10]
    sm = get_session_manager()
    sess = sm.get_or_create(sid)
    sess.thread_id = sid
    sess.title = "E2E HITL view"
    sess.messages = [{"role": "user", "content": "write README.md"}]
    sm.put(sess)
    turn = open_reply_turn(sid)
    payload = {
        "thread_id": sid,
        "tool": "file_write",
        "interrupt_id": iid,
        "args": {"path": "README.md", "content": "hi"},
        "message": "Write README.md",
        "yolo_allowed": True,
    }
    if pending:
        register_gate(GateRow(
            gate_id=iid,
            thread_id=sid,
            session_id=sid,
            tool="file_write",
            payload_json=json.dumps(payload),
        ))
        persist_reply(
            sid,
            turn,
            "Need to write README.md.",
            thread_id=sid,
            interrupted=True,
            pending=True,
            parts=[
                {
                    "type": "hitl",
                    "state": "pending",
                    "tool": "file_write",
                    "interrupt_id": iid,
                    "payload": payload,
                },
                {"type": "text", "text": "Need to write README.md."},
            ],
        )
    else:
        persist_reply(
            sid,
            turn,
            "Wrote README.md.",
            thread_id=sid,
            interrupted=False,
            parts=[
                {
                    "type": "hitl",
                    "state": "approved",
                    "tool": "file_write",
                    "interrupt_id": iid,
                    "payload": payload,
                },
                {"type": "text", "text": "Wrote README.md."},
            ],
        )
    return sid, iid


def _open_session(page, base: str, sid: str) -> None:
    page.add_init_script(
        f"window.localStorage.setItem('kazma.chatSessionId', '{sid}');"
    )
    page.goto(f"{base}/chat", timeout=30000, wait_until="domcontentloaded")
    page.locator("#chat-input").wait_for(state="visible", timeout=15000)
    page.locator(".message-assistant").first.wait_for(timeout=15000)
    # loadSession paints, then _resyncDelivery fetches /status. Give the
    # second pass a moment — A1 will make this wait unnecessary.
    page.wait_for_timeout(1500)


def test_2_refresh_mid_pause_has_live_buttons(live_server: str) -> None:
    """Incident 2: hard reload while waiting → live buttons, no Alpine twin."""
    from playwright.sync_api import sync_playwright

    sid, _iid = _seed_session(pending=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            _open_session(page, live_server, sid)
            page.reload(wait_until="domcontentloaded")
            page.locator(".message-assistant").first.wait_for(timeout=15000)
            page.wait_for_timeout(1500)

            inline = page.locator(".message-assistant .hitl-approval-card")
            assert inline.count() >= 1, "no in-bubble HITL card after refresh"
            approve = page.locator(
                ".message-assistant .hitl-approval-card .hitl-approve"
            )
            assert approve.count() >= 1, (
                "refresh mid-pause left the card without live Approve "
                "(hydrate awaiting freeze)"
            )
            assert approve.first.is_enabled(), (
                "in-bubble Approve is present but disabled"
            )
            all_enabled = page.locator(".hitl-approval-card button:enabled")
            inline_enabled = page.locator(
                ".message-assistant .hitl-approval-card button:enabled"
            )
            assert all_enabled.count() == inline_enabled.count(), (
                "a second HITL card (Alpine strip) still has live buttons"
            )
        finally:
            browser.close()
        try:
            from kazma_ui.session_manager import get_session_manager

            get_session_manager().delete(sid)
        except Exception:
            pass


def test_3_refresh_after_settle_has_no_live_buttons(live_server: str) -> None:
    """Incident 3: hard reload after settle → Approved, no buttons, no timeout."""
    from playwright.sync_api import sync_playwright

    sid, _iid = _seed_session(pending=False)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            _open_session(page, live_server, sid)
            page.reload(wait_until="domcontentloaded")
            page.locator(".message-assistant").first.wait_for(timeout=15000)
            page.wait_for_timeout(1500)

            body = page.locator(".message-assistant").first.inner_text()
            assert "Wrote README.md" in body
            assert "timed out" not in body.lower()
            enabled = page.locator(
                ".message-assistant .hitl-approval-card .hitl-approve"
            )
            assert enabled.count() == 0, "settled gate still has a live Approve"
            card = page.locator(".message-assistant .hitl-approval-card")
            assert card.count() >= 1, "settled gate disappeared from the transcript"
            text = card.first.inner_text()
            assert "Waiting for approval" not in text, (
                "historical approved gate labelled as still waiting"
            )
            assert "Approved" in text or "approved" in text.lower(), (
                "settled gate is not labelled Approved"
            )
        finally:
            browser.close()
        try:
            from kazma_ui.session_manager import get_session_manager

            get_session_manager().delete(sid)
        except Exception:
            pass
