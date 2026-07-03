"""Chat interface for the Kazma TUI.

Color-coded messages with accent bars per role. Input at bottom.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input

from kazma_tui.widgets.message_list import MessageList

logger = logging.getLogger(__name__)

_HELP_TEXT = """Commands: /help, /clear, /model, /quit"""


class ChatPanel(Vertical):
    """Chat: MessageList + Input."""

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
        background: $panel;
        border: solid $border;
        color: $text;
    }
    ChatPanel > Input:focus {
        border: solid $primary;
    }
    """

    def compose(self) -> ComposeResult:
        yield MessageList(id="message-list")
        yield Input(placeholder="Type a message...  /help for commands", id="chat-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self._add("user", text)
            self.app.call_later(self._generate_response, text)

    def _add(self, role: str, content: str) -> None:
        self.query_one(MessageList).add_message(role, content)

    def _handle_command(self, text: str) -> None:
        cmd = text.lower().split()[0]
        if cmd == "/help":
            self._add("system", _HELP_TEXT)
        elif cmd == "/clear":
            self.query_one(MessageList).clear()
        elif cmd == "/quit":
            self.app.exit()
        elif cmd in ("/model", "/models"):
            try:
                from kazma_core.settings.model_registry import get_model_list_text
                self._add("system", get_model_list_text("tui"))
            except Exception as e:
                self._add("error", f"Model registry: {e}")
        else:
            self._add("system", f"Unknown: {cmd}. Try /help.")

    async def _generate_response(self, prompt: str) -> None:
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            provider = registry.get_client()
            self._add("thinking", "Thinking...")
            response = await provider.chat([{"role": "user", "content": prompt}])
            content = response.content if hasattr(response, "content") else str(response)
            # Remove thinking, add response
            msg_list = self.query_one(MessageList)
            for entry in list(msg_list.query("MessageEntry")):
                if "···" in str(entry.render()):
                    entry.remove()
            self._add("assistant", content)
        except Exception as e:
            self._add("error", f"Error: {e}")

    def action_copy_last(self) -> None:
        """Copy last assistant response to clipboard."""
        try:
            import pyperclip
            entries = list(self.query_one(MessageList).query("MessageEntry"))
            for entry in reversed(entries):
                text = str(entry.render())
                if text.startswith("▌ KAZMA"):
                    pyperclip.copy(text.split("\n", 1)[-1])
                    return
        except Exception:
            pass
