"""Command palette — fuzzy-searchable action launcher.

Press Ctrl+P to open. Search for commands, keybindings, and actions.
Select with Enter, dismiss with Escape.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView


class CommandPalette(ModalScreen[str | None]):
    """Modal overlay with a fuzzy-searchable command list."""

    COMMANDS: list[tuple[str, str]] = [
        ("/help", "Show help message"),
        ("/clear", "Clear chat"),
        ("/model", "List available models"),
        ("/quit", "Exit application"),
        ("Ctrl+T", "Switch tab"),
        ("Ctrl+Q", "Quit"),
        ("Tab: Chat", "Switch to Chat tab"),
        ("Tab: Swarm", "Switch to Swarm tab"),
    ]

    DEFAULT_CSS = """
    CommandPalette {
        align: center middle;
    }
    CommandPalette > .palette-box {
        width: 60%;
        max-height: 60%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    CommandPalette Input {
        width: 100%;
        margin-bottom: 1;
    }
    CommandPalette ListView {
        height: 1fr;
        background: transparent;
    }
    CommandPalette ListItem {
        padding: 0 1;
    }
    CommandPalette ListItem.-highlight {
        background: $accent 20%;
    }
    """

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search commands...", id="palette-search")
        yield ListView(
            *[ListItem(f"{name}  —  {desc}") for name, desc in self.COMMANDS],
            id="palette-list",
        )

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter the list based on search query."""
        query = event.value.lower()
        lst = self.query_one(ListView)
        lst.clear()
        for name, desc in self.COMMANDS:
            if query in name.lower() or query in desc.lower():
                lst.append(ListItem(f"{name}  —  {desc}"))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle selection."""
        if event.item is None:
            return
        text = str(event.item.render())
        cmd = text.split("  —  ")[0]
        # Route common commands
        if cmd == "/quit":
            self.app.exit()
        elif cmd == "/clear":
            self._send_command("/clear")
        elif cmd == "/help":
            self._send_command("/help")
        elif cmd == "/model":
            self._send_command("/model")
        elif cmd == "Tab: Chat":
            self.dismiss("chat")
        elif cmd == "Tab: Swarm":
            self.dismiss("swarm")
        else:
            self.dismiss(cmd)

    def _send_command(self, cmd: str) -> None:
        """Send a command to the chat input."""
        try:
            inp = self.app.query_one("#chat-input", Input)
            inp.value = cmd
            inp.action_submit()
        except Exception:
            pass
        self.dismiss(None)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
