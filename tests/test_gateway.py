"""Tests for the Kazma Gateway — schemas, BaseAdapter, GatewayManager, TelegramAdapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
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
        # Wait for shutdown
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
        # Task completed with exception
        assert adapter._task.exception() is not None
        await adapter.stop()


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

        # The adapter's listen() is waiting on shutdown_event
        assert adapter._running is True

        # Stop signals shutdown
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

        # Simulate an adapter putting a message on the bus
        test_msg = IncomingMessage(
            platform="dummy",
            sender_id="dummy:1",
            text="test message",
        )
        await manager.queue.put(test_msg)

        # Give the consumer time to process
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
        """send() fails gracefully for unknown platforms."""
        manager = GatewayManager()
        await manager.start()

        ok = await manager.send(OutboundMessage(target_id="slack:C123", text="hi"))
        assert ok is False

        await manager.stop()

    @pytest.mark.asyncio
    async def test_send_bad_format(self) -> None:
        """send() fails for non-prefixed target IDs."""
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
        """Calling start() twice doesn't double-start."""
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

        # Fill the queue
        await manager.queue.put(IncomingMessage(platform="t", sender_id="t:1", text="a"))
        await manager.queue.put(IncomingMessage(platform="t", sender_id="t:1", text="b"))
        assert manager.queue.full()

        # Third put should block (we'll use put_nowait to test)
        with pytest.raises(asyncio.QueueFull):
            manager.queue.put_nowait(IncomingMessage(platform="t", sender_id="t:1", text="c"))


# ══════════════════════════════════════════════════════════════════════════
# TelegramAdapter (unit tests — no real Telegram API)
# ══════════════════════════════════════════════════════════════════════════


class TestTelegramAdapter:
    """Unit tests for TelegramAdapter (mocked aiogram)."""

    def test_import(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        assert adapter.name == "telegram"

    def test_allowed_users(self) -> None:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token", allowed_users=[1, 2, 3])
        assert adapter._allowed_users == {1, 2, 3}

    @pytest.mark.asyncio
    async def test_send_without_bot(self) -> None:
        """send() fails gracefully when bot is not initialized."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        ok = await adapter.send(
            OutboundMessage(
                target_id="telegram:123",
                text="hi",
                context_metadata={"chat_id": 123},
            )
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_with_mock_bot(self) -> None:
        """send() delegates to bot.send_message with correct args."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        mock_bot = AsyncMock()
        adapter._bot = mock_bot

        ok = await adapter.send(
            OutboundMessage(
                target_id="telegram:12345",
                text="Hello!",
                context_metadata={"chat_id": 12345},
            )
        )
        assert ok is True
        mock_bot.send_message.assert_called_once_with(
            chat_id=12345,
            text="Hello!",
            parse_mode=adapter._parse_mode,
        )

    @pytest.mark.asyncio
    async def test_send_fallback_target_id(self) -> None:
        """send() falls back to parsing target_id when context_metadata has no chat_id."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        mock_bot = AsyncMock()
        adapter._bot = mock_bot

        ok = await adapter.send(OutboundMessage(target_id="telegram:99999", text="hi"))
        assert ok is True
        mock_bot.send_message.assert_called_once_with(
            chat_id=99999,
            text="hi",
            parse_mode=adapter._parse_mode,
        )

    @pytest.mark.asyncio
    async def test_send_no_chat_id(self) -> None:
        """send() fails when neither context_metadata nor target_id has chat_id."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="fake:token")
        adapter._bot = AsyncMock()

        ok = await adapter.send(OutboundMessage(target_id="telegram:", text="hi"))
        assert ok is False


# ══════════════════════════════════════════════════════════════════════════
# End-to-end flow (simulated)
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

        # Simulate incoming message
        incoming = IncomingMessage(
            platform="dummy",
            sender_id="dummy:42",
            text="Hello Brain",
            context_metadata={"chat_id": 42, "user_id": 7},
        )
        await manager.queue.put(incoming)

        # Let the consumer process
        await asyncio.sleep(0.5)
        await manager.stop()

        assert len(replies) == 1
        assert replies[0].text == "Echo: Hello Brain"
        assert replies[0].context_metadata["chat_id"] == 42
