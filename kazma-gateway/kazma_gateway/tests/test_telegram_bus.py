"""Tests for Telegram platform adapter."""

import pytest


class TestTelegramBusAdapter:
    """Tests for TelegramBusAdapter functionality."""

    def test_escape_md_special_chars(self):
        """MarkdownV2 special characters should be escaped."""
        from kazma_gateway.adapters.telegram_bus import _escape_md

        # Test special characters are escaped
        result = _escape_md("Hello *world*")
        assert result == "Hello \\*world\\*"

        result = _escape_md("Text with [brackets]")
        assert result == "Text with \\[brackets\\]"

    def test_escape_md_empty(self):
        """Empty string should return empty."""
        from kazma_gateway.adapters.telegram_bus import _escape_md

        assert _escape_md("") == ""

    def test_escape_md_no_special(self):
        """Plain text should pass through unchanged."""
        from kazma_gateway.adapters.telegram_bus import _escape_md

        assert _escape_md("Hello world") == "Hello world"