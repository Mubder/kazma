"""Kazma TUI footer — context-sensitive keyboard shortcuts."""

from __future__ import annotations

from textual.widgets import Footer


class KazmaFooter(Footer):
    """Footer showing key bindings. Textual's built-in Footer handles this."""

    DEFAULT_CSS = """
    KazmaFooter {
        dock: bottom;
        height: 1;
        background: $primary 18%;
        color: $primary;
    }
    """
