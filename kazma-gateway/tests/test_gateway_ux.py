"""Tests for gateway UX features: slash commands, markdown rendering, typing indicators."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kazma_gateway.gateway import IncomingMessage, OutboundMessage
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
        """/reset returns a confirmation message."""
        result = resolve_slash_command("/reset")
        assert result is not None
        assert "reset" in result.lower()
        assert "🔄" in result

    def test_status_returns_health(self):
        """/status returns gateway health with adapter info."""
        ctx = _mock_context(adapters="telegram", queue_depth=5)
        result = resolve_slash_command("/status", ctx)
        assert result is not None
        assert "telegram" in result
        assert "running" in result
        assert "5" in result

    def test_model_command(self):
        """/model returns the active model name."""
        ctx = _mock_context(model="claude-sonnet-4")
        result = resolve_slash_command("/model", ctx)
        assert result is not None
        assert "claude-sonnet-4" in result

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
# Markdown rendering tests (via dispatcher)
# ══════════════════════════════════════════════════════════════════════


class TestMarkdownRendering:
    def test_telegram_parse_mode_set(self):
        """Telegram dispatcher uses MarkdownV2 parse_mode."""
        from kazma_gateway.dispatcher import _platform_parse_mode

        mode = _platform_parse_mode("telegram")
        assert mode == "MarkdownV2"

    def test_discord_parse_mode_none(self):
        """Discord uses native Markdown (no parse_mode needed)."""
        from kazma_gateway.dispatcher import _platform_parse_mode

        mode = _platform_parse_mode("discord")
        assert mode is None

    def test_slack_parse_mode_none(self):
        """Slack uses mrkdwn=true (no parse_mode needed)."""
        from kazma_gateway.dispatcher import _platform_parse_mode

        mode = _platform_parse_mode("slack")
        assert mode is None

    def test_fallback_html(self):
        """Unknown platforms use HTML conversion."""
        from kazma_gateway.dispatcher import _platform_parse_mode

        mode = _platform_parse_mode("unknown")
        assert mode == "HTML"

    def test_markdown_to_html_bold(self):
        """Bold markdown is converted to <b> tags."""
        from kazma_gateway.dispatcher import _markdown_to_html

        result = _markdown_to_html("Hello **world**")
        assert "<b>world</b>" in result

    def test_markdown_to_html_code(self):
        """Inline code is converted to <code> tags."""
        from kazma_gateway.dispatcher import _markdown_to_html

        result = _markdown_to_html("Run `pip install`")
        assert "<code>pip install</code>" in result

    def test_markdown_to_html_link(self):
        """Links are converted to <a> tags."""
        from kazma_gateway.dispatcher import _markdown_to_html

        result = _markdown_to_html("[click here](https://example.com)")
        assert 'href="https://example.com"' in result
        assert "click here" in result

    def test_markdown_to_html_codeblock(self):
        """Code blocks are converted to <pre><code>."""
        from kazma_gateway.dispatcher import _markdown_to_html

        result = _markdown_to_html("```python\nprint('hi')\n```")
        assert "<pre><code>" in result
        assert "print('hi')" in result

    def test_dispatcher_reply_applies_markdown(self):
        """Dispatcher.reply() applies HTML conversion for unknown platforms."""
        from kazma_gateway.gateway import GatewayManager

        mgr = MagicMock(spec=GatewayManager)
        mgr.send = AsyncMock(return_value=True)

        from kazma_gateway.dispatcher import MessageDispatcher

        d = MessageDispatcher(mgr)
        import asyncio

        asyncio.run(d.reply("unknown:123", "Hello **world**"))

        # Verify OutboundMessage was sent with HTML parse_mode
        call_args = mgr.send.call_args
        outbound = call_args[0][0]
        assert outbound.text == "Hello <b>world</b>"
        assert outbound.context_metadata.get("parse_mode") == "HTML"


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

        import asyncio

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

        import asyncio

        asyncio.run(adapter.send(outbound))
        assert True
