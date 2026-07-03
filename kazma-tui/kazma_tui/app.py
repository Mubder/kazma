"""Main Kazma TUI application — Textual-based professional dashboard.

Adopts the kazma.ai design language: deep charcoal background,
cyan primary accent, purple secondary, tabbed navigation.
"""

from __future__ import annotations

import logging
import sys

from textual.app import App, ComposeResult
from textual.widgets import TabbedContent, TabPane

from kazma_tui.chat import ChatPanel
from kazma_tui.dashboard import MetricsDashboard
from kazma_tui.footer import FooterShortcuts
from kazma_tui.header import HeaderProviderModel
from kazma_tui.panels.swarm_panel import SwarmPanel
from kazma_tui.theme import KAZMA_CSS

logger = logging.getLogger(__name__)


class KazmaTUI(App[None]):
    """Kazma TUI — Production terminal dashboard.

    Features:
    - Tabbed navigation: Chat | Swarm
    - Metrics dashboard with CPU/Memory/RPM/Latency
    - Chat interface with markdown and streaming
    - ModelRegistry integration
    - Swarm worker registry with live bus events
    - Context-sensitive footer shortcuts
    - kazma.ai dark theme

    Keys:
        Ctrl+T  — Switch tabs
        Ctrl+P  — Command palette
        Ctrl+Q  — Quit
        Enter   — Send chat message
    """

    TITLE = "Kazma TUI"
    CSS = KAZMA_CSS

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+y", "copy_last", "Copy last"),
        ("ctrl+p", "command_palette", "Commands"),
    ]

    def compose(self) -> ComposeResult:
        """Create the tabbed application layout.

        Widgets are composed directly inside TabPane (not Screen wrappers)
        to avoid full re-renders on tab switches.
        """
        yield HeaderProviderModel()
        yield MetricsDashboard()
        with TabbedContent(initial="chat-tab"):
            with TabPane("Chat", id="chat-tab"):
                yield ChatPanel()
            with TabPane("Swarm", id="swarm-tab"):
                yield SwarmPanel()
        yield FooterShortcuts()

    def action_copy_last(self) -> None:
        """Copy the last chat message to clipboard."""
        try:
            chat = self.query_one(ChatPanel)
            chat.action_copy_last()
        except Exception:
            pass

    def action_command_palette(self) -> None:
        """Open the fuzzy-searchable command palette."""
        from kazma_tui.screens.command_palette import CommandPalette

        self.push_screen(CommandPalette())


def main() -> None:
    """Launch the Kazma TUI application."""
    try:
        app = KazmaTUI()
        app.run()
    except Exception:
        logger.exception("Failed to launch Kazma TUI")
        sys.exit(1)
