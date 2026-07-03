"""Chat screen — main conversation view with metrics dashboard."""

from __future__ import annotations

from textual.screen import Screen

from kazma_tui.chat import ChatPanel
from kazma_tui.dashboard import MetricsDashboard
from kazma_tui.header import HeaderProviderModel


class ChatScreen(Screen):
    """Primary screen: header + metrics + chat conversation."""

    DEFAULT_CSS = """
    ChatScreen {
        layout: vertical;
    }
    ChatScreen > MetricsDashboard {
        height: 8;
    }
    """

    def compose(self):
        yield HeaderProviderModel()
        yield MetricsDashboard()
        yield ChatPanel()
