"""Command palette — Ctrl+P fuzzy-searchable action launcher."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static


class CommandPalette(ModalScreen[str | None]):
    """Modal overlay with fuzzy-searchable command list."""

    COMMANDS: list[tuple[str, str, str | None]] = [
        ("/help", "Show help", None),
        ("/clear", "Clear chat", None),
        ("/model", "List available models", None),
        ("/quit", "Exit Kazma TUI", None),
        ("-", "", None),
        ("Ctrl+P", "Command palette", "ctrl+p"),
        ("Ctrl+Q", "Quit", "ctrl+q"),
        ("Ctrl+C", "Copy last response", "ctrl+c"),
        ("Tab: Chat", "Switch to Chat tab", None),
        ("Tab: Swarm", "Switch to Swarm tab", None),
    ]

    DEFAULT_CSS = """
    CommandPalette {
        align: center middle;
    }
    CommandPalette Vertical {
        width: 55%;
        max-height: 55%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    CommandPalette Input {
        width: 100%;
        margin-bottom: 1;
        background: $boost;
        border: solid $border;
    }
    CommandPalette ListView {
        height: 1fr;
        background: transparent;
    }
    CommandPalette ListItem {
        padding: 0 1;
        color: $text-muted;
    }
    CommandPalette ListItem.-highlight {
        background: $primary 12%;
        color: $text;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Input(placeholder="Search commands...", id="palette-search")
            yield ListView(
                *[ListItem(Static(f"  {name}    {desc}")) for name, desc, _ in self.COMMANDS if name != "-"],
                id="palette-list",
            )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        q = event.value.lower()
        lst = self.query_one(ListView)
        lst.clear()
        for name, desc, _ in self.COMMANDS:
            if name == "-":
                continue
            if q in name.lower() or q in desc.lower():
                lst.append(ListItem(Static(f"  {name}    {desc}")))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item is None:
            return
        text = str(event.item.query_one(Static)._renderable).strip()
        cmd = text.split("    ")[0].strip()
        if cmd == "/quit":
            self.app.exit()
        elif cmd in ("/clear", "/help", "/model"):
            self._route_command(cmd)
        elif cmd == "Tab: Chat":
            self._switch_tab("chat")
            self.dismiss(None)
        elif cmd == "Tab: Swarm":
            self._switch_tab("swarm")
            self.dismiss(None)
        else:
            self.dismiss(None)

    def _route_command(self, cmd: str) -> None:
        """Send command to chat panel directly."""
        try:
            from kazma_tui.chat import ChatPanel
            chat = self.app.query_one(ChatPanel)
            if cmd == "/help":
                chat.write("system", "/help, /clear, /model, /quit — Ctrl+P for palette")
            elif cmd == "/clear":
                self.app.query_one("RichLog#chat-log").clear()
            elif cmd == "/model":
                try:
                    from kazma_core.settings.model_registry import get_model_list_text
                    chat.write("system", get_model_list_text("tui"))
                except Exception:
                    chat.write("error", "Model registry unavailable")
        except Exception:
            pass
        self.dismiss(None)

    def _switch_tab(self, tab_id: str) -> None:
        try:
            tabs = self.app.query_one("TabbedContent")
            tabs.active = tab_id
        except Exception:
            pass

    def key_escape(self) -> None:
        self.dismiss(None)
