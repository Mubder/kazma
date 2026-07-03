"""Context-sensitive footer widget displaying keyboard shortcuts.

Shows different shortcuts based on the active tab (Chat vs Swarm).
All shortcut labels are English-only.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)

# Per-tab shortcut sets
CHAT_SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+Q", "Quit"),
    ("Ctrl+Y", "Copy last"),
    ("Ctrl+P", "Commands"),
    ("Enter", "Send"),
    ("Ctrl+T", "Swarm"),
]

SWARM_SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl+Q", "Quit"),
    ("Ctrl+P", "Commands"),
    ("Ctrl+R", "Refresh"),
    ("Ctrl+T", "Chat"),
]


class FooterShortcuts(Widget):
    """Footer bar showing keyboard shortcuts.

    Detects the active tab (Chat vs Swarm) and shows relevant bindings.
    Refreshes on focus changes.
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

    def _get_shortcuts_text(self) -> str:
        """Return the shortcuts string for the active tab."""
        # Detect which screen is active
        try:
            tabs = self.app.query_one("TabbedContent")
            active = tabs.active
            shortcuts = CHAT_SHORTCUTS if active in (None, "chat") else SWARM_SHORTCUTS
        except Exception:
            shortcuts = CHAT_SHORTCUTS  # fallback if TabbedContent not available

        parts = [f"{key} {desc}" for key, desc in shortcuts]
        return "  |  ".join(parts)

    def on_mount(self) -> None:
        """Refresh shortcuts periodically to pick up tab changes."""
        self.set_interval(0.5, self._refresh_display)

    def _refresh_display(self) -> None:
        """Update the displayed text."""
        try:
            self.query_one(Static).update(self._get_shortcuts_text())
        except Exception:
            pass

    # ── Public helpers (used by tests) ──────────────────────────────

    def _get_shortcuts_text_legacy(self) -> str:
        """Return the full shortcuts display string (legacy API for tests)."""
        return self._get_shortcuts_text()

    # ── Textual lifecycle ───────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Compose the footer with shortcut labels."""
        yield Static(self._get_shortcuts_text())
