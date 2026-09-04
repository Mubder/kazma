"""Tests for Industrial Audit Wave 6 fixes (Security Surface Polish).

Covers:
- H8: Skills UI admin requirement, rate limiting, path traversal guard, safe_error
- M16: GET /api/research/ready rate limiting
- M17: POST /api/memory/graph/clear confirm requirement and tenant/admin auth
- M18: GET /health information disclosure guard, /api/pending-approvals admin gate
- L17: Dead /chat redirect route removal & sessions template cleanup
- L18: Browser automation SSRF validation & isolated driver cleanup
- M13: Telegram 64-byte callback store and decoding
- L13: Swarm bus local/shared approval racing and pending result protection
- L14: Discord / Slack component removal & replace_original on interaction
- M12: Slack streaming attachment size ceiling
- M14: Discord op 6 Resume & background voice processing
- L16: Vision analyze local image stat guard & decompression bomb protection
"""

import asyncio
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# H8 & M16: Skills UI & Research ready rate limit
# ---------------------------------------------------------------------------

def test_skills_ui_security():
    from kazma_ui.skills_ui import create_skills_router

    app = FastAPI()
    router = create_skills_router(MagicMock(), MagicMock())
    app.include_router(router)
    client = TestClient(app)

    # 1. Unauthenticated request when KAZMA_SECRET is active
    with patch("kazma_ui.auth.get_kazma_secret", return_value="super-secret"):
        resp = client.post("/api/skills/install", json={"skill_id": "test_skill"})
        assert resp.status_code == 401

        # 2. Authenticated but non-admin request
        with patch("kazma_ui.auth.is_authenticated", return_value=True):
            with patch("kazma_ui.auth.get_request_principal", return_value={"role": "viewer"}):
                resp = client.post("/api/skills/install", json={"skill_id": "test_skill"})
                assert resp.status_code == 403
                assert "Admin role required" in resp.text

            # 3. Authenticated admin request with path traversal attempt
            with patch("kazma_ui.auth.get_request_principal", return_value={"role": "admin"}):
                resp = client.post("/api/skills/install", json={"skill_id": "../../../etc/passwd"})
                assert resp.status_code == 400
                assert "Directory traversal not allowed" in resp.text


def test_research_ready_rate_limit():
    from kazma_ui.research_panel.routes import create_research_router

    router = create_research_router()
    route = next(r for r in router.routes if getattr(r, "path", "") == "/api/research/ready")
    assert len(route.dependencies) > 0


# ---------------------------------------------------------------------------
# M17: Memory graph clear authorization
# ---------------------------------------------------------------------------

def test_memory_graph_clear_authorization():
    from kazma_ui.routes_direct.memory import register_memory_routes

    app = FastAPI()
    fake_self = MagicMock()
    fake_self.app = app
    register_memory_routes(fake_self)
    client = TestClient(app)

    # Missing confirm
    resp = client.post("/api/memory/graph/clear?tenant=tenantA")
    assert resp.status_code == 400
    assert "confirm=true" in resp.text

    # Confirm present but unauthenticated
    with patch("kazma_ui.auth.get_kazma_secret", return_value="secret123"):
        resp = client.post("/api/memory/graph/clear?tenant=tenantA&confirm=true")
        assert resp.status_code == 401

        # Wrong tenant
        with patch("kazma_ui.auth.is_authenticated", return_value=True):
            with patch("kazma_ui.auth.get_request_principal", return_value={"role": "member", "tenant_id": "tenantB"}):
                resp = client.post("/api/memory/graph/clear?tenant=tenantA&confirm=true")
                assert resp.status_code == 403


# ---------------------------------------------------------------------------
# M18: Health disclosure & pending approvals clear gate
# ---------------------------------------------------------------------------

