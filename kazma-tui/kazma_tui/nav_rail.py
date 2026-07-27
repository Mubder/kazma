"""Left navigation rail for the TUI (hides top tabs chrome)."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Static

__all__ = ["NavRail", "NavSelected"]

# (tab_id, full label, key hint) — labels must fit width 20 with key prefix
NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "Dashboard", "1"),
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
    """Vertical nav: keys 1–7 map to main tabs. Full labels always visible."""

    DEFAULT_CSS = """
    NavRail {
        dock: left;
        width: 20;
        background: $panel;
        border-right: solid $border;
        padding: 1 0 0 0;
        height: 1fr;
        layout: vertical;
    }
    NavRail .nav-brand {
        height: 3;
        width: 100%;
        content-align: center middle;
        color: $primary;
        text-style: bold;
        border-bottom: solid $border;
        margin-bottom: 1;
    }
    NavRail .nav-btn {
        width: 100%;
        min-width: 1;
        max-width: 100%;
        height: 3;
        margin: 0 0 0 0;
        padding: 0 1;
        border: none;
        border-left: solid transparent;
        background: transparent;
        color: $text-muted;
        text-align: left;
        content-align: left middle;
    }
    NavRail .nav-btn:hover {
        background: $boost;
        color: $text;
        border-left: solid $primary 40%;
    }
    NavRail .nav-btn.-active {
        background: $primary 12%;
        color: $primary;
        text-style: bold;
        border-left: solid $primary;
    }
    NavRail .nav-key {
        color: $text-disabled;
        text-style: none;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("KAZMA", classes="nav-brand")
        for tab_id, label, key in NAV_ITEMS:
            # Key + full label — width 20 fits " 7  Settings" cleanly
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
