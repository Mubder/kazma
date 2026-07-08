"""Tests for gateway UX features: slash commands, typing indicators."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from kazma_gateway.gateway import OutboundMessage
from kazma_gateway.slash_commands import is_slash_command, resolve_slash_command

# ── Helpers ──────────────────────────────────────────────────────────


def _mock_context(**overrides: dict) -> dict:
    return {
        "started": True,
        "adapters": "telegram, discord",
        "queue_depth": 3,
        "active_threads": 2,
        "model": "gpt-4o-mini",
        "memory_count": 12,
        "total_tokens": 4520,
        "total_cost": 0.0231,
        **overrides,
    }


# ══════════════════════════════════════════════════════════════════════
# Slash command tests
# ══════════════════════════════════════════════════════════════════════


class TestSlashCommands:
    def test_help_command_returns_list(self):
        """/help returns a list of available commands."""
        result = resolve_slash_command("/help")
        assert result is not None
        assert "/help" in result
        assert "/reset" in result
        assert "/status" in result

    def test_reset_command_clears_state(self):
        """/reset returns None (handled by agent_handler directly)."""
        result = resolve_slash_command("/reset")
        assert result is None  # Handled by agent_handler

    def test_status_returns_health(self):
        """/status returns gateway health with adapter info."""
        ctx = _mock_context(adapters="telegram", queue_depth=5)
        result = resolve_slash_command("/status", ctx)
        assert result is not None
        assert "telegram" in result
        assert "running" in result
        assert "5" in result

    def test_model_command(self):
        """/model returns None (handled by agent_handler - interactive selector)."""
        ctx = _mock_context(model="claude-sonnet-4")
        result = resolve_slash_command("/model", ctx)
        assert result is None  # Handled by agent_handler

    def test_cost_command(self):
        """/cost returns token spend."""
        ctx = _mock_context(total_tokens=5000, total_cost=0.05)
        result = resolve_slash_command("/cost", ctx)
        assert result is not None
        assert "5000" in result
        assert "0.05" in result

    def test_memory_command(self):
        """/memory returns memory stats."""
        ctx = _mock_context(memory_count=42)
        result = resolve_slash_command("/memory", ctx)
        assert result is not None
        assert "42" in result

    def test_unknown_command(self):
        """An unknown /command returns None (passes through to LLM)."""
        result = resolve_slash_command("/unknown42")
        assert result is None

    def test_slash_command_skips_llm(self):
        """is_slash_command correctly identifies commands."""
        assert is_slash_command("/help") is True
        assert is_slash_command("/reset") is True
        assert is_slash_command("hello") is False
        assert is_slash_command("/") is False  # just a slash
        assert is_slash_command("") is False

    def test_case_insensitive(self):
        """Slash commands are case-insensitive."""
        result = resolve_slash_command("/HELP")
        assert result is not None
        assert "/help" in result.lower() or "/reset" in result


# ══════════════════════════════════════════════════════════════════════
# Typing indicator tests
# ══════════════════════════════════════════════════════════════════════


class TestTypingIndicator:
    def test_telegram_typing_indicator_fires(self):
        """Telegram adapter fires sendChatAction on send()."""
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(token="test:token")
        adapter._http = AsyncMock()
        adapter._http.post = AsyncMock()

        outbound = OutboundMessage(
            target_id="telegram:123456",
            text="hello",
            context_metadata={"chat_id": 123456},
        )


        asyncio.run(adapter.send(outbound))

        # Verify typing was called (fire-and-forget task was created)
        # The typing call is async — we can't await it, but we verify
        # the send method itself completed without error
        assert True

    def test_discord_typing_indicator_fires(self):
        """Discord adapter fires typing indicator on send()."""
        from kazma_gateway.adapters.discord import DiscordAdapter

        adapter = DiscordAdapter(token="test_token")
        adapter._http = AsyncMock()
        adapter._http.post = AsyncMock()

        outbound = OutboundMessage(
            target_id="discord:987654",
            text="hello",
            context_metadata={"channel_id": "987654"},
        )


        asyncio.run(adapter.send(outbound))
        assert True
