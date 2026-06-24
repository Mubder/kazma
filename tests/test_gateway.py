"""Tests for the Kazma Gateway — schemas, BaseAdapter, GatewayManager, TelegramAdapter.

Covers:
    - IncomingMessage / OutboundMessage dataclasses
    - BaseAdapter lifecycle + jitter_sleep
    - GatewayManager orchestration, bounded queue, graceful drain
    - TelegramAdapter polling, parsing, send with 429 retry
    - End-to-end flow: adapter → queue → handler → reply
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_gateway import agent_handler
from kazma_gateway.gateway import (
    BaseAdapter,
    GatewayManager,
    IncomingMessage,
    OutboundMessage,
)

# ══════════════════════════════════════════════════════════════════════════
# IncomingMessage
# ══════════════════════════════════════════════════════════════════════════


class TestIncomingMessage:
    """Tests for the IncomingMessage dataclass."""

    def test_basic_creation(self) -> None:
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:12345",
            text="Hello world",
        )
        assert msg.platform == "telegram"
        assert msg.sender_id == "telegram:12345"
        assert msg.text == "Hello world"
        assert msg.context_metadata == {}
        assert msg.timestamp > 0

    def test_context_metadata(self) -> None:
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:12345",
            text="hi",
            context_metadata={"chat_id": 12345, "user_id": 999, "username": "test"},
        )
        assert msg.context_metadata["chat_id"] == 12345
        assert msg.context_metadata["user_id"] == 999

    def test_reply_target(self) -> None:
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:12345",
            text="hi",
        )
        assert msg.reply_target() == "telegram:12345"

    def test_slots(self) -> None:
        """IncomingMessage uses __slots__."""
        msg = IncomingMessage(platform="t", sender_id="t:1", text="c")
        with pytest.raises(AttributeError):
            msg.nonexistent = "value"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# OutboundMessage
# ══════════════════════════════════════════════════════════════════════════


class TestOutboundMessage:
    """Tests for the OutboundMessage dataclass."""

    def test_basic_creation(self) -> None:
        msg = OutboundMessage(target_id="telegram:12345", text="reply")
        assert msg.target_id == "telegram:12345"
        assert msg.text == "reply"
        assert msg.context_metadata == {}

    def test_with_context(self) -> None:
        msg = OutboundMessage(
            target_id="telegram:12345",
            text="reply",
            context_metadata={"chat_id": 12345, "message_id": 42},
        )
        assert msg.context_metadata["chat_id"] == 12345


# ══════════════════════════════════════════════════════════════════════════
# BaseAdapter
# ══════════════════════════════════════════════════════════════════════════


class DummyAdapter(BaseAdapter):
    """Concrete adapter for testing."""

    name = "dummy"

    def __init__(self, *, fail_listen: bool = False) -> None:
        super().__init__()
        self._fail_listen = fail_listen
        self.sent: list[OutboundMessage] = []

    async def listen(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        if self._fail_listen:
            raise RuntimeError("listen failed on purpose")
        # Wait for shutdown (simulates a polling loop)
        await shutdown_event.wait()

    async def send(self, outbound: OutboundMessage) -> bool:
        self.sent.append(outbound)
        return True


class TestBaseAdapter:
    """Tests for BaseAdapter lifecycle."""

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        adapter = DummyAdapter()
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        shutdown = asyncio.Event()
        await adapter.start(queue, shutdown)
        assert adapter._running is True
        assert adapter._task is not None
        shutdown.set()
        await adapter.stop()
        assert adapter._running is False

    @pytest.mark.asyncio
    async def test_listen_failure(self) -> None:
        """Adapter with failing listen should complete the task (with error)."""
        adapter = DummyAdapter(fail_listen=True)
        queue: asyncio.Queue[IncomingMessage] = asyncio.Queue()
        shutdown = asyncio.Event()
        await adapter.start(queue, shutdown)
        await asyncio.sleep(0.1)
        assert adapter._task.done()
        assert adapter._task.exception() is not None
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_jitter_sleep_returns_false_on_timeout(self) -> None:
        """jitter_sleep returns False when no shutdown (normal expiry)."""
        shutdown = asyncio.Event()
        result = await BaseAdapter.jitter_sleep(shutdown)
        assert result is False

    @pytest.mark.asyncio
    async def test_jitter_sleep_returns_true_on_shutdown(self) -> None:
        """jitter_sleep returns True when shutdown signalled during sleep."""
        shutdown = asyncio.Event()

        async def signal_after_delay() -> None:
            await asyncio.sleep(0.1)
            shutdown.set()

        asyncio.create_task(signal_after_delay())
        result = await BaseAdapter.jitter_sleep(shutdown)
        assert result is True

    @pytest.mark.asyncio
    async def test_jitter_sleep_delay_range(self) -> None:
        """jitter_sleep waits between 1 and 3 seconds (roughly)."""
        shutdown = asyncio.Event()
        import time

        start = time.monotonic()
        await BaseAdapter.jitter_sleep(shutdown)
        elapsed = time.monotonic() - start
        # Should be between 0.9 and 3.5 (with some tolerance)
        assert 0.9 <= elapsed <= 3.5


# ══════════════════════════════════════════════════════════════════════════
# GatewayManager
# ══════════════════════════════════════════════════════════════════════════


class TestGatewayManager:
    """Tests for GatewayManager orchestration."""

    @pytest.mark.asyncio
    async def test_add_adapter(self) -> None:
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        assert len(manager.adapters) == 1
        assert manager.adapters[0].name == "dummy"

    @pytest.mark.asyncio
    async def test_bounded_queue(self) -> None:
        """Queue is bounded at maxsize=100 by default."""
        manager = GatewayManager()
        assert manager.queue.maxsize == 100

    @pytest.mark.asyncio
    async def test_custom_queue_size(self) -> None:
        manager = GatewayManager(max_queue_size=50)
        assert manager.queue.maxsize == 50

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self) -> None:
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        await manager.start()
        assert manager._started is True
        assert adapter._running is True
        assert not manager._shutdown.is_set()
        await manager.stop()
        assert manager._started is False
        assert manager._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_event_propagates(self) -> None:
        """Setting manager's shutdown event stops all adapters."""
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        await manager.start()
        assert adapter._running is True
        await manager.stop()
        assert adapter._running is False

    @pytest.mark.asyncio
    async def test_handler_receives_messages(self) -> None:
        """Messages enqueued by adapters reach the registered handler."""
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)

        received: list[IncomingMessage] = []

        async def handler(msg: IncomingMessage) -> None:
            received.append(msg)

        manager.on_message(handler)
        await manager.start()

        test_msg = IncomingMessage(
            platform="dummy",
            sender_id="dummy:1",
            text="test message",
        )
        await manager.queue.put(test_msg)

        await asyncio.sleep(0.3)
        await manager.stop()

        assert len(received) == 1
        assert received[0].text == "test message"

    @pytest.mark.asyncio
    async def test_send_routes_to_adapter(self) -> None:
        """send() with platform-prefixed ID routes to the correct adapter."""
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        await manager.start()

        outbound = OutboundMessage(
            target_id="dummy:12345",
            text="hello",
            context_metadata={"chat_id": 12345},
        )
        ok = await manager.send(outbound)
        assert ok is True
        assert len(adapter.sent) == 1
        assert adapter.sent[0].text == "hello"
        assert adapter.sent[0].context_metadata["chat_id"] == 12345

        await manager.stop()

    @pytest.mark.asyncio
    async def test_send_unknown_platform(self) -> None:
        manager = GatewayManager()
        await manager.start()
        ok = await manager.send(OutboundMessage(target_id="slack:C123", text="hi"))
        assert ok is False
        await manager.stop()

    @pytest.mark.asyncio
    async def test_send_bad_format(self) -> None:
        manager = GatewayManager()
        await manager.start()
        ok = await manager.send(OutboundMessage(target_id="no-prefix", text="hi"))
        assert ok is False
        await manager.stop()

    @pytest.mark.asyncio
    async def test_stats(self) -> None:
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        stats = manager.stats
        assert stats["started"] is False
        assert stats["shutdown_signalled"] is False
        assert stats["queue_maxsize"] == 100
        assert len(stats["adapters"]) == 1

    @pytest.mark.asyncio
    async def test_lifespan_context_manager(self) -> None:
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        async with manager.lifespan(None):
            assert manager._started is True
            assert adapter._running is True
        assert manager._started is False
        assert manager._shutdown.is_set()

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        await manager.start()
        await manager.start()  # should warn, not crash
        assert manager._started is True
        await manager.stop()

    @pytest.mark.asyncio
    async def test_queue_backpressure(self) -> None:
        """Bounded queue applies backpressure when full."""
        manager = GatewayManager(max_queue_size=2)
        await manager.queue.put(IncomingMessage(platform="t", sender_id="t:1", text="a"))
        await manager.queue.put(IncomingMessage(platform="t", sender_id="t:1", text="b"))
        assert manager.queue.full()
        with pytest.raises(asyncio.QueueFull):
            manager.queue.put_nowait(IncomingMessage(platform="t", sender_id="t:1", text="c"))

    @pytest.mark.asyncio
    async def test_graceful_drain(self) -> None:
        """stop() drains remaining messages from the queue."""
        manager = GatewayManager(max_queue_size=10)

        drained: list[IncomingMessage] = []
        original_drain = manager.queue.get_nowait

        # Put messages on the queue before stopping
        await manager.queue.put(IncomingMessage(platform="t", sender_id="t:1", text="msg1"))
        await manager.queue.put(IncomingMessage(platform="t", sender_id="t:1", text="msg2"))

        await manager.start()
        await manager.stop()

        # Queue should be empty after stop()
        assert manager.queue.empty()


# ══════════════════════════════════════════════════════════════════════════
# TelegramAdapter (unit tests — no real Telegram API)
# ══════════════════════════════════════════════════════════════════════════


class TestTelegramAdapter:
    """Unit tests for TelegramAdapter (mocked HTTP)."""

    def test_import(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        assert adapter.name == "telegram"

    def test_allowed_users(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token", allowed_users=[1, 2, 3])
        assert adapter._allowed_users == {1, 2, 3}

    def test_parse_text_message(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        update = {
            "update_id": 42,
            "message": {
                "message_id": 1,
                "chat": {"id": 12345, "type": "private"},
                "from": {"id": 999, "username": "testuser", "first_name": "Test"},
                "text": "Hello Kazma",
            },
        }
        msg = adapter._parse_update(update)
        assert msg is not None
        assert msg.platform == "telegram"
        assert msg.sender_id == "telegram:12345"
        assert msg.text == "Hello Kazma"
        assert msg.context_metadata["chat_id"] == 12345
        assert msg.context_metadata["user_id"] == 999
        assert msg.context_metadata["username"] == "testuser"
        assert msg.context_metadata["update_id"] == 42

    def test_parse_channel_post(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        update = {
            "update_id": 1,
            "channel_post": {
                "message_id": 10,
                "chat": {"id": -100123, "type": "channel"},
                "text": "Channel update",
            },
        }
        msg = adapter._parse_update(update)
        assert msg is not None
        assert msg.sender_id == "telegram:-100123"
        assert msg.text == "Channel update"

    def test_parse_caption(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 1},
                "from": {"id": 1},
                "text": None,
                "caption": "Photo caption",
            },
        }
        msg = adapter._parse_update(update)
        assert msg is not None
        assert msg.text == "Photo caption"

    def test_parse_ignores_non_text(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 1},
                "from": {"id": 1},
                "sticker": {"file_id": "abc"},
            },
        }
        assert adapter._parse_update(update) is None

    def test_parse_ignores_callback_query(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        update = {"update_id": 1, "callback_query": {"id": "abc"}}
        assert adapter._parse_update(update) is None

    def test_parse_no_chat_id(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        update = {"update_id": 1, "message": {"text": "hello"}}
        assert adapter._parse_update(update) is None

    @pytest.mark.asyncio
    async def test_send_with_mock_http(self) -> None:
        """send() delegates to sendMessage with correct payload."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"ok": True}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        adapter._http = mock_http

        ok = await adapter.send(
            OutboundMessage(
                target_id="telegram:12345",
                text="Hello!",
                context_metadata={"chat_id": 12345},
            )
        )
        assert ok is True
        mock_http.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_fallback_target_id(self) -> None:
        """send() falls back to parsing target_id when no chat_id in metadata."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"ok": True}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        adapter._http = mock_http

        ok = await adapter.send(OutboundMessage(target_id="telegram:99999", text="hi"))
        assert ok is True

    @pytest.mark.asyncio
    async def test_send_no_chat_id(self) -> None:
        """send() fails when neither context_metadata nor target_id has chat_id."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        adapter._http = AsyncMock()

        ok = await adapter.send(OutboundMessage(target_id="telegram:", text="hi"))
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_without_http(self) -> None:
        """send() fails gracefully when HTTP client not initialized."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        # _http is None — send should create one or fail gracefully
        # We mock httpx.AsyncClient to avoid real HTTP
        with patch("kazma_gateway.adapters.telegram.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_client

            ok = await adapter.send(
                OutboundMessage(
                    target_id="telegram:123",
                    text="hi",
                    context_metadata={"chat_id": 123},
                )
            )
            assert ok is True

    @pytest.mark.asyncio
    async def test_send_429_retry(self) -> None:
        """send() retries on HTTP 429 with exponential backoff."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")

        # First call: 429, second call: 200
        resp_429 = MagicMock()
        resp_429.status_code = 429
        resp_429.raise_for_status = MagicMock()
        resp_429.json.return_value = {"ok": False, "parameters": {"retry_after": 1}}

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.raise_for_status = MagicMock()
        resp_200.json.return_value = {"ok": True}

        mock_http = AsyncMock()
        mock_http.post = AsyncMock(side_effect=[resp_429, resp_200])
        adapter._http = mock_http

        ok = await adapter.send(
            OutboundMessage(
                target_id="telegram:123",
                text="hi",
                context_metadata={"chat_id": 123},
            )
        )
        assert ok is True
        assert mock_http.post.call_count == 2


# ══════════════════════════════════════════════════════════════════════════
# End-to-end flow
# ══════════════════════════════════════════════════════════════════════════


class TestEndToEnd:
    """Simulate the full flow: adapter enqueues → handler processes → reply sent."""

    @pytest.mark.asyncio
    async def test_full_flow(self) -> None:
        manager = GatewayManager(max_queue_size=10)
        adapter = DummyAdapter()
        manager.add_adapter(adapter)

        replies: list[OutboundMessage] = []
        original_send = adapter.send

        async def capture_send(outbound: OutboundMessage) -> bool:
            replies.append(outbound)
            return await original_send(outbound)

        adapter.send = capture_send  # type: ignore[assignment]

        async def brain(msg: IncomingMessage) -> None:
            """Simulated Brain: echo back with prefix."""
            await manager.send(
                OutboundMessage(
                    target_id=msg.reply_target(),
                    text=f"Echo: {msg.text}",
                    context_metadata=msg.context_metadata,
                )
            )

        manager.on_message(brain)
        await manager.start()

        incoming = IncomingMessage(
            platform="dummy",
            sender_id="dummy:42",
            text="Hello Brain",
            context_metadata={"chat_id": 42, "user_id": 7},
        )
        await manager.queue.put(incoming)

        await asyncio.sleep(0.5)
        await manager.stop()

        assert len(replies) == 1
        assert replies[0].text == "Echo: Hello Brain"
        assert replies[0].context_metadata["chat_id"] == 42


# ══════════════════════════════════════════════════════════════════════════
# Platform isolation — Brain state must NEVER contain platform IDs
# ══════════════════════════════════════════════════════════════════════════


class TestPlatformIsolation:
    """Verify that agent_handler strips platform IDs from graph state."""

    @pytest.fixture
    async def store(self):
        from kazma_gateway.stores.sqlite import SQLiteSessionStore

        s = SQLiteSessionStore(":memory:")
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_brain_state_has_no_platform_ids(self, store) -> None:
        """agent_handler must not leak chat_id/user_id/message_id/update_id/chat_type into state."""
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:123456789",
            text="hello",
            context_metadata={
                "chat_id": 123456789,
                "user_id": 555,
                "message_id": 999,
                "update_id": 888,
                "username": "test_user",
                "chat_type": "private",
            },
        )
        state = await agent_handler._build_initial_state(msg, store)

        gateway_block = state.get("_gateway", {})
        assert "chat_id" not in gateway_block
        assert "user_id" not in gateway_block
        assert "message_id" not in gateway_block
        assert "update_id" not in gateway_block
        assert "chat_type" not in gateway_block
        assert "thread_id" in gateway_block
        assert gateway_block["display_name"] == "test_user"
        assert gateway_block["platform"] == "telegram"

    @pytest.mark.asyncio
    async def test_session_map_stores_full_context(self, store) -> None:
        """Store must contain the full platform context after state build."""
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:100",
            text="test",
            context_metadata={
                "chat_id": 100,
                "user_id": 200,
                "message_id": 300,
                "update_id": 400,
                "username": "alice",
                "chat_type": "group",
                "thread_id": "test-thread-123",
            },
        )
        state = await agent_handler._build_initial_state(msg, store)
        thread_id = state["_gateway"]["thread_id"]

        # Store must have the full context
        cached = await store.get(thread_id)
        assert cached["chat_id"] == 100
        assert cached["user_id"] == 200
        assert cached["message_id"] == 300
        assert cached["update_id"] == 400
        assert cached["username"] == "alice"
        assert cached["chat_type"] == "group"

        # Cleanup
        await store.delete(thread_id)

    @pytest.mark.asyncio
    async def test_session_map_popped_on_return_path(self, store) -> None:
        """After store.delete(), get() returns empty."""
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:50",
            text="test",
            context_metadata={
                "chat_id": 50,
                "thread_id": "pop-test-thread",
            },
        )
        state = await agent_handler._build_initial_state(msg, store)
        thread_id = state["_gateway"]["thread_id"]

        ctx = await store.get(thread_id)
        assert ctx["chat_id"] == 50

        # Delete (simulates return path)
        await store.delete(thread_id)

        # Second get returns empty
        ctx2 = await store.get(thread_id)
        assert ctx2 == {}

    @pytest.mark.asyncio
    async def test_gateway_block_only_agnostic_fields(self, store) -> None:
        """_gateway block must contain exactly: thread_id, display_name, platform."""
        msg = IncomingMessage(
            platform="telegram",
            sender_id="telegram:1",
            text="hi",
            context_metadata={
                "chat_id": 1,
                "user_id": 2,
                "message_id": 3,
                "update_id": 4,
                "username": "bob",
                "chat_type": "private",
                "extra_field": "should not leak",
            },
        )
        state = await agent_handler._build_initial_state(msg, store)
        gw = state["_gateway"]

        allowed_keys = {"thread_id", "display_name", "platform"}
        leaked = set(gw.keys()) - allowed_keys
        assert leaked == set(), f"Unexpected keys in _gateway: {leaked}"


# ══════════════════════════════════════════════════════════════════════════
# Gateway Status Endpoint
# ══════════════════════════════════════════════════════════════════════════


class TestGatewayStatus:
    """Tests for GET /api/gateway/status endpoint."""

    @pytest.mark.asyncio
    async def test_status_has_required_keys(self) -> None:
        """Status response must have adapters, persistence, threads."""
        manager = GatewayManager()
        status = await manager.get_status()

        assert "adapters" in status
        assert "persistence" in status
        assert "threads" in status
        assert isinstance(status["adapters"], list)
        assert isinstance(status["persistence"], dict)
        assert isinstance(status["threads"], list)

    @pytest.mark.asyncio
    async def test_status_empty_adapters(self) -> None:
        """No adapters registered → empty array, not error."""
        manager = GatewayManager()
        status = await manager.get_status()

        assert status["adapters"] == []
        assert status["threads"] == []

    @pytest.mark.asyncio
    async def test_status_adapter_fields(self) -> None:
        """Each adapter has platform, status, uptime_seconds."""
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        await manager.start()

        status = await manager.get_status()
        assert len(status["adapters"]) == 1
        a = status["adapters"][0]
        assert "platform" in a
        assert "status" in a
        assert "uptime_seconds" in a
        assert a["platform"] == "dummy"
        assert a["status"] == "connected"

        await manager.stop()

    @pytest.mark.asyncio
    async def test_status_with_persistence(self) -> None:
        """Status includes persistence info when store is registered."""
        from kazma_gateway.stores.sqlite import SQLiteSessionStore

        manager = GatewayManager()
        store = SQLiteSessionStore(":memory:")
        manager.set_persistence(
            session_store=store,
            session_store_path=":memory:",
        )

        # Add a session
        await store.put("thread-1", {"chat_id": 100, "username": "alice"})

        status = await manager.get_status()
        assert status["persistence"]["session_store"]["type"] == "sqlite"
        assert status["persistence"]["active_threads"] == 1
        assert len(status["threads"]) == 1
        assert status["threads"][0]["thread_id"] == "thread-1"

        await store.close()


# ══════════════════════════════════════════════════════════════════════════
# send_message Tool Registration
# ══════════════════════════════════════════════════════════════════════════


class TestSendMessageTool:
    """Verify send_message is registered as an agent tool."""

    def test_send_message_in_registry(self) -> None:
        """send_message must appear in the built-in tool registry."""
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        assert "send_message" in tool_names

    def test_send_message_schema(self) -> None:
        """send_message tool must have target_id and text parameters."""
        from kazma_core.agent.tool_registry import LocalToolRegistry

        registry = LocalToolRegistry(include_builtins=True)
        tools = registry.get_tool_definitions()
        sm = next(t for t in tools if t["function"]["name"] == "send_message")
        params = sm["function"]["parameters"]
        assert "target_id" in params["properties"]
        assert "text" in params["properties"]
        assert params["properties"]["target_id"]["type"] == "string"
        assert params["properties"]["text"]["type"] == "string"
