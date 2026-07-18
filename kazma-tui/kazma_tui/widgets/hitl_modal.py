"""Interactive Human-in-the-Loop (HITL) approval gate dialog modal."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

logger = logging.getLogger(__name__)

__all__ = ["HitlApprovalScreen"]


class HitlApprovalScreen(ModalScreen[bool]):
    """High-impact color-coded security gate dialog for approving dangerous tool actions.

    Renders tool parameters and Risk Tiers with enter/escape hotkey support.
    """

    DEFAULT_CSS = """
    HitlApprovalScreen {
        align: center middle;
        background: rgba(10, 15, 20, 0.75);
    }

    HitlApprovalScreen > Container {
        width: 70%;
        max-width: 80;
        height: auto;
        max-height: 28;
        background: $boost;
        border: solid $warning;
        padding: 1 3;
        border-title-color: $error;
        border-title-align: center;
    }

    .hitl-title {
        text-align: center;
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }

    .hitl-subtitle {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }

    .hitl-meta-grid {
        height: auto;
        background: $panel;
        border: solid $border;
        padding: 1 2;
        margin-bottom: 1;
    }

    .hitl-meta-row {
        height: auto;
        layout: horizontal;
    }

    .hitl-meta-label {
        width: 15;
        text-style: bold;
        color: $text-muted;
    }

    .hitl-meta-value {
        width: 1fr;
        color: $text;
    }

    .hitl-args-title {
        text-style: bold;
        color: $primary;
        margin-top: 1;
        margin-bottom: 0;
    }

    .hitl-args-scroll {
        height: 6;
        background: $surface;
        border: solid $border;
        padding: 0 1;
        margin-bottom: 1;
        scrollbar-size: 1 0;
    }

    .hitl-args-text {
        color: $text-muted;
    }

    .hitl-actions {
        align: center middle;
        height: auto;
        margin-top: 1;
    }

    .hitl-actions Button {
        min-width: 18;
        margin: 0 2;
    }
    """

    def __init__(
        self,
        thread_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        message: str = "",
    ) -> None:
        super().__init__()
        self.thread_id = thread_id
        self.tool_name = tool_name
        self.arguments = arguments
        self.message = message or "Dangerous operations require immediate administrative approval."

    def compose(self) -> ComposeResult:
        import json

        try:
            formatted_args = json.dumps(self.arguments, indent=2)
        except Exception:
            formatted_args = str(self.arguments)

        with Container():
            yield Static("Approval Required", classes="hitl-title")
            yield Static(self.message, classes="hitl-subtitle")

            with Vertical(classes="hitl-meta-grid"):
                with Horizontal(classes="hitl-meta-row"):
                    yield Static("Thread ID:", classes="hitl-meta-label")
                    yield Static(self.thread_id, classes="hitl-meta-value")
                with Horizontal(classes="hitl-meta-row"):
                    yield Static("Tool Name:", classes="hitl-meta-label")
                    yield Static(f"[bold $error]{self.tool_name}[/bold $error]", classes="hitl-meta-value")
                with Horizontal(classes="hitl-meta-row"):
                    yield Static("Risk Rating:", classes="hitl-meta-label")
                    yield Static("[bold $error]CRITICAL DANGER[/bold $error]", classes="hitl-meta-value")

            yield Static("Target Arguments:", classes="hitl-args-title")
            with Vertical(classes="hitl-args-scroll"):
                yield Static(formatted_args, classes="hitl-args-text")

            with Horizontal(classes="hitl-actions"):
                yield Button(
                    "❌ Reject (Esc)",
                    variant="error",
                    id="hitl-btn-deny",
                )
                yield Button(
                    "✅ Approve (Enter)",
                    variant="success",
                    id="hitl-btn-approve",
                )

    def on_mount(self) -> None:
        """Focus on the deny button initially for fail-safe security."""
        self.query_one("#hitl-btn-deny", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle manual button press and dismiss."""
        approved = event.button.id == "hitl-btn-approve"
        self._safe_dismiss(approved)

    def key_enter(self) -> None:
        """Enter activates the focused button."""
        focused = self.focused
        if focused is not None and isinstance(focused, Button):
            approved = focused.id == "hitl-btn-approve"
            self._safe_dismiss(approved)
        else:
            # Safe default
            self._safe_dismiss(False)

    def key_escape(self) -> None:
        """Escape cancels and denies the tool execution."""
        self._safe_dismiss(False)

    def _safe_dismiss(self, result: bool) -> None:
        try:
            self.dismiss(result)
        except Exception as exc:
            logger.debug("HitlApprovalScreen dismiss failed: %s", exc)
