"""Toast notification system for Kazma TUI.

Provides non-blocking, auto-dismissing notifications with different severity levels.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class Toast(ModalScreen[None]):
    """Non-blocking notification popup with auto-dismiss.

    Displays a styled message with an icon based on severity level.
    Auto-dismisses after a configurable duration.

    Levels:
        - info: ℹ️  General information
        - success: ✅  Operation completed successfully
        - warning: ⚠️  Potential issue
        - error: ❌  Error occurred
    """

    DEFAULT_CSS = """
    Toast {
        align: center top;
        offset: 0 2;
    }

    Toast > .toast-content {
        background: $panel;
        border: solid $primary;
        padding: 1 3;
        text-align: center;
        text-style: bold;
        min-width: 40;
    }

    Toast.-info > .toast-content {
        border: solid $primary;
    }

    Toast.-success > .toast-content {
        border: solid $success;
    }

    Toast.-warning > .toast-content {
        border: solid $warning;
    }

    Toast.-error > .toast-content {
        border: solid $error;
    }
    """

    def __init__(
        self,
        message: str,
        level: str = "info",
        duration: float = 3.0,
    ) -> None:
        """Initialize toast notification.

        Args:
            message: The notification message to display.
            level: Severity level (info, success, warning, error).
            duration: Auto-dismiss time in seconds (0 for manual dismiss).
        """
        super().__init__()
        self.message = message
        self.level = level
        self.duration = duration

    def compose(self) -> ComposeResult:
        icons = {
            "info": "ℹ️",
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
        }
        icon = icons.get(self.level, "📍")
        with Vertical(classes=f"toast-content toast-{self.level}"):
            yield Static(f"{icon}  {self.message}")

    def on_mount(self) -> None:
        if self.duration > 0:
            self.set_timer(self.duration, self.dismiss)

    def key_escape(self) -> None:
        """Allow manual dismiss with Escape key."""
        self.call_next(self.dismiss)
