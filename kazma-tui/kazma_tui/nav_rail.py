"""Left navigation rail for the TUI (hides top tabs chrome)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Static

__all__ = ["NavRail", "NavSelected"]

# (tab_id, short label, key hint)
NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "Dash", "1"),
    ("memory", "Memory", "2"),
    ("chat", "Chat", "3"),
    ("files", "Files", "4"),
    ("traces", "Traces", "5"),
    ("swarm", "Swarm", "6"),
    ("settings", "Settings", "7"),
)


class NavSelected(Message):
    """Emitted when a nav rail button is pressed."""

    def __init__(self, tab_id: str) -> None:
        super().__init__()
        self.tab_id = tab_id


class NavRail(Widget):
    """Vertical nav: keys 1–7 map to main tabs."""

    DEFAULT_CSS = """
    NavRail {
        dock: left;
        width: 14;
        background: $panel;
        border-right: tall $border;
        padding: 1 0;
        height: 1fr;
    }
    NavRail .nav-brand {
        height: 2;
        content-align: center middle;
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }
    NavRail Button {
        width: 100%;
        min-width: 1;
        height: 3;
        margin: 0 1 1 1;
        border: tall $border;
        background: $boost;
        color: $text-muted;
        text-align: left;
    }
    NavRail Button:hover {
        border: tall $primary;
        color: $text;
    }
    NavRail Button.-active {
        border: tall $primary;
        background: $primary 15%;
        color: $primary;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("KAZMA", classes="nav-brand")
        for tab_id, label, key in NAV_ITEMS:
            yield Button(
                f" {key}  {label}",
                id=f"nav-{tab_id}",
                classes="nav-btn",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid.startswith("nav-"):
            tab_id = bid[4:]
            self.set_active(tab_id)
            self.post_message(NavSelected(tab_id))

    def set_active(self, tab_id: str) -> None:
        for tid, _label, _key in NAV_ITEMS:
            try:
                btn = self.query_one(f"#nav-{tid}", Button)
                if tid == tab_id:
                    btn.add_class("-active")
                else:
                    btn.remove_class("-active")
            except Exception:
                pass
