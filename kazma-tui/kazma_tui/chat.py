"""Chat interface widget for the Kazma TUI.

Provides ``ChatPanel``, a Textual widget that combines a scrollable
message display with a text input field.  Supports built-in commands:

- ``/help``  — displays available commands and shortcuts
- ``/clear`` — clears the chat log
- ``/quit``  — exits the TUI cleanly

Commands are case-insensitive and are intercepted before being shown as
user messages.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Input, RichLog

logger = logging.getLogger(__name__)

_HELP_TEXT = """\
Available commands:
  /help   — Show this help message
  /clear  — Clear the chat log
  /quit   — Exit the application

Keyboard shortcuts:
  Enter   — Send message
  Ctrl+Q  — Quit
"""


class ChatPanel(Widget):
    """Chat interface with message display and input field.

    Layout::

        ┌────────────────────────────┐
        │  You: hello                │
        │  Assistant: Hi there!      │
        │                            │
        │                            │
        ├────────────────────────────┤
        │ Type a message...          │
        └────────────────────────────┘

    The input field is focused automatically on mount.
    """

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        border: solid $primary;
        layout: vertical;
    }

    ChatPanel > #chat-log {
        height: 1fr;
    }

    ChatPanel > #chat-input {
        dock: bottom;
        height: 3;
    }
    """

    # ── Textual lifecycle ───────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Compose the chat layout: message log + input field."""
        yield RichLog(id="chat-log", wrap=True, highlight=True)
        yield Input(placeholder="Type a message...", id="chat-input")

    def on_mount(self) -> None:
        """Focus the input field when the widget mounts."""
        self._focus_input()

    # ── Event handlers ──────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle text submitted from the input field.

        If the text starts with ``/`` it is treated as a command.
        Otherwise the text is displayed as a user message.
        """
        text = event.value.strip()
        # Clear the input regardless of content
        event.input.value = ""

        if not text:
            return

        if text.startswith("/"):
            self._handle_command(text)
        else:
            self.add_message("You", text)

        # Re-focus the input for the next message
        self._focus_input()

    # ── Command handling ────────────────────────────────────────────

    def _handle_command(self, raw: str) -> None:
        """Parse and execute a slash-command (case-insensitive).

        Supported commands: /help, /clear, /quit.
        Unknown commands display an error message.
        """
        cmd = raw.strip().lower()

        if cmd == "/help":
            self.add_message("System", _HELP_TEXT.strip())

        elif cmd == "/clear":
            self._clear_messages()

        elif cmd == "/quit":
            self.app.exit()

        else:
            self.add_message("System", f"Unknown command: {raw.strip()}")

    # ── Message helpers ─────────────────────────────────────────────

    def add_message(self, role: str, text: str) -> None:
        """Append a message to the chat log.

        Args:
            role: The label prefix (e.g. "You", "Assistant", "System").
            text: The message body.
        """
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(f"[bold]{role}:[/bold] {text}")
        except Exception:
            logger.debug("Chat log widget not yet mounted", exc_info=True)

    def _clear_messages(self) -> None:
        """Clear all messages from the chat log."""
        try:
            log = self.query_one("#chat-log", RichLog)
            log.clear()
        except Exception:
            logger.debug("Chat log widget not yet mounted", exc_info=True)

    def _focus_input(self) -> None:
        """Focus the input field."""
        try:
            input_widget = self.query_one("#chat-input", Input)
            input_widget.focus()
        except Exception:
            logger.debug("Chat input widget not yet mounted", exc_info=True)
