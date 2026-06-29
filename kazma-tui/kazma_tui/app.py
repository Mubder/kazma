"""Main Kazma TUI application — Textual-based professional dashboard."""

from __future__ import annotations

import logging
import sys

from textual.app import App, ComposeResult
from textual.widgets import Static

from kazma_tui.footer import FooterShortcuts
from kazma_tui.header import HeaderProviderModel

logger = logging.getLogger(__name__)


class PlaceholderWidget(Static):
    """A placeholder widget for future dashboard content."""

    def __init__(self, label: str = "Dashboard") -> None:
        super().__init__(f"[bold]{label}[/bold] — Coming soon")


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
    PlaceholderWidget {
        height: 1fr;
        border: solid $primary;
        content-align: center middle;
        padding: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Create the application layout."""
        yield HeaderProviderModel()
        yield PlaceholderWidget("Dashboard")
        yield FooterShortcuts()


def main() -> None:
    """Launch the Kazma TUI application."""
    try:
        app = KazmaTUI()
        app.run()
    except Exception:
        logger.exception("Failed to launch Kazma TUI")
        sys.exit(1)
