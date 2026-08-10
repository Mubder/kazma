"""Left navigation rail for the TUI (hides top tabs chrome).

Supports expanded (full labels) and collapsed (key-only) modes for narrow
terminals. Toggle with ``[`` or the rail footer button.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Static

__all__ = ["NavRail", "NavSelected", "NAV_ITEMS"]

# (tab_id, full label, key hint)
NAV_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("dashboard", "Dashboard", "1"),
    ("memory", "Memory", "2"),
    ("chat", "Chat", "3"),
    ("files", "Files", "4"),
    ("traces", "Traces", "5"),
    ("swarm", "Swarm", "6"),
    ("settings", "Settings", "7"),
    ("documents", "Documents", "8"),
)

_WIDTH_EXPANDED = 20
_WIDTH_COLLAPSED = 5


class NavSelected(Message):
    """Emitted when a nav rail button is pressed."""

    def __init__(self, tab_id: str) -> None:
        super().__init__()
        self.tab_id = tab_id


class NavRail(Widget):
    """Vertical nav: keys 1–7 map to main tabs. Collapsible for narrow terminals."""

    collapsed: reactive[bool] = reactive(False)

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
    NavRail.-collapsed {
        width: 5;
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
        margin: 0;
        padding: 0 1;
        border: none;
        border-left: solid transparent;
        background: transparent;
        color: $text-muted;
        text-align: left;
        content-align: left middle;
    }
    NavRail.-collapsed .nav-btn {
        padding: 0;
        content-align: center middle;
        text-align: center;
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
    NavRail .nav-collapse {
        dock: bottom;
        width: 100%;
        min-width: 1;
        height: 3;
        margin: 0;
        padding: 0;
        border: none;
        border-top: solid $border;
        background: $boost;
        color: $text-muted;
        text-align: center;
        content-align: center middle;
    }
    NavRail .nav-collapse:hover {
        color: $primary;
        background: $primary 10%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("KAZMA", classes="nav-brand", id="nav-brand")
        for tab_id, label, key in NAV_ITEMS:
            yield Button(
                f" {key}  {label}",
                id=f"nav-{tab_id}",
                classes="nav-btn",
            )
        yield Button(" « ", id="nav-collapse", classes="nav-collapse")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "nav-collapse":
            self.toggle()
            event.stop()
            return
        if bid.startswith("nav-"):
            tab_id = bid[4:]
            self.set_active(tab_id)
            self.post_message(NavSelected(tab_id))

    def toggle(self) -> None:
        """Flip expanded / collapsed rail."""
        self.collapsed = not self.collapsed

    def watch_collapsed(self, collapsed: bool) -> None:
        """Apply CSS class and refresh button labels when mode changes."""
        self.set_class(collapsed, "-collapsed")
        self.styles.width = _WIDTH_COLLAPSED if collapsed else _WIDTH_EXPANDED
        self._refresh_labels()

    def _refresh_labels(self) -> None:
        """Update brand + nav button text for current mode."""
        try:
            brand = self.query_one("#nav-brand", Static)
            brand.update("K" if self.collapsed else "KAZMA")
        except Exception:
            pass
        for tab_id, label, key in NAV_ITEMS:
            try:
                btn = self.query_one(f"#nav-{tab_id}", Button)
                if self.collapsed:
                    btn.label = f"{key}"
                    btn.tooltip = label
                else:
                    btn.label = f" {key}  {label}"
                    btn.tooltip = None
            except Exception:
                pass
        try:
            toggle = self.query_one("#nav-collapse", Button)
            if self.collapsed:
                toggle.label = " » "
                toggle.tooltip = "Expand nav ([)"
            else:
                toggle.label = " « "
                toggle.tooltip = "Collapse nav ([)"
        except Exception:
            pass

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
