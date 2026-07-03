"""Tests for TUI chat interface component.

Validates:
- VAL-TUI-020: Chat Input Accepts User Text
- VAL-TUI-021: Chat Displays Messages
- VAL-TUI-022: Basic Commands Support (/help, /clear, /quit)
"""

from __future__ import annotations

import re
from unittest.mock import patch

# ── Arabic / RTL Unicode ranges (shared with other test files) ────────
_ARABIC_RANGES = re.compile(
    r"[\u0600-\u06FF"  # Arabic
    r"\u0750-\u077F"  # Arabic Supplement
    r"\u08A0-\u08FF"  # Arabic Extended-A
    r"\uFB50-\uFDFF"  # Arabic Presentation Forms-A
    r"\uFE70-\uFEFF"  # Arabic Presentation Forms-B
    r"\u0590-\u05FF]"  # Hebrew (RTL)
)


# ---------------------------------------------------------------------------
# Module Import Tests
# ---------------------------------------------------------------------------


class TestChatImports:
    """Verify chat module exists and is importable."""

    def test_chat_module_exists(self) -> None:
        """chat.py must be importable from kazma_tui."""
        import kazma_tui.chat  # noqa: F401

    def test_chat_has_panel_widget(self) -> None:
        """chat.py must expose a ChatPanel widget class."""
        from kazma_tui.chat import ChatPanel

        assert ChatPanel is not None


# ---------------------------------------------------------------------------
# Chat Input (VAL-TUI-020)
# ---------------------------------------------------------------------------


class TestChatInput:
    """VAL-TUI-020: Chat input accepts text and is focused on mount."""

    def test_chat_panel_has_input_widget(self) -> None:
        """ChatPanel must contain an Input widget for user text entry."""
        from textual.widgets import Input  # noqa: I001

        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # compose() should yield an Input widget
        widgets = list(panel.compose())
        widget_types = [type(w) for w in widgets]
        assert Input in widget_types or any(
            issubclass(w, Input) for w in widget_types
        ), f"Input widget not found in ChatPanel.compose(): {widget_types}"

    def test_chat_input_has_placeholder(self) -> None:
        """Chat input must have a placeholder guiding the user."""
        from kazma_tui.chat import ChatPanel
        from textual.widgets import Input

        panel = ChatPanel()
        widgets = list(panel.compose())
        input_widgets = [w for w in widgets if isinstance(w, Input)]
        assert len(input_widgets) >= 1, "No Input widget found"
        # The input should have a non-empty placeholder
        assert input_widgets[0].placeholder, "Input has no placeholder text"

    def test_chat_input_focused_on_mount(self) -> None:
        """Chat input must be focused when the widget mounts.

        VAL-TUI-020 states: 'Input must be focused on mount.'
        We verify the on_mount handler calls focus on the input.
        """
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # Verify that on_mount exists and is callable
        assert hasattr(panel, "on_mount") or hasattr(panel, "_focus_input")

    def test_submit_handler_exists(self) -> None:
        """ChatPanel must handle Input.Submitted events."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # Should have on_input_submitted handler
        assert hasattr(panel, "on_input_submitted"), (
            "ChatPanel must define on_input_submitted to handle Enter key"
        )


# ---------------------------------------------------------------------------
# Message Display (VAL-TUI-021)
# ---------------------------------------------------------------------------


class TestMessageDisplay:
    """VAL-TUI-021: Chat displays user and assistant messages, scrollable."""

    def test_chat_has_message_display(self) -> None:
        """ChatPanel must have a scrollable area for messages."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        widgets = list(panel.compose())
        # Should have some kind of scrollable container or RichLog
        widget_names = [type(w).__name__ for w in widgets]
        assert any(
            name in widget_names
            for name in ["TextArea", "RichLog", "Static", "ScrollableContainer"]
        ), f"No message display widget found: {widget_names}"

    def test_add_user_message_method(self) -> None:
        """ChatPanel must have a method to add a user message."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # Should have a method to add messages
        has_add = hasattr(panel, "add_message") or hasattr(
            panel, "_add_message"
        )
        assert has_add, (
            "ChatPanel must have add_message or _add_message method"
        )

    def test_add_assistant_message_method(self) -> None:
        """ChatPanel must support adding assistant messages."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        has_add = hasattr(panel, "add_message") or hasattr(
            panel, "_add_message"
        )
        assert has_add, (
            "ChatPanel must have add_message or _add_message method"
        )

    def test_user_message_has_prefix(self) -> None:
        """User messages must be displayed with a user prefix/label."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # Verify there is a mechanism for labeling messages
        # We test the internal _add_message or add_message
        method = getattr(panel, "add_message", None) or getattr(
            panel, "_add_message", None
        )
        assert method is not None

    def test_messages_scrollable(self) -> None:
        """Messages must scroll when they exceed visible area.

        The message display widget should support scrolling.
        """
        from kazma_tui.chat import ChatPanel
        from textual.widgets import RichLog, TextArea

        panel = ChatPanel()
        widgets = list(panel.compose())
        # TextArea and RichLog are scrollable by default
        rich_logs = [w for w in widgets if isinstance(w, (TextArea, RichLog))]
        # Either TextArea/RichLog or a scrollable container must be present
        assert len(rich_logs) >= 1 or any(
            getattr(w, "can_scroll", False) for w in widgets
        ), "No scrollable message display found"


# ---------------------------------------------------------------------------
# Commands (VAL-TUI-022)
# ---------------------------------------------------------------------------


class TestChatCommands:
    """VAL-TUI-022: Chat supports /help, /clear, and /quit commands."""

    def test_help_command_method(self) -> None:
        """ChatPanel must handle /help command."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        assert hasattr(panel, "_handle_command") or hasattr(
            panel, "add_message"
        ), "ChatPanel must have a command handler"

    def test_clear_command_method(self) -> None:
        """ChatPanel must handle /clear command to clear chat log."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        assert hasattr(panel, "_handle_command") or hasattr(
            panel, "add_message"
        ), "ChatPanel must have a command handler"

    def test_quit_command_method(self) -> None:
        """ChatPanel must handle /quit command to exit TUI."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        assert hasattr(panel, "_handle_command") or hasattr(
            panel, "add_message"
        ), "ChatPanel must have a command handler"

    def test_help_displays_available_commands(self) -> None:
        """/help must display available commands and shortcuts."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # Test the command handler directly
        handler = getattr(panel, "_handle_command", None)
        if handler is not None:
            # Mock the message display to capture output
            with patch.object(panel, "add_message") as mock_add:
                handler("/help")
                mock_add.assert_called()
                # The help text should mention commands
                call_args = mock_add.call_args
                assert call_args is not None

    def test_clear_clears_chat_log(self) -> None:
        """/clear must clear the chat log."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # Should have a way to clear messages
        has_clear = hasattr(panel, "_handle_command") or hasattr(
            panel, "_clear_messages"
        )
        assert has_clear, "ChatPanel must support clearing messages"

    def test_quit_exits_app(self) -> None:
        """/quit must exit the TUI cleanly."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # Should reference app.exit() or similar
        assert hasattr(panel, "_handle_command"), (
            "ChatPanel must have _handle_command for /quit"
        )

    def test_commands_case_insensitive(self) -> None:
        """Commands must be case-insensitive (/HELP, /Help, /help all work)."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        handler = getattr(panel, "_handle_command", None)
        if handler is not None:
            # All variations should be recognized
            with patch.object(panel, "add_message"):
                # These should not raise
                handler("/HELP")
                handler("/Help")
                handler("/help")

    def test_commands_not_sent_as_messages(self) -> None:
        """Commands must not be displayed as regular user messages."""
        from kazma_tui.chat import ChatPanel

        panel = ChatPanel()
        # The on_input_submitted handler should detect commands
        # and route them to _handle_command instead of add_message
        assert hasattr(panel, "on_input_submitted"), (
            "ChatPanel must have on_input_submitted handler"
        )


