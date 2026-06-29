"""Main Kazma TUI application — Textual-based professional dashboard."""

from __future__ import annotations

import logging
import sys

from textual.app import App, ComposeResult

from kazma_tui.dashboard import MetricsDashboard
from kazma_tui.footer import FooterShortcuts
from kazma_tui.header import HeaderProviderModel

logger = logging.getLogger(__name__)


class KazmaTUI(App[None]):
    """Kazma TUI — Professional terminal dashboard.

    Features:
    - Metrics dashboard with CPU/Memory/RPM/Latency
    - Chat interface with command support
    - ModelRegistry integration
    """

    TITLE = "Kazma TUI"
    CSS = """
    Screen {
        layout: vertical;
    }
    """

    def compose(self) -> ComposeResult:
        """Create the application layout."""
        yield HeaderProviderModel()
        yield MetricsDashboard()
        yield FooterShortcuts()


def main() -> None:
    """Launch the Kazma TUI application."""
    try:
        app = KazmaTUI()
        app.run()
    except Exception:
        logger.exception("Failed to launch Kazma TUI")
        sys.exit(1)
