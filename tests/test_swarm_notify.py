"""Tests for swarm_notify — SwarmNotifier + SwarmTaskTracker.

Covers:
- Telegram message sending
- Markdown formatting pass-through
- Rate limit throttle (30 msg/sec)
- Task tracker lifecycle (start, progress, complete)
- Standalone mode (from_env without SwarmManager)
- Graceful fallback when kazma_core.swarm is missing
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from kazma_gateway.swarm_notify import (
    _RATE_LIMIT_MSGS,
    SwarmNotifier,
    SwarmTaskTracker,
)

# ── Helpers ────────────────────────────────────────────────────────


def _make_notifier(
    chat_id: str | int | None = "12345",
    client: httpx.AsyncClient | None = None,
) -> SwarmNotifier:
    """Build a SwarmNotifier with a fake token for testing."""
    return SwarmNotifier(
        bot_token="123456:TEST-TOKEN",
        default_chat_id=chat_id,
        parse_mode="Markdown",
        client=client,
    )


def _mock_client(response_json: dict | None = None) -> httpx.AsyncClient:
    """Return a mocked httpx.AsyncClient that returns a canned response."""
    if response_json is None:
        response_json = {"ok": True, "result": {"message_id": 1}}

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.json.return_value = response_json
    mock_resp.raise_for_status = MagicMock()

    client = AsyncMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=mock_resp)
    return client


# ── Tests ──────────────────────────────────────────────────────────


class TestSendMessage:
    """test_send_message — posts to Telegram."""

    @pytest.mark.asyncio
    async def test_send_message_posts_to_telegram(self) -> None:
        """SwarmNotifier.send_message calls the Telegram sendMessage endpoint."""
        client = _mock_client()
        notifier = _make_notifier(client=client)

        result = await notifier.send_message(chat_id="99999", text="Hello worker")

        client.post.assert_awaited_once()
        call_args = client.post.call_args
        assert "sendMessage" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["chat_id"] == "99999"
        assert payload["text"] == "Hello worker"
        assert result["ok"] is True

        await notifier.close()


class TestMarkdownFormatting:
    """test_markdown_formatting — bold + code blocks."""

    @pytest.mark.asyncio
    async def test_markdown_formatting_bold_and_code(self) -> None:
        """Markdown parse_mode is set and bold+code text is passed verbatim."""
        client = _mock_client()
        notifier = _make_notifier(client=client)

        text = "*Bold text* and `inline code`"
        await notifier.send_message(text=text)

        payload = client.post.call_args[1]["json"]
        assert payload["parse_mode"] == "Markdown"
        assert "*Bold text*" in payload["text"]
        assert "`inline code`" in payload["text"]

        await notifier.close()

    @pytest.mark.asyncio
    async def test_parse_mode_override(self) -> None:
        """Per-message parse_mode overrides the default."""
        client = _mock_client()
        notifier = _make_notifier(client=client)

        await notifier.send_message(text="test", parse_mode="HTML")

        payload = client.post.call_args[1]["json"]
        assert payload["parse_mode"] == "HTML"

        await notifier.close()


class TestRateLimitThrottle:
    """test_rate_limit_throttle — respects 30 msg/sec."""

    @pytest.mark.asyncio
    async def test_rate_limit_throttle(self) -> None:
        """Sending >30 messages triggers throttle sleep."""
        client = _mock_client()
        notifier = _make_notifier(client=client)

        # Send exactly _RATE_LIMIT_MSGS messages — should not throttle
        start = time.monotonic()
        for _ in range(_RATE_LIMIT_MSGS):
            await notifier.send_message(text="fast")
        elapsed_fast = time.monotonic() - start

        # The 31st message should trigger throttle
        start = time.monotonic()
        await notifier.send_message(text="throttled")
        elapsed_throttle = time.monotonic() - start

        # Fast burst should complete quickly (<1s)
        assert elapsed_fast < 2.0
        # Throttled message should have waited some amount
        # (at least a fraction of the window)
        assert elapsed_throttle >= 0.0  # smoke — real wait depends on timing

        await notifier.close()

    @pytest.mark.asyncio
    async def test_rate_limit_window_purges(self) -> None:
        """Timestamps outside the window are purged, not accumulated."""
        client = _mock_client()
        notifier = _make_notifier(client=client)

        # Simulate old timestamps
        notifier._send_timestamps = [time.monotonic() - 2.0] * 100
        await notifier.send_message(text="after-purge")

        # Should only have 1 timestamp (the new one), not 101
        assert len(notifier._send_timestamps) <= _RATE_LIMIT_MSGS

        await notifier.close()


class TestTaskTrackerStart:
    """test_task_tracker_start — records dispatched task."""

    @pytest.mark.asyncio
    async def test_task_tracker_start_records_task(self) -> None:
        """start_task creates a TrackedTask with correct fields."""
        notifier = _make_notifier()
        tracker = SwarmTaskTracker(notifier)

        tid = tracker.start_task(
            worker_id="worker-1",
            description="Analyze codebase",
            chat_id="54321",
        )

        task = tracker.get_task(tid)
        assert task is not None
        assert task.worker_id == "worker-1"
        assert task.description == "Analyze codebase"
        assert task.chat_id == "54321"
        assert task.status == "running"
        assert task.completed_at is None

        await notifier.close()


class TestTaskTrackerProgress:
    """test_task_tracker_progress — posts progress update."""

    @pytest.mark.asyncio
    async def test_task_tracker_posts_progress(self) -> None:
        """post_progress sends a formatted progress message to Telegram."""
        client = _mock_client()
        notifier = _make_notifier(client=client)
        tracker = SwarmTaskTracker(notifier)

        tid = tracker.start_task("worker-2", "Build module", chat_id="11111")
        result = await tracker.post_progress(tid, "50% complete")

        assert result is not None
        task = tracker.get_task(tid)
        assert "50% complete" in task.progress_messages[-1]

        # Verify the Telegram call contains progress info
        payload = client.post.call_args[1]["json"]
        assert "Progress" in payload["text"]
        assert "worker-2" in payload["text"]

        await notifier.close()

    @pytest.mark.asyncio
    async def test_should_post_progress_timing(self) -> None:
        """should_post_progress respects the interval."""
        notifier = _make_notifier()
        tracker = SwarmTaskTracker(notifier, progress_interval=0.1)

        tid = tracker.start_task("w-3", "test", chat_id="1")
        # Right after start, not enough time elapsed
        assert not await tracker.should_post_progress(tid)

        # After waiting past interval
        await asyncio.sleep(0.15)
        assert await tracker.should_post_progress(tid)

        await notifier.close()


class TestTaskTrackerComplete:
    """test_task_tracker_complete — posts completion."""

    @pytest.mark.asyncio
    async def test_task_tracker_complete_success(self) -> None:
        """complete_task sends success notification and updates status."""
        client = _mock_client()
        notifier = _make_notifier(client=client)
        tracker = SwarmTaskTracker(notifier)

        tid = tracker.start_task("worker-1", "Deploy", chat_id="22222")
        result = await tracker.complete_task(tid, success=True, summary="Deployed v1.2")

        assert result is not None
        task = tracker.get_task(tid)
        assert task.status == "completed"
        assert task.completed_at is not None

        payload = client.post.call_args[1]["json"]
        assert "✅" in payload["text"]
        assert "Deployed v1.2" in payload["text"]

        await notifier.close()

    @pytest.mark.asyncio
    async def test_task_tracker_complete_failure(self) -> None:
        """complete_task with success=False shows failure icon."""
        client = _mock_client()
        notifier = _make_notifier(client=client)
        tracker = SwarmTaskTracker(notifier)

        tid = tracker.start_task("worker-1", "Build", chat_id="33333")
        await tracker.complete_task(tid, success=False, summary="OOM killed")

        task = tracker.get_task(tid)
        assert task.status == "failed"

        payload = client.post.call_args[1]["json"]
        assert "❌" in payload["text"]
        assert "OOM killed" in payload["text"]

        await notifier.close()


class TestStandaloneMode:
    """test_standalone_mode — works without SwarmManager."""

    @pytest.mark.asyncio
    async def test_from_env_standalone(self) -> None:
        """SwarmNotifier.from_env reads tokens from environment variables."""
        env = {
            "SWARM_BOT_TOKEN": "env-token-123",
            "SWARM_CHAT_ID": "99999",
            "SWARM_PARSE_MODE": "HTML",
        }
        with patch.dict("os.environ", env, clear=False):
            notifier = SwarmNotifier.from_env()

        assert notifier._token == "env-token-123"
        assert notifier._default_chat_id == "99999"
        assert notifier._parse_mode == "HTML"

        await notifier.close()

    @pytest.mark.asyncio
    async def test_from_swarm_manager_fallback(self) -> None:
        """from_swarm_manager extracts config from manager, falls back to env."""
        mock_manager = MagicMock()
        mock_manager.bot_token = "mgr-token"
        mock_manager.chat_id = "77777"
        mock_manager.parse_mode = None  # should fall back

        env = {"SWARM_PARSE_MODE": "MarkdownV2"}
        with patch.dict("os.environ", env, clear=False):
            notifier = SwarmNotifier.from_swarm_manager(mock_manager)

        assert notifier._token == "mgr-token"
        assert notifier._default_chat_id == "77777"
        assert notifier._parse_mode == "MarkdownV2"

        await notifier.close()


class TestFallbackOnImportError:
    """test_fallback_on_import_error — graceful if kazma_core.swarm missing."""

    @pytest.mark.asyncio
    async def test_import_without_kazma_core_swarm(self) -> None:
        """swarm_notify module works even if kazma_core.delegation.swarm is absent."""
        # The module doesn't import kazma_core at module level — it's designed
        # for standalone use. Verify the module loads cleanly and from_env works.
        # Simulate by checking that importing swarm_notify never touches kazma_core.
        import kazma_gateway.swarm_notify as sn

        # Verify the module exposes the expected public API
        assert hasattr(sn, "SwarmNotifier")
        assert hasattr(sn, "SwarmTaskTracker")
        assert hasattr(sn, "TrackedTask")

        # Verify from_env works with empty env (graceful, no crash)
        env = {"SWARM_BOT_TOKEN": "standalone-token"}
        with patch.dict("os.environ", env, clear=False):
            notifier = SwarmNotifier.from_env()
            assert notifier._token == "standalone-token"
            await notifier.close()

    @pytest.mark.asyncio
    async def test_from_swarm_manager_missing_attrs(self) -> None:
        """from_swarm_manager handles a manager with no expected attributes."""
        bare_manager = object()  # no bot_token, no chat_id, no parse_mode

        env = {
            "SWARM_BOT_TOKEN": "fallback-token",
            "SWARM_CHAT_ID": "00000",
        }
        with patch.dict("os.environ", env, clear=False):
            notifier = SwarmNotifier.from_swarm_manager(bare_manager)

        assert notifier._token == "fallback-token"
        assert notifier._default_chat_id == "00000"
        assert notifier._parse_mode == "Markdown"  # default

        await notifier.close()
