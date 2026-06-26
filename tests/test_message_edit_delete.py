"""Tests for message edit/delete tracking and slash commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from kazma_gateway.dispatcher import (
    MessageDispatcher,
    MessageTracker,
    _friendly_error,
)
from kazma_gateway.slash_commands import (
    is_slash_command,
    resolve_slash_command,
)

# ── Mock GatewayManager ────────────────────────────────────────────────


@pytest.fixture
def mock_gateway() -> Mock:
    """Return a mock GatewayManager that returns True for send()."""
    gw = Mock()
    gw.send = AsyncMock(return_value=True)
    gw.adapters = []
    return gw


@pytest.fixture
def dispatcher(mock_gateway: Mock) -> MessageDispatcher:
    """Return a MessageDispatcher with a mock gateway."""
    return MessageDispatcher(mock_gateway)


# ══════════════════════════════════════════════════════════════════════════
# MessageTracker tests
# ══════════════════════════════════════════════════════════════════════════


class TestMessageTracker:
    """Tests for the lightweight message-pair store."""

    def test_record_and_pop_last(self) -> None:
        """Messages are stored and popped in LIFO order."""
        tracker = MessageTracker()
        tracker.record("chat:1", 100, "bot-aaa")
        tracker.record("chat:1", 101, "bot-bbb")

        assert tracker.history_length("chat:1") == 2

        pair = tracker.pop_last("chat:1")
        assert pair == (101, "bot-bbb")
        assert tracker.history_length("chat:1") == 1

        pair = tracker.pop_last("chat:1")
        assert pair == (100, "bot-aaa")
        assert tracker.history_length("chat:1") == 0

    def test_pop_last_empty_returns_none(self) -> None:
        """Pop on empty or missing chat returns None."""
        tracker = MessageTracker()
        assert tracker.pop_last("chat:9") is None

    def test_peek_last_does_not_remove(self) -> None:
        """Peek returns the last pair without removing it."""
        tracker = MessageTracker()
        tracker.record("chat:1", 200, "bot-zzz")

        pair = tracker.peek_last("chat:1")
        assert pair == (200, "bot-zzz")
        assert tracker.history_length("chat:1") == 1  # still there

    def test_separate_chats_independent(self) -> None:
        """Each chat_id has its own stack."""
        tracker = MessageTracker()
        tracker.record("chat:A", 1, "bot-a1")
        tracker.record("chat:B", 2, "bot-b1")

        assert tracker.history_length("chat:A") == 1
        assert tracker.history_length("chat:B") == 1

        assert tracker.pop_last("chat:A") == (1, "bot-a1")
        assert tracker.history_length("chat:A") == 0
        assert tracker.history_length("chat:B") == 1  # unaffected

    def test_history_length_nonexistent(self) -> None:
        """history_length returns 0 for unknown chat."""
        tracker = MessageTracker()
        assert tracker.history_length("nonexistent") == 0


# ══════════════════════════════════════════════════════════════════════════
# Slash command tests
# ══════════════════════════════════════════════════════════════════════════


class TestSlashCommands:
    """Tests for slash command detection and resolution."""

    def test_is_slash_command_true(self) -> None:
        """Messages starting with / are commands."""
        assert is_slash_command("/help")
        assert is_slash_command("/undo")
        assert is_slash_command("/edit")
        assert not is_slash_command("/")  # single slash is not a command

    def test_is_slash_command_false(self) -> None:
        """Non-slash messages are not commands."""
        assert not is_slash_command("hello")
        assert not is_slash_command(" /help")  # leading space
        assert not is_slash_command("")

    def test_help_includes_new_commands(self) -> None:
        """/help output mentions /undo and /edit."""
        result = resolve_slash_command("/help")
        assert result is not None
        assert "/undo" in result
        assert "/edit" in result

    def test_reset_returns_expected(self) -> None:
        """/reset returns the reset message."""
        result = resolve_slash_command("/reset")
        assert result is not None
        assert "reset" in result.lower()

    def test_unknown_command_returns_none(self) -> None:
        """Unknown slash command is passed through."""
        result = resolve_slash_command("/foobar")
        assert result is None

    def test_undo_without_dispatcher(self) -> None:
        """/undo without _dispatcher in context returns error."""
        result = resolve_slash_command("/undo", {"_chat_id": "chat:1"})
        assert result is not None
        assert "not available" in result

    def test_undo_with_empty_tracker(self, dispatcher: MessageDispatcher) -> None:
        """/undo when there's nothing to undo returns info."""
        ctx = {
            "_dispatcher": dispatcher,
            "_chat_id": "chat:empty",
        }
        result = resolve_slash_command("/undo", ctx)
        assert result is not None
        assert "Nothing to undo" in result

    def test_undo_pops_last(self, dispatcher: MessageDispatcher) -> None:
        """/undo pops the last exchange from the tracker."""
        # Simulate a tracked exchange
        dispatcher.tracker.record("chat:test", 42, "bot-track-1")

        ctx = {
            "_dispatcher": dispatcher,
            "_chat_id": "chat:test",
        }
        result = resolve_slash_command("/undo", ctx)
        assert result is not None
        assert "Last response removed" in result
        assert dispatcher.tracker.history_length("chat:test") == 0

    def test_edit_without_dispatcher(self) -> None:
        """/edit without _dispatcher returns error."""
        result = resolve_slash_command("/edit fixed text", {"_chat_id": "chat:1"})
        assert result is not None
        assert "not available" in result

    def test_edit_without_text(self, dispatcher: MessageDispatcher) -> None:
        """/edit without new text returns usage hint."""
        ctx = {
            "_dispatcher": dispatcher,
            "_chat_id": "chat:test",
        }
        result = resolve_slash_command("/edit", ctx)
        assert result is not None
        assert "Usage" in result

    def test_edit_with_only_spaces(self, dispatcher: MessageDispatcher) -> None:
        """/edit with whitespace-only text returns usage hint."""
        ctx = {
            "_dispatcher": dispatcher,
            "_chat_id": "chat:test",
        }
        result = resolve_slash_command("/edit   ", ctx)
        assert result is not None
        assert "Usage" in result

    def test_edit_empty_tracker(self, dispatcher: MessageDispatcher) -> None:
        """/edit when nothing to edit returns info."""
        ctx = {
            "_dispatcher": dispatcher,
            "_chat_id": "chat:empty",
        }
        result = resolve_slash_command("/edit new text", ctx)
        assert result is not None
        assert "Nothing to edit" in result

    def test_edit_pops_and_returns_new_text(self, dispatcher: MessageDispatcher) -> None:
        """/edit pops last exchange and shows corrected text."""
        dispatcher.tracker.record("chat:test", 10, "bot-old")

        ctx = {
            "_dispatcher": dispatcher,
            "_chat_id": "chat:test",
        }
        result = resolve_slash_command("/edit corrected message here", ctx)
        assert result is not None
        assert "corrected message here" in result
        assert dispatcher.tracker.history_length("chat:test") == 0

    def test_model_command(self) -> None:
        """/model returns active model."""
        result = resolve_slash_command("/model", {"model": "gpt-4"})
        assert result is not None
        assert "gpt-4" in result

    def test_status_command(self) -> None:
        """/status returns gateway overview."""
        result = resolve_slash_command(
            "/status",
            {
                "started": True,
                "adapters": "telegram",
                "queue_depth": 0,
                "active_threads": 3,
            },
        )
        assert result is not None
        assert "running" in result

    def test_cost_command(self) -> None:
        """/cost reports token spend."""
        result = resolve_slash_command(
            "/cost", {"total_tokens": 1500, "total_cost": 0.03}
        )
        assert result is not None
        assert "0.0300" in result

    def test_memory_command(self) -> None:
        """/memory reports stored facts."""
        result = resolve_slash_command("/memory", {"memory_count": 42})
        assert result is not None
        assert "42" in result