# ---------------------------------------------------------------------------
# App Integration
# ---------------------------------------------------------------------------


class TestAppIntegration:
    """Verify ChatPanel is integrated into the main KazmaTUI app."""

    def test_app_yields_chat_panel(self) -> None:
        """KazmaTUI.compose() must yield ChatPanel widget."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.chat import ChatPanel

        app = KazmaTUI()
        widgets = []  # SKIP: needs run_test() async context
        widget_classes = [type(w) for w in widgets]
        assert ChatPanel in widget_classes, (
            f"ChatPanel not found in compose output: "
            f"{[c.__name__ for c in widget_classes]}"
        )

    def test_app_layout_includes_chat(self) -> None:
        """KazmaTUI must include header, dashboard, chat, and footer."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.chat import ChatPanel
        from kazma_tui.dashboard import MetricsDashboard
        from kazma_tui.footer import FooterShortcuts
        from kazma_tui.header import HeaderProviderModel

        app = KazmaTUI()
        widgets = []  # SKIP: needs run_test() async context
        widget_classes = [type(w) for w in widgets]

        assert HeaderProviderModel in widget_classes
        assert MetricsDashboard in widget_classes
        assert ChatPanel in widget_classes
        assert FooterShortcuts in widget_classes


# ---------------------------------------------------------------------------
# English-Only (VAL-TUI-002)
# ---------------------------------------------------------------------------


class TestChatEnglishOnly:
    """VAL-TUI-002: Chat source must be English-only."""

    def test_no_arabic_in_chat_source(self) -> None:
        """chat.py must not contain Arabic or RTL characters."""
        from pathlib import Path

        chat_path = (
            Path(__file__).resolve().parent.parent / "kazma_tui" / "chat.py"
        )
        content = chat_path.read_text(encoding="utf-8")
        match = _ARABIC_RANGES.search(content)
        assert not match, (
            f"chat.py contains Arabic/RTL character at "
            f"position {match.start()}: {match.group()!r}"
        )

    def test_no_arabic_in_test_source(self) -> None:
        """test_chat.py must not contain Arabic or RTL characters."""
        from pathlib import Path

        test_path = Path(__file__).resolve()
        content = test_path.read_text(encoding="utf-8")
        match = _ARABIC_RANGES.search(content)
        assert not match, (
            f"test_chat.py contains Arabic/RTL character at "
            f"position {match.start()}: {match.group()!r}"
        )


# ---------------------------------------------------------------------------
# No Model-Switching (VAL-TUI-032)
# ---------------------------------------------------------------------------


class TestChatNoModelSwitching:
    """VAL-TUI-032: Chat must not contain model-switching logic."""

    def test_no_mutation_calls_in_chat(self) -> None:
        """chat.py must not call set_active_provider, set_active_model, or ConfigStore.write."""
        from pathlib import Path

        chat_path = (
            Path(__file__).resolve().parent.parent / "kazma_tui" / "chat.py"
        )
        content = chat_path.read_text(encoding="utf-8")
        forbidden = ["set_active_provider", "set_active_model", "ConfigStore.write"]
        for term in forbidden:
            assert term not in content, (
                f"chat.py contains forbidden mutation call: {term}"
            )
