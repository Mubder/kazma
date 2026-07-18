"""Kazma TUI footer — context-sensitive keyboard shortcuts with enhanced styling."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Footer, Static

__all__ = ["CHAT_SHORTCUTS", "KazmaFooter"]

# Shortcut definitions for display
CHAT_SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+Q", "Quit"),
    ("Ctrl+P", "Commands"),
    ("Enter", "Send"),
    ("Ctrl+F", "Focus"),
]


class KazmaFooter(Footer):
    """Footer showing key bindings with enhanced visual design.

    Features:
        - Context-sensitive bindings based on active tab
        - Enhanced color scheme matching kazma.ai palette
        - Support for vim-style navigation indicators
    """

    DEFAULT_CSS = """
    KazmaFooter {
        dock: bottom;
        height: 1;
        background: $primary 18%;
        color: $primary;
    }

    FooterKey {
        background: $primary 10%;
        color: $text;
    }

    FooterKey > .footer-key--key {
        color: $primary;
        text-style: bold;
    }
    """

    BINDINGS = [
        # Navigation
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+p", "command_palette", "Commands"),
        ("j", "scroll_down", "Down"),
        ("k", "scroll_up", "Up"),
        # Context-aware (will be updated dynamically)
        ("ctrl+enter", "send_message", "Send"),
        ("ctrl+f", "focus_input", "Focus"),
    ]

    def _get_shortcuts_text(self) -> str:
        """Get formatted shortcuts text for display."""
        return " | ".join(f"{key} {desc}" for key, desc in CHAT_SHORTCUTS)

    def compose(self) -> ComposeResult:
        """Compose the footer with shortcuts display."""
        yield Static(self._get_shortcuts_text())
