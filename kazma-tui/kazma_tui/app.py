"""Kazma TUI — Professional terminal dashboard for the Kazma agent framework.

Architecture: Header · Tabs (Chat | Files | Swarm | Settings) · Footer
"""

from __future__ import annotations

import logging
import sys

from textual.app import App, ComposeResult
from textual.widgets import Footer, TabbedContent, TabPane

from kazma_tui.chat import ChatPanel
from kazma_tui.files import FilesPanel
from kazma_tui.header import KazmaHeader
from kazma_tui.settings_panel import SettingsPanel
from kazma_tui.swarm import SwarmPanel
from kazma_tui.theme import KAZMA_THEME

logger = logging.getLogger(__name__)


class KazmaTUI(App[None]):
    """Kazma Terminal Dashboard — kazma.ai Web UI theme."""

    TITLE = "Kazma"
    CSS = KAZMA_THEME

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+p", "command_palette", "Commands"),
        ("ctrl+c", "copy", "Copy"),
        ("ctrl+f", "focus_input", "Focus Chat"),
        ("ctrl+l", "theme_notice", "Theme"),
    ]

    def compose(self) -> ComposeResult:
        yield KazmaHeader()
        with TabbedContent(initial="chat"):
            with TabPane("Chat", id="chat"):
                yield ChatPanel()
            with TabPane("Files", id="files"):
                yield FilesPanel()
            with TabPane("Swarm", id="swarm"):
                yield SwarmPanel()
            with TabPane("Settings", id="settings"):
                yield SettingsPanel()
        yield Footer()

    def action_copy(self) -> None:
        try:
            self.query_one(ChatPanel).action_copy_last()
        except Exception:
            pass

    def action_command_palette(self) -> None:
        from kazma_tui.command_palette import CommandPalette
        self.push_screen(CommandPalette())

    def action_focus_input(self) -> None:
        try:
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    def action_theme_notice(self) -> None:
        self.notify("Light theme coming in a future update", severity="information", timeout=3)


def main() -> None:
    try:
        KazmaTUI().run()
    except Exception:
        logger.exception("Kazma TUI crashed")
        sys.exit(1)
