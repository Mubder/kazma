"""Message list widget — color-coded conversation display with accent bars.

Each message is rendered with a left-side accent bar color-coded by role:
  - user:      cyan ($accent-user)
  - assistant: purple ($accent-assistant)
  - tool:      amber ($accent-tool)
  - system:    dim ($text-muted)
  - error:     red ($error)
  - thinking:  animated cyan pulse while streaming
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import ClassVar

from rich.markdown import Markdown as RichMarkdown
from rich.panel import Panel
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import RichLog, Static

logger = logging.getLogger(__name__)

# Message role → CSS class mapping
ROLE_CLASS: dict[str, str] = {
    "user": "msg-user",
    "assistant": "msg-assistant",
    "tool": "msg-tool",
    "system": "msg-system",
    "error": "msg-error",
    "thinking": "msg-thinking",
}

# Accent bar color per role
ROLE_ACCENT: dict[str, str] = {
    "user": "$accent",
    "assistant": "$secondary",
    "tool": "$warning",
    "system": "$text-muted",
    "error": "$error",
    "thinking": "$accent",
}


class MessageEntry(Widget):
    """A single message entry with an accent bar, role label, and content.

    Rendered as a bordered panel with a left-side accent bar. Content
    is plain text displayed in a RichLog widget.
    """

    DEFAULT_CSS = """
    MessageEntry {
        width: 100%;
        height: auto;
        margin: 1 2;
        border-left: heavy $accent;
        padding: 1 2;
        background: $panel;
    }
    MessageEntry.msg-user { border-left: heavy $accent; }
    MessageEntry.msg-assistant { border-left: heavy $secondary; }
    MessageEntry.msg-tool { border-left: heavy $warning; }
    MessageEntry.msg-system { border-left: heavy $text-muted; }
    MessageEntry.msg-error { border-left: heavy $error; }
    MessageEntry.msg-thinking { border-left: heavy $accent; }

    MessageEntry > .msg-header {
        height: 1;
        color: $text-muted;
        text-style: bold;
    }
    MessageEntry > .msg-body {
        height: auto;
        color: $text;
        margin-top: 1;
    }
    """

    def __init__(self, role: str, content: str) -> None:
        super().__init__()
        self.role = role
        self.content = content
        self._css_class = ROLE_CLASS.get(role, "msg-system")

    def compose(self):
        role_label = self.role.upper()
        yield Static(f"[dim]{role_label}[/dim]", classes="msg-header")
        yield RichLog(highlight=True, markup=True, wrap=True, auto_scroll=True)


class MessageList(VerticalScroll):
    """Vertical-scrolling list of MessageEntry widgets.

    Features:
      - Auto-scroll to latest message ("follow mode")
      - Accent-colored left borders per role
      - RichLog for markdown-capable content display
    """

    DEFAULT_CSS = """
    MessageList {
        height: 1fr;
        width: 100%;
        background: $surface;
    }
    """

    def add_message(self, role: str, content: str) -> MessageEntry:
        """Append a new message and scroll to it."""
        entry = MessageEntry(role, content)
        self.mount(entry)
        # Populate the RichLog inside the entry
        try:
            log = entry.query_one(RichLog)
            log.write(content)
        except Exception:
            pass
        self.scroll_end(animate=False)
        return entry

    def clear(self) -> None:
        """Remove all messages."""
        for child in list(self.query(MessageEntry)):
            child.remove()