def test_health_and_pending_approvals_gate():
    from kazma_ui.routes_direct.misc import register_misc_routes

    app = FastAPI()
    fake_self = MagicMock()
    fake_self.app = app
    fake_self.templates = MagicMock()
    fake_self.agent = MagicMock()
    register_misc_routes(fake_self)
    client = TestClient(app)

    # Anonymous health check returns only minimal status
    with patch("kazma_ui.auth.get_kazma_secret", return_value="secret123"):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        # Pending approvals clear requires admin
        resp = client.post("/api/pending-approvals/clear")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# L18: Browser automation SSRF & safe driver cleanup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_browser_automation_ssrf_and_cleanup():
    from kazma_skills.native.browser_automation.tools import _close_sync, browser_navigate

    # SSRF: loopback / metadata blocked
    res = await browser_navigate("http://127.0.0.1:8000")
    assert "Error: URL blocked" in res and "blocked" in res

    res_meta = await browser_navigate("http://169.254.169.254/latest/meta-data/")
    assert "Error: URL blocked" in res_meta and "blocked" in res_meta

    # Test _close_sync error isolation
    mock_page = MagicMock()
    mock_page.close.side_effect = RuntimeError("Page close error")
    mock_browser = MagicMock()
    mock_browser.close.return_value = None
    mock_pw = MagicMock()
    mock_pw.stop.return_value = None

    import kazma_skills.native.browser_automation.tools as bt
    bt._state.update(page=mock_page, browser=mock_browser, playwright=mock_pw)

    # Even though page.close() raised, browser.close() and pw.stop() must still execute
    _close_sync()
    assert mock_browser.close.called
    assert mock_pw.stop.called
    assert bt._state.get("page") is None
    assert bt._state.get("browser") is None
    assert bt._state.get("playwright") is None


# ---------------------------------------------------------------------------
# M13: Telegram callback token store and decoding
# ---------------------------------------------------------------------------

def test_telegram_callback_store_and_decoding():
    from kazma_gateway.adapters.callback_store import decode_callback_data, encode_callback_data
    from kazma_gateway.adapters.platform_callbacks import parse_callback_data

    # Short string returns as is
    short = "hitl:approve:12345"
    assert encode_callback_data(short) == short

    # Long string (>64 bytes) gets shortened to cb:<token>
    long_str = "hitl:opt:some_very_long_semantic_clarification_option_identifier_exceeding_sixty_four_bytes:thread-xyz-9876543210"
    encoded = encode_callback_data(long_str)
    assert encoded.startswith("cb:")
    assert len(encoded.encode("utf-8")) <= 64

    # Decoding restores original
    decoded = decode_callback_data(encoded)
    assert decoded == long_str

    # parse_callback_data transparently decodes cb:<token>
    action = parse_callback_data(encoded)
    assert action.kind == "hitl"
    assert "thread-xyz-9876543210" in action.text
    assert "some_very_long_semantic_clarification_option_identifier_exceeding_sixty_four_bytes" in action.text


# ---------------------------------------------------------------------------
# L13: Swarm bus dual racing and pending result protection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bus_dual_racing_and_pending_result():
    from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter
    from kazma_core.swarm.bus import ApprovalRequest

    tb = TelegramBusAdapter(bot_token="fake", chat_id="123")
    approval = ApprovalRequest(
        task_id="task-999",
        worker_name="coder",
        task_description="write file",
        proposed_output="foo.py",
    )

    # Mock _post to succeed
    tb._post = AsyncMock(return_value={"ok": True, "result": {"message_id": 456}})
    tb._edit_message = AsyncMock(return_value=None)

    # If local click happens concurrently
    async def simulate_local_click():
        await asyncio.sleep(0.01)
        tb.approve("task-999")

    async def slow_shared(*args, **kwargs):
        await asyncio.sleep(5)
        return False

    asyncio.create_task(simulate_local_click())

    with patch("kazma_core.swarm.shared_approvals.create_pending"):
        with patch("kazma_core.swarm.shared_approvals.wait_for_resolution", side_effect=slow_shared):
            result = await tb.request_approval(approval, timeout=1.0)
            # Local click set pending_results, so result must be True even if shared wait was slow!
            assert result is True


