"""Main Kazma TUI application — Textual-based professional dashboard.

Adopts the kazma.ai design language: deep charcoal background,
cyan primary accent, purple secondary, gradient borders.
"""

from __future__ import annotations

import logging
import sys

from textual.app import App, ComposeResult

from kazma_tui.chat import ChatPanel
from kazma_tui.dashboard import MetricsDashboard
from kazma_tui.footer import FooterShortcuts
from kazma_tui.header import HeaderProviderModel
from kazma_tui.theme import KAZMA_CSS

logger = logging.getLogger(__name__)


class KazmaTUI(App[None]):
    """Kazma TUI — Production terminal dashboard.

    Features:
    - Metrics dashboard with CPU/Memory/RPM/Latency (gauge-style)
    - Chat interface with command support
    - ModelRegistry integration
    - Split-pane swarm panel (via Tab)
    - kazma.ai dark theme

    Keys:
        Tab     — Switch focus between panels
        Ctrl+Q  — Quit
        Enter   — Send chat message
    """

    TITLE = "Kazma TUI"
    CSS = KAZMA_CSS

    BINDINGS = [
        ("ctrl+q", "quit", "Quit"),
        ("ctrl+y", "copy_last", "Copy last"),
    ]

    def compose(self) -> ComposeResult:
        """Create the application layout."""
        yield HeaderProviderModel()
        yield MetricsDashboard()
        yield ChatPanel()
        yield FooterShortcuts()

    def action_copy_last(self) -> None:
        """Copy the last chat message to clipboard."""
        try:
            chat = self.query_one(ChatPanel)
            chat.action_copy_last()
        except Exception:
            pass


def main() -> None:
    """Launch the Kazma TUI application."""
    try:
        app = KazmaTUI()
        app.run()
    except Exception:
        logger.exception("Failed to launch Kazma TUI")
        sys.exit(1)
