"""Confirmation dialog for destructive actions."""

from __future__ import annotations

import logging
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

logger = logging.getLogger(__name__)


class ConfirmDialog(ModalScreen[bool]):
    """Reusable confirmation dialog for destructive actions.
    
    Features:
        - Customizable message and button text
        - Warning styling for destructive actions
        - Keyboard shortcuts (Enter=confirm, Escape=cancel)
        - Returns boolean result on dismiss
    """

    DEFAULT_CSS = """
    ConfirmDialog {
        align: center middle;
    }
    ConfirmDialog > Container {
        width: 50%;
        max-width: 60;
        background: $panel;
        border: solid $error;
        padding: 1 2;
    }
    ConfirmDialog .dialog-title {
        text-align: center;
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    ConfirmDialog .dialog-message {
        text-align: center;
        padding: 1 2;
        margin-bottom: 1;
    }
    ConfirmDialog Horizontal {
        align: center middle;
        margin-top: 1;
    }
    ConfirmDialog Button {
        min-width: 15;
        margin: 0 1;
    }
    """

    def __init__(
        self,
        message: str,
        title: str = "Confirm Action",
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        is_destructive: bool = True,
    ) -> None:
        super().__init__()
        self.message = message
        self.title = title
        self.confirm_text = confirm_text
        self.cancel_text = cancel_text
        self.is_destructive = is_destructive

    def compose(self) -> ComposeResult:
        with Container():
            yield Static(f"⚠️  {self.title}", classes="dialog-title")
            yield Label(self.message, classes="dialog-message")
            with Horizontal():
                cancel_btn = Button(
                    self.cancel_text,
                    variant="default",
                    id="btn-cancel",
                )
                confirm_btn = Button(
                    self.confirm_text,
                    variant="error" if self.is_destructive else "primary",
                    id="btn-confirm",
                )
                yield cancel_btn
                yield confirm_btn

    def on_mount(self) -> None:
        """Focus the cancel button by default for safety."""
        self.query_one("#btn-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button press and dismiss with result."""
        confirmed = event.button.id == "btn-confirm"
        self._safe_dismiss(confirmed)

    def key_enter(self) -> None:
        """Enter activates the focused button (cancel by default for safety)."""
        focused = self.focused
        if focused is not None and isinstance(focused, Button):
            confirmed = focused.id == "btn-confirm"
            self._safe_dismiss(confirmed)
        else:
            # No button focused — default to cancel for safety
            self._safe_dismiss(False)

    def key_escape(self) -> None:
        """Escape cancels the action."""
        self._safe_dismiss(False)

    def _safe_dismiss(self, result: bool) -> None:
        """Dismiss without returning an awaitable from a message handler."""
        try:
            self.dismiss(result)
        except Exception:
            pass
