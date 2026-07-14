"""Gateway Test Infrastructure - Platform Adapter Tests.

This module provides test fixtures and utilities for testing the Telegram,
Discord, and Slack platform adapters without sending real messages.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock


# ── Mock Message Builders ──────────────────────────────────────────────

class MockTelegramMessage:
    """Mock Telegram message for testing."""
    
    def __init__(
        self,
        message_id: int = 1,
        chat_id: int = 12345,
        text: str = "test message",
        user_id: int | None = None,
        from_user: dict[str, Any] | None = None,
    ) -> None:
        self.message_id = message_id
        self.chat_id = chat_id
        self.text = text
        self.from_user = from_user or {"id": user_id or 123, "is_bot": False}


class MockDiscordMessage:
    """Mock Discord message for testing."""
    
    def __init__(
        self,
        message_id: int = 1,
        channel_id: int = 12345,
        content: str = "test message",
        author_id: int | None = None,
    ) -> None:
        self.id = message_id
        self.channel_id = channel_id
        self.content = content
        self.author = MagicMock(id=author_id or 123)


class MockSlackMessage:
    """Mock Slack message for testing."""
    
    def __init__(
        self,
        ts: str = "1234567890.123456",
        channel: str = "C1234567890",
        text: str = "test message",
        user: str | None = None,
    ) -> None:
        self.ts = ts
        self.channel = channel
        self.text = text
        self.user = user or "U1234567890"


# ── Mock Bus Adapters ───────────────────────────────────────────────────

class MockTelegramBusAdapter:
    """Mock TelegramBusAdapter for testing without API calls."""
    
    def __init__(self, bot_token: str = "test_token", chat_id: int = 12345) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.sent_messages: list[dict[str, Any]] = []
        self.approval_requests: list[dict[str, Any]] = []
    
    async def send(self, message: str) -> bool:
        """Mock send - records message instead of sending."""
        self.sent_messages.append({"text": message, "chat_id": self.chat_id})
        return True
    
    async def send_report(self, report: Any) -> bool:
        """Mock send_report - records report."""
        self.sent_messages.append({"report": str(report), "chat_id": self.chat_id})
        return True
    
    async def request_approval(
        self,
        request: Any,
    ) -> bool:
        """Mock approval request - records request."""
        self.approval_requests.append({
            "text": request.text if hasattr(request, "text") else str(request),
            "chat_id": self.chat_id,
        })
        return True


class MockDiscordBusAdapter:
    """Mock DiscordBusAdapter for testing without WebSocket."""
    
    def __init__(self, token: str = "test_token", channel_id: int = 12345) -> None:
        self.token = token
        self.channel_id = channel_id
        self.sent_messages: list[str] = []
        self.approval_requests: list[dict[str, Any]] = []
        self._ready: bool = True
    
    async def send(self, message: str) -> bool:
        self.sent_messages.append(message)
        return True
    
    async def send_report(self, report: Any) -> bool:
        self.sent_messages.append(str(report))
        return True
    
    async def request_approval(self, request: Any) -> bool:
        self.approval_requests.append({"text": str(request)})
        return True
    
    async def start(self) -> None:
        pass
    
    async def stop(self) -> None:
        pass


class MockSlackBusAdapter:
    """Mock SlackBusAdapter for testing without Socket Mode."""
    
    def __init__(self, token: str = "test_token", channel: str = "C1234567890") -> None:
        self.token = token
        self.channel = channel
        self.sent_messages: list[str] = []
        self.approval_requests: list[dict[str, Any]] = []
    
    async def send(self, message: str) -> bool:
        self.sent_messages.append(message)
        return True
    
    async def send_report(self, report: Any) -> bool:
        self.sent_messages.append(str(report))
        return True
    
    async def request_approval(self, request: Any) -> bool:
        self.approval_requests.append({"text": str(request)})
        return True


# ── Test Helpers ─────────────────────────────────────────────────────────

async def run_async(coro: Any) -> Any:
    """Run an async coroutine synchronously for test convenience."""
    return await coro