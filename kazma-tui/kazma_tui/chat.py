"""Chat interface widget for the Kazma TUI.

Provides ``ChatPanel``, a Textual widget that combines a scrollable
message display with a text input field.  Supports built-in commands
and AI response generation via ModelRegistry.
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
    """Chat interface with message display, input field, and AI responses."""

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

    def compose(self) -> ComposeResult:
        """Compose the chat layout: message log + input field."""
        yield RichLog(id="chat-log", wrap=True, highlight=True)
        yield Input(placeholder="Type a message...", id="chat-input")

    def on_mount(self) -> None:
        """Focus the input field when the widget mounts."""
        self._focus_input()

    # ── Event handlers ──────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle text submitted from the input field."""
        text = event.value.strip()
        event.input.value = ""

        if not text:
            return

        if text.startswith("/"):
            self._handle_command(text)
        else:
            self.add_message("You", text)
            # Generate AI response
            self.app.call_later(self._generate_response, text)

        self._focus_input()

    # ── AI response generation ──────────────────────────────────────

    def _generate_response(self, prompt: str) -> None:
        """Generate an AI response via ModelRegistry and display it."""
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            agent = registry.get_agent()
            if agent is None:
                self.add_message("System", "[$error]No agent configured — check your model settings[/$error]")
                return
            response = agent.invoke(prompt)
            self.add_message("Assistant", response)
        except (RuntimeError, ImportError) as exc:
            self.add_message("System", f"[$secondary]Agent unavailable: {exc}[/$secondary]")
        except Exception as exc:
            self.add_message("System", f"[$error]Error: {exc}[/$error]")

    # ── Command handling ────────────────────────────────────────────

    def _handle_command(self, raw: str) -> None:
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

        User text is escaped to prevent Rich markup from being interpreted
        (e.g. '[bold]' in user input displays literally).
        """
        try:
            log = self.query_one("#chat-log", RichLog)
            escaped = _escape(text) if role == "You" else text
            log.write(f"[bold]{role}:[/bold] {escaped}")
        except Exception:
            logger.debug("Chat log widget not yet mounted", exc_info=True)

    def _clear_messages(self) -> None:
        try:
            log = self.query_one("#chat-log", RichLog)
            log.clear()
        except Exception:
            logger.debug("Chat log widget not yet mounted", exc_info=True)

    def _focus_input(self) -> None:
        try:
            input_widget = self.query_one("#chat-input", Input)
            input_widget.focus()
        except Exception:
            logger.debug("Chat input widget not yet mounted", exc_info=True)


def _escape(text: str) -> str:
    """Escape Rich markup characters so they display literally."""
    return text.replace("[", "\\[")
