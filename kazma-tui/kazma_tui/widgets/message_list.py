"""Message list widget — color-coded conversation display with accent bars.

Each message is rendered with a left-side accent bar color-coded by role:
  - user:      cyan ($accent)
  - assistant: purple ($secondary)
  - tool:      amber ($warning)
  - system:    dim ($text-muted)
  - error:     red ($error)
  - thinking:  highlighted cyan with alternate background
"""

from __future__ import annotations

import logging

from textual.containers import VerticalScroll
from textual.widgets import RichLog

logger = logging.getLogger(__name__)

# Role → CSS class
ROLE_CLASS: dict[str, str] = {
    "user": "msg-user",
    "assistant": "msg-assistant",
    "tool": "msg-tool",
    "system": "msg-system",
    "error": "msg-error",
    "thinking": "msg-thinking",
}


class MessageEntry(RichLog):
    """A single color-coded message entry with accent bar."""

    DEFAULT_CSS = """
    MessageEntry {
        width: 100%;
        height: auto;
        min-height: 2;
        margin: 1 2;
        border-left: heavy $accent;
        padding: 0 2;
        background: transparent;
        color: $text;
    }
    MessageEntry.msg-user { border-left: heavy $accent; }
    MessageEntry.msg-assistant { border-left: heavy $secondary; }
    MessageEntry.msg-tool { border-left: heavy $warning; }
    MessageEntry.msg-system { border-left: heavy $text-muted; }
    MessageEntry.msg-error { border-left: heavy $error; }
    MessageEntry.msg-thinking { border-left: heavy $accent; background: $panel-alt; }
    """

    def __init__(self, role: str, content: str) -> None:
        super().__init__(highlight=True, markup=True, wrap=True, auto_scroll=True)
        self._content = content
        self.add_class(ROLE_CLASS.get(role, "msg-system"))
        self.write(f"[bold dim]{role.upper()}[/]")
        self.write(content)


class MessageList(VerticalScroll):
    """Scrollable list of MessageEntry widgets with auto-scroll."""

    DEFAULT_CSS = """
    MessageList {
        height: 1fr;
        width: 100%;
        background: $surface;
    }
    """

    def add_message(self, role: str, content: str) -> MessageEntry:
        """Append a message and scroll to it."""
        entry = MessageEntry(role, content)
        self.mount(entry)
        self.scroll_end(animate=False)
        return entry

    def clear(self) -> None:
        """Remove all messages."""
        for child in list(self.query(MessageEntry)):
            child.remove()