# ══════════════════════════════════════════════════════════════════════════
# Friendly error tests
# ══════════════════════════════════════════════════════════════════════════


class TestFriendlyError:
    """Tests for _friendly_error formatting."""

    def test_rate_limit_error(self) -> None:
        """HTTP 429 or rate-limit messages get a friendly format."""
        msg = _friendly_error(Exception("429 Too Many Requests"))
        assert "Rate limited" in msg

    def test_timeout_error(self) -> None:
        """TimeoutError gets a friendly format."""
        msg = _friendly_error(TimeoutError("connection timed out"))
        assert "taking longer than expected" in msg

    def test_connection_error(self) -> None:
        """ConnectionError gets a friendly format."""
        msg = _friendly_error(ConnectionError("Connection refused"))
        assert "Connection issue" in msg

    def test_tool_error(self) -> None:
        """Tool-related errors include context."""
        msg = _friendly_error(RuntimeError("tool 'search' failed: API key missing"))
        assert "Tool execution issue" in msg

    def test_generic_error(self) -> None:
        """Unknown errors get truncated class+message."""
        msg = _friendly_error(ValueError("some specific error"))
        assert "ValueError" in msg
        assert "some specific error" in msg


# ══════════════════════════════════════════════════════════════════════════
# Dispatcher integration tests
# ══════════════════════════════════════════════════════════════════════════