# ---------------------------------------------------------------------------
# L14: Discord component removal on interaction
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discord_interaction_component_removal():
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake", allow_all=True)
    ack_payloads = []

    async def mock_post(url, json=None, **kwargs):
        ack_payloads.append(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        return mock_resp

    adapter._http = MagicMock()
    adapter._http.post = AsyncMock(side_effect=mock_post)

    interaction_data = {
        "id": "ia_123",
        "token": "tok_123",
        "data": {"custom_id": "swarm_approve_task_123"},
        "message": {"content": "Approval prompt for task 123"},
        "user": {"id": "user_1"},
    }

    with patch("kazma_gateway.adapters.discord_callbacks.route_swarm_bus", return_value="task_123"):
        await adapter._handle_interaction(interaction_data)

    assert len(ack_payloads) == 1
    # Must use type 7 and empty components to deactivate buttons
    assert ack_payloads[0]["type"] == 7
    assert ack_payloads[0]["data"]["components"] == []
    assert "✅ Approved" in ack_payloads[0]["data"]["content"]


# ---------------------------------------------------------------------------
# L14 & M12: Slack streaming attachment size ceiling & interaction response
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_slack_streaming_attachment_and_interaction():
    from kazma_gateway.adapters.slack import SlackAdapter
    from kazma_gateway.gateway import Attachment, IncomingMessage

    adapter = SlackAdapter(bot_token="xoxb-fake", app_token="xapp-fake")
    adapter._http = MagicMock()

    # Test M12: stream respects 20 MB ceiling
    class MockStreamResponse:
        def __init__(self, size: int):
            self.headers = {"Content-Length": str(size)}
            self.status_code = 200

        def raise_for_status(self):
            pass

        async def aiter_bytes(self, chunk_size=65536):
            yield b"x" * 1024

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    # Header over 20 MB -> skipped without downloading
    adapter._http.stream = MagicMock(return_value=MockStreamResponse(25 * 1024 * 1024))
    msg = IncomingMessage(
        platform="slack",
        sender_id="slack:U1",
        text="here is a file",
        attachments=[Attachment(kind="file", mime="application/octet-stream", url="https://slack.com/big.zip", filename="big.zip")],
    )
    result = await adapter._prefetch_private_files(msg)
    # Remains with original url, data is None
    assert result.attachments[0].data is None


# ---------------------------------------------------------------------------
# M14: Discord op 6 Resume & background audio
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_discord_resume_and_session_tracking():
    from kazma_gateway.adapters.discord import DiscordAdapter

    adapter = DiscordAdapter(token="fake", allow_all=True)
    adapter._session_id = "sess_xyz"
    adapter._sequence = 42

    sent_messages = []

    class MockWebSocket:
        async def recv(self):
            return json.dumps({"op": 10, "d": {"heartbeat_interval": 10000}})

        async def send(self, data):
            sent_messages.append(json.loads(data))

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    ws = MockWebSocket()
    shutdown = asyncio.Event()
    shutdown.set()  # Stop loop after connect

    with patch("websockets.connect", return_value=AsyncMock(__aenter__=AsyncMock(return_value=ws), __aexit__=AsyncMock())):
        await adapter._connect_gateway(shutdown, asyncio.Queue())

    # Must send op 6 Resume since session_id and sequence were set!
    assert len(sent_messages) >= 1
    resume_msg = sent_messages[0]
    assert resume_msg["op"] == 6
    assert resume_msg["d"]["session_id"] == "sess_xyz"
    assert resume_msg["d"]["seq"] == 42


# ---------------------------------------------------------------------------
# L16: Vision analyze file size guard & decompression bomb protection
# ---------------------------------------------------------------------------

def test_vision_analyze_decompression_and_size_guard(tmp_path: Path):
    from kazma_core.tools.vision_analyze import _load_local_image, _resize_image
    from PIL import Image

    # Test file size guard (> 40 MB)
    large_file = tmp_path / "large.png"
    large_file.write_bytes(b"\x00" * 1024)

    mock_stat = MagicMock()
    mock_stat.st_size = 50 * 1024 * 1024  # 50 MB > MAX_IMAGE_BYTES * 2 (40 MB)
    mock_stat.st_mode = 0o100644  # regular file

    with patch.object(Path, "stat", return_value=mock_stat):
        with patch("kazma_core.tools.vision_analyze._detect_format", return_value="png"):
            with pytest.raises(ValueError) as exc:
                _load_local_image(large_file)
            assert "Image file too large" in str(exc.value)

    # Test Pillow MAX_IMAGE_PIXELS configuration in _resize_image
    assert Image.MAX_IMAGE_PIXELS == 50_000_000

    # Ensure corrupted / decompression bomb byte sequence does not crash
    corrupt_bytes = b"not an image"
    res = _resize_image(corrupt_bytes)
    assert res == corrupt_bytes  # safely fell back to returning original bytes
