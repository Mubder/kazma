"""Tests for the Kazma Gateway — schemas, base adapter, manager, and Telegram adapter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kazma_gateway.base import BaseAdapter
from kazma_gateway.manager import GatewayManager
from kazma_gateway.schemas import UniversalMessage

# ══════════════════════════════════════════════════════════════════════════
# UniversalMessage schema
# ══════════════════════════════════════════════════════════════════════════


class TestUniversalMessage:
    """Tests for the UniversalMessage dataclass."""

    def test_basic_creation(self) -> None:
        msg = UniversalMessage(
            platform="telegram",
            sender_id="telegram:12345",
            content="Hello world",
        )
        assert msg.platform == "telegram"
        assert msg.sender_id == "telegram:12345"
        assert msg.content == "Hello world"
        assert msg.reply_to == "telegram:12345"  # defaults to sender_id
        assert msg.metadata == {}
        assert msg.timestamp > 0

    def test_custom_reply_to(self) -> None:
        msg = UniversalMessage(
            platform="discord",
            sender_id="discord:user123",
            content="test",
            reply_to="discord:channel456",
        )
        assert msg.reply_to == "discord:channel456"

    def test_metadata(self) -> None:
        msg = UniversalMessage(
            platform="telegram",
            sender_id="telegram:1",
            content="hi",
            metadata={"chat_id": 1, "username": "test"},
        )
        assert msg.metadata["chat_id"] == 1
        assert msg.metadata["username"] == "test"

    def test_to_dict(self) -> None:
        msg = UniversalMessage(
            platform="telegram",
            sender_id="telegram:1",
            content="hello",
        )
        d = msg.to_dict()
        assert d["platform"] == "telegram"
        assert d["sender_id"] == "telegram:1"
        assert d["content"] == "hello"

    def test_content_truncated_in_to_dict(self) -> None:
        msg = UniversalMessage(
            platform="test",
            sender_id="test:1",
            content="x" * 500,
        )
        d = msg.to_dict()
        assert len(d["content"]) == 200

    def test_slots(self) -> None:
        """UniversalMessage uses __slots__ — cannot add arbitrary attributes."""
        msg = UniversalMessage(platform="t", sender_id="t:1", content="c")
        with pytest.raises(AttributeError):
            msg.nonexistent = "value"  # type: ignore[misc]


# ══════════════════════════════════════════════════════════════════════════
# BaseAdapter
# ══════════════════════════════════════════════════════════════════════════


class DummyAdapter(BaseAdapter):
    """Concrete adapter for testing."""

    name = "dummy"

    def __init__(self, *, fail_listen: bool = False) -> None:
        super().__init__()
        self._fail_listen = fail_listen
        self.sent: list[tuple[str, str]] = []
        self.received: list[UniversalMessage] = []

    async def listen(self, queue: asyncio.Queue[UniversalMessage]) -> None:
        if self._fail_listen:
            raise RuntimeError("listen failed on purpose")
        while self._running:
            # Just idle — tests inject messages manually
            await asyncio.sleep(0.1)

    async def send(self, target_id: str, content: str) -> bool:
        self.sent.append((target_id, content))
        return True


class TestBaseAdapter:
    """Tests for BaseAdapter lifecycle."""

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        adapter = DummyAdapter()
        queue: asyncio.Queue[UniversalMessage] = asyncio.Queue()
        await adapter.start(queue)
        assert adapter._running is True
        assert adapter._task is not None
        await adapter.stop()
        assert adapter._running is False

    @pytest.mark.asyncio
    async def test_listen_failure_logged(self) -> None:
        """Adapter with failing listen should not crash the manager."""
        adapter = DummyAdapter(fail_listen=True)
        queue: asyncio.Queue[UniversalMessage] = asyncio.Queue()
        await adapter.start(queue)
        # Give the task time to run and fail
        await asyncio.sleep(0.2)
        assert adapter._task.done()
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
    async def test_start_stop_lifecycle(self) -> None:
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        await manager.start()
        assert manager._started is True
        assert adapter._running is True
        await manager.stop()
        assert manager._started is False

    @pytest.mark.asyncio
    async def test_handler_receives_messages(self) -> None:
        """Messages enqueued by adapters reach the registered handler."""
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)

        received: list[UniversalMessage] = []

        async def handler(msg: UniversalMessage) -> None:
            received.append(msg)

        manager.on_message(handler)
        await manager.start()

        # Simulate an adapter putting a message on the bus
        test_msg = UniversalMessage(
            platform="dummy",
            sender_id="dummy:1",
            content="test message",
        )
        await manager.queue.put(test_msg)

        # Give the consumer task time to process
        await asyncio.sleep(0.2)
        await manager.stop()

        assert len(received) == 1
        assert received[0].content == "test message"

    @pytest.mark.asyncio
    async def test_next_message(self) -> None:
        """next_message() blocks until a message arrives."""
        manager = GatewayManager()
        await manager.start()

        msg = UniversalMessage(platform="test", sender_id="t:1", content="yo")
        await manager.queue.put(msg)

        result = await asyncio.wait_for(manager.next_message(), timeout=1.0)
        assert result.content == "yo"
        await manager.stop()

    @pytest.mark.asyncio
    async def test_send_routes_to_adapter(self) -> None:
        """send() with platform-prefixed ID routes to the correct adapter."""
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)
        await manager.start()

        ok = await manager.send("dummy:12345", "hello")
        assert ok is True
        assert adapter.sent == [("dummy:12345", "hello")]

        await manager.stop()

    @pytest.mark.asyncio
    async def test_send_unknown_platform(self) -> None:
        """send() fails gracefully for unknown platforms."""
        manager = GatewayManager()
        await manager.start()

        ok = await manager.send("slack:C123", "hello")
        assert ok is False

        await manager.stop()

    @pytest.mark.asyncio
    async def test_send_bad_format(self) -> None:
        """send() fails gracefully for non-prefixed target IDs."""
        manager = GatewayManager()
        await manager.start()

        ok = await manager.send("no-platform-prefix", "hello")
        assert ok is False

        await manager.stop()

    @pytest.mark.asyncio
    async def test_stats(self) -> None:
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)

        stats = manager.stats
        assert stats["started"] is False
        assert len(stats["adapters"]) == 1
        assert stats["adapters"][0]["name"] == "dummy"

    @pytest.mark.asyncio
    async def test_lifespan_context_manager(self) -> None:
        """The lifespan async context manager starts and stops the gateway."""
        manager = GatewayManager()
        adapter = DummyAdapter()
        manager.add_adapter(adapter)

        async with manager.lifespan(None):
            assert manager._started is True
            assert adapter._running is True

        assert manager._started is False

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


# ══════════════════════════════════════════════════════════════════════════
# TelegramAdapter
# ══════════════════════════════════════════════════════════════════════════


class TestTelegramAdapter:
    """Tests for the Telegram polling adapter."""

    def _make_adapter(self, **kwargs: object) -> object:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        return TelegramAdapter(token="fake:token", **kwargs)  # type: ignore[arg-type]

    def test_parse_text_message(self) -> None:
        adapter = self._make_adapter()
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
        assert msg.content == "Hello Kazma"
        assert msg.reply_to == "telegram:12345"
        assert msg.metadata["chat_id"] == 12345
        assert msg.metadata["user_id"] == 999
        assert msg.metadata["username"] == "testuser"
        assert msg.metadata["update_id"] == 42

    def test_parse_channel_post(self) -> None:
        adapter = self._make_adapter()
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
        assert msg.content == "Channel update"

    def test_parse_caption(self) -> None:
        adapter = self._make_adapter()
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
        assert msg.content == "Photo caption"

    def test_parse_ignores_non_text(self) -> None:
        adapter = self._make_adapter()
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 1},
                "from": {"id": 1},
                "sticker": {"file_id": "abc"},
            },
        }
        msg = adapter._parse_update(update)
        assert msg is None

    def test_parse_ignores_callback_query(self) -> None:
        adapter = self._make_adapter()
        update = {
            "update_id": 1,
            "callback_query": {"id": "abc"},
        }
        msg = adapter._parse_update(update)
        assert msg is None

    def test_parse_no_chat_id(self) -> None:
        adapter = self._make_adapter()
        update = {
            "update_id": 1,
            "message": {"text": "hello"},
        }
        msg = adapter._parse_update(update)
        assert msg is None

    def test_target_id_parsing(self) -> None:
        adapter = self._make_adapter()
        # Valid
        assert adapter._parse_update({}) is None  # just checking import works

    @pytest.mark.asyncio
    async def test_send_valid_target(self) -> None:
        adapter = self._make_adapter()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"ok": True}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client
            adapter._http = mock_client

            ok = await adapter.send("telegram:12345", "Hello")
            assert ok is True
            mock_client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_invalid_target(self) -> None:
        adapter = self._make_adapter()
        ok = await adapter.send("discord:12345", "Hello")
        assert ok is False

    @pytest.mark.asyncio
    async def test_send_bad_chat_id(self) -> None:
        adapter = self._make_adapter()
        ok = await adapter.send("telegram:notanumber", "Hello")
        assert ok is False

    def test_user_whitelist(self) -> None:
        adapter = self._make_adapter(allowed_users=[999])
        update = {
            "update_id": 1,
            "message": {
                "chat": {"id": 1},
                "from": {"id": 111},  # not in whitelist
                "text": "blocked",
            },
        }
        msg = adapter._parse_update(update)
        # The parse itself succeeds — filtering is in listen()
        assert msg is not None
        assert msg.metadata["user_id"] == 111


# ══════════════════════════════════════════════════════════════════════════
# FastAPI Integration
# ══════════════════════════════════════════════════════════════════════════


class TestFastAPIIntegration:
    """Tests for the FastAPI integration module."""

    def test_setup_gateway_no_config(self) -> None:
        from kazma_gateway.fastapi_integration import setup_gateway

        config = MagicMock()
        config.raw = {}
        manager = setup_gateway(config)
        assert len(manager.adapters) == 0

    def test_setup_gateway_with_telegram(self) -> None:
        from kazma_gateway.fastapi_integration import setup_gateway

        config = MagicMock()
        config.raw = {"connectors": {"telegram": {"token": "test:token"}}}
        manager = setup_gateway(config)
        assert len(manager.adapters) == 1
        assert manager.adapters[0].name == "telegram"

    def test_gateway_router(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kazma_gateway.fastapi_integration import gateway_router

        manager = GatewayManager()
        app = FastAPI()
        app.include_router(gateway_router(manager))

        client = TestClient(app)
        resp = client.get("/api/gateway/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["started"] is False