class TestDispatcherTracking:
    """Tests for dispatcher-level message tracking."""

    @pytest.mark.asyncio
    async def test_reply_tracks_exchange(self, dispatcher: MessageDispatcher) -> None:
        """reply() records the exchange after a successful send."""
        await dispatcher.reply(
            "telegram:12345",
            "Hello!",
            {"chat_id": 12345, "message_id": 100},
        )

        # Tracker should have one entry
        assert dispatcher.tracker.history_length("12345") == 1

        pair = dispatcher.tracker.peek_last("12345")
        assert pair is not None
        assert pair[0] == 100  # user_msg_id

    @pytest.mark.asyncio
    async def test_reply_multiple_exchanges(self, dispatcher: MessageDispatcher) -> None:
        """Multiple replies build up the stack."""
        await dispatcher.reply(
            "telegram:12345",
            "First",
            {"chat_id": 12345, "message_id": 1},
        )
        await dispatcher.reply(
            "telegram:12345",
            "Second",
            {"chat_id": 12345, "message_id": 2},
        )

        assert dispatcher.tracker.history_length("12345") == 2

        pair = dispatcher.undo_last("12345")
        assert pair is not None
        assert pair[0] == 2  # most recent user_msg_id
        assert dispatcher.tracker.history_length("12345") == 1

    @pytest.mark.asyncio
    async def test_reply_falls_back_to_sender_id(self, dispatcher: MessageDispatcher) -> None:
        """When context_metadata has no chat_id, sender_id is used."""
        await dispatcher.reply(
            "telegram:99999",
            "Hi",
            None,  # no context_metadata
        )
        assert dispatcher.tracker.history_length("telegram:99999") == 1

    @pytest.mark.asyncio
    async def test_undo_last_pops_correctly(self, dispatcher: MessageDispatcher) -> None:
        """undo_last removes and returns the last exchange."""
        await dispatcher.reply(
            "telegram:12345",
            "Msg1",
            {"chat_id": 12345, "message_id": 10},
        )
        await dispatcher.reply(
            "telegram:12345",
            "Msg2",
            {"chat_id": 12345, "message_id": 11},
        )

        pair = dispatcher.undo_last("12345")
        assert pair is not None
        assert pair[0] == 11  # most recent user_msg_id
        assert dispatcher.tracker.history_length("12345") == 1

    @pytest.mark.asyncio
    async def test_undo_last_empty_returns_none(self, dispatcher: MessageDispatcher) -> None:
        """undo_last on empty tracker returns None."""
        assert dispatcher.undo_last("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_last_tracking_id(self, dispatcher: MessageDispatcher) -> None:
        """get_last_tracking_id returns the bot tracking ID."""
        await dispatcher.reply(
            "telegram:12345",
            "Test",
            {"chat_id": 12345, "message_id": 50},
        )

        tid = dispatcher.get_last_tracking_id("12345")
        assert tid is not None
        assert tid.startswith("msg-")

    @pytest.mark.asyncio
    async def test_get_last_tracking_id_empty(self, dispatcher: MessageDispatcher) -> None:
        """get_last_tracking_id on empty tracker returns None."""
        assert dispatcher.get_last_tracking_id("empty") is None

    @pytest.mark.asyncio
    async def test_error_timeout_returns_friendly(self, dispatcher: MessageDispatcher) -> None:
        """Send timeout returns a friendly message."""
        dispatcher._gateway.send = AsyncMock(side_effect=TimeoutError("timed out"))

        result = await dispatcher.reply(
            "telegram:12345",
            "Test",
            {"chat_id": 12345, "message_id": 1},
        )
        assert "taking longer than expected" in result

    @pytest.mark.asyncio
    async def test_error_connection_returns_friendly(self, dispatcher: MessageDispatcher) -> None:
        """Connection error returns a friendly message."""
        dispatcher._gateway.send = AsyncMock(side_effect=ConnectionError("refused"))

        result = await dispatcher.reply(
            "telegram:12345",
            "Test",
            {"chat_id": 12345, "message_id": 1},
        )
        assert "Connection issue" in result

    @pytest.mark.asyncio
    async def test_resolve_injects_dispatcher(self, dispatcher: MessageDispatcher) -> None:
        """dispatcher.resolve() injects _dispatcher + _chat_id into context."""
        # Record with the chat_id key that _extract_chat_id produces (string)
        dispatcher.tracker.record("12345", 1, "bot-1")

        # resolve() should inject dispatcher into context
        # chat_id as integer — _extract_chat_id converts to str("12345")
        result = dispatcher.resolve("/undo", {"chat_id": 12345, "sender_id": "telegram:999"})
        assert result is not None
        assert "Last response removed" in result
        assert dispatcher.tracker.history_length("12345") == 0

    @pytest.mark.asyncio
    async def test_reply_to_message(self, dispatcher: MessageDispatcher) -> None:
        """reply_to_message convenience method works."""
        from kazma_gateway.gateway import IncomingMessage

        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:12345",
            text="/help",
            context_metadata={"chat_id": 12345, "message_id": 77},
        )

        result = await dispatcher.reply_to_message(msg, "Response")
        assert result is not None
        assert dispatcher.tracker.history_length("12345") == 1
