"""Kazma TUI footer — context-sensitive keyboard shortcuts with enhanced styling."""

from __future__ import annotations

from textual.widgets import Footer


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
