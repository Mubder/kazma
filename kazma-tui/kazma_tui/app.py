"""Kazma TUI — Professional terminal dashboard for the Kazma agent framework.

Architecture: Header (model info) · TabbedContent (Chat | Swarm | Files) · Footer (shortcuts)
"""

from __future__ import annotations

import logging
import sys

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, TabbedContent, TabPane

from kazma_tui.chat import ChatPanel
from kazma_tui.footer import KazmaFooter
from kazma_tui.header import KazmaHeader
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
    ]

    def compose(self) -> ComposeResult:
        yield KazmaHeader()
        with TabbedContent(initial="chat"):
            with TabPane("Chat", id="chat"):
                yield ChatPanel()
            with TabPane("Swarm", id="swarm"):
                yield SwarmPanel()
        yield KazmaFooter()

    def action_copy(self) -> None:
        """Copy selected text or last response to clipboard."""
        try:
            chat = self.query_one(ChatPanel)
            chat.action_copy_last()
        except Exception:
            pass

    def action_command_palette(self) -> None:
        from kazma_tui.command_palette import CommandPalette
        self.push_screen(CommandPalette())


def main() -> None:
    try:
        KazmaTUI().run()
    except Exception:
        logger.exception("Kazma TUI crashed")
        sys.exit(1)
