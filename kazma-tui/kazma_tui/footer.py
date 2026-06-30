"""Custom footer widget displaying keyboard shortcuts.

This module provides ``FooterShortcuts``, a lightweight Textual widget that
shows key bindings in the footer area.  All shortcut labels are English-only.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)

# Ordered list of (key_label, description) pairs shown in the footer.
SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+Q", "Quit"),
    ("Ctrl+Y", "Copy"),
    ("Enter", "Send"),
]


class FooterShortcuts(Widget):
    """Footer bar showing keyboard shortcuts.

    Layout::

        Ctrl+Q Quit  |  Tab Switch  |  Enter Send
    """

    DEFAULT_CSS = """
    FooterShortcuts {
        dock: bottom;
        width: 100%;
        height: 1;
        background: $accent;
        color: $text;
        content-align: center middle;
        padding: 0 1;
    }
    """

    # ── Public helpers (used by tests) ──────────────────────────────

    def _get_shortcuts_text(self) -> str:
        """Return the full shortcuts display string.

        Returns a pipe-separated string of all shortcut labels and their
        descriptions, e.g. ``"Ctrl+Q Quit | Tab Switch | Enter Send"``.
        """
        parts = [f"{key} {desc}" for key, desc in SHORTCUTS]
        return "  |  ".join(parts)

    # ── Textual lifecycle ───────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Compose the footer with shortcut labels."""
        yield Static(self._get_shortcuts_text())
