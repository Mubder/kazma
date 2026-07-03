"""Message list widget — color-coded conversation with accent bars.

Uses Static widgets (proven Textual rendering) with left-side accent
bars color-coded by role. Messages are markdown via rich Panel styling.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.widgets import Static


ROLE_ACCENT: dict[str, str] = {
    "user": "#22d3ee",
    "assistant": "#a855f7",
    "tool": "#fbbf24",
    "system": "#64748b",
    "error": "#ef4444",
    "thinking": "#22d3ee",
}

ROLE_LABEL: dict[str, str] = {
    "user": "YOU",
    "assistant": "KAZMA",
    "tool": "TOOL",
    "system": "SYS",
    "error": "ERR",
    "thinking": "···",
}


class MessageEntry(Static):
    """A single message with accent bar and role label."""

    def __init__(self, role: str, content: str) -> None:
        accent = ROLE_ACCENT.get(role, "#64748b")
        label = ROLE_LABEL.get(role, role.upper())
        header = f"[{accent}]▌[/] [bold dim]{label}[/]"
        body = f"\n{content}" if content.strip() else ""
        super().__init__(f"{header}{body}")


class MessageList(VerticalScroll):
    """Scrollable list of colored MessageEntry widgets."""

    DEFAULT_CSS = """
    MessageList {
        height: 1fr;
        width: 100%;
        background: $surface;
        padding: 1 2;
    }
    MessageEntry {
        width: 100%;
        height: auto;
        min-height: 1;
        margin-bottom: 1;
        padding: 1 1;
        background: $panel;
        border-left: heavy $accent;
    }
    """

    def add_message(self, role: str, content: str) -> MessageEntry:
        entry = MessageEntry(role, content)
        self.mount(entry)
        self.scroll_end(animate=False)
        return entry

    def clear(self) -> None:
        for child in list(self.query(MessageEntry)):
            child.remove()
