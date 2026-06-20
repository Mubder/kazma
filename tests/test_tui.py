"""Tests for kazma_tui module."""

from __future__ import annotations

from kazma_tui.tui import _fix_arabic


class TestFixArabic:
    """Tests for the Arabic text fixing function."""

    def test_plain_english(self) -> None:
        """English text should pass through unchanged."""
        result = _fix_arabic("Hello, world!")
        assert result == "Hello, world!"

    def test_mixed_text(self) -> None:
        """Mixed Arabic/English should not crash."""
        result = _fix_arabic("Hello مرحبا World")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_string(self) -> None:
        """Empty string should return empty."""
        result = _fix_arabic("")
        assert result == ""

    def test_numbers(self) -> None:
        """Numbers should pass through."""
        result = _fix_arabic("Testing 123")
        assert "123" in result
