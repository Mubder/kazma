"""Chat interface widget for the Kazma TUI.

Uses MessageList (RichLog-based, markdown-capable) for output
and Input for message entry. Color-coded accent bars per role.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Input

from kazma_tui.widgets.message_list import MessageList

logger = logging.getLogger(__name__)

_HELP_TEXT = """\
Available commands:
  /help   — Show this help message
  /clear  — Clear the chat log
  /quit   — Exit the application"""


class ChatPanel(Vertical):
    """Chat interface — MessageList output + Input field."""

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        border: solid $primary;
        background: $surface;
    }

    ChatPanel > MessageList {
        height: 1fr;
    }

    ChatPanel > Input {
        dock: bottom;
        height: 3;
        margin: 1 2;
        background: $panel-alt;
        border: solid $border;
        color: $text;
    }
    ChatPanel > Input:focus {
        border: solid $primary;
    }
    """

    def compose(self) -> ComposeResult:
        yield MessageList(id="message-list")
        yield Input(placeholder="Type a message...", id="chat-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter in the input field."""
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self.add_message("user", text)
            self.app.call_later(self._generate_response, text)

    def add_message(self, role: str, content: str) -> None:
        """Append a color-coded message to the list."""
        try:
            msg_list = self.query_one(MessageList)
            msg_list.add_message(role, content)
        except Exception:
            logger.debug("MessageList not mounted yet")

    def _handle_command(self, text: str) -> None:
        """Route a slash command."""
        cmd = text.lower().split()[0]
        if cmd == "/help":
            self.add_message("system", _HELP_TEXT)
        elif cmd == "/clear":
            self.query_one(MessageList).clear()
        elif cmd == "/quit":
            self.app.exit()
        elif cmd in ("/model", "/models"):
            try:
                from kazma_core.settings.model_registry import get_model_list_text
                self.add_message("system", get_model_list_text("tui"))
            except Exception as exc:
                self.add_message("error", f"Model registry unavailable: {exc}")
        else:
            self.add_message("system", f"Unknown command: {cmd}. Try /help.")

    async def _generate_response(self, prompt: str) -> None:
        """Generate an AI response via ModelRegistry."""
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            provider = registry.get_client()
            messages = [{"role": "user", "content": prompt}]
            self.add_message("thinking", "Thinking...")
            response = await provider.chat(messages)
            content = response.content if hasattr(response, "content") else str(response)
            # Remove thinking entry
            try:
                msg_list = self.query_one(MessageList)
                for entry in list(msg_list.query("MessageEntry.msg-thinking")):
                    entry.remove()
            except Exception:
                pass
            self.add_message("assistant", content)
        except Exception as exc:
            self.add_message("error", f"Error: {exc}")

    def action_copy_last(self) -> None:
        """Copy the last assistant message to clipboard."""
        try:
            import pyperclip
            msg_list = self.query_one(MessageList)
            entries = list(msg_list.query("MessageEntry.msg-assistant"))
            if entries:
                pyperclip.copy(entries[-1].content)
        except Exception:
            pass
