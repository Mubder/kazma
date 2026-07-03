"""Chat screen — main conversation view with metrics dashboard."""

from __future__ import annotations

from textual.screen import Screen
from textual.widgets import Static

from kazma_tui.chat import ChatPanel
from kazma_tui.dashboard import MetricsDashboard
from kazma_tui.header import HeaderProviderModel


class ChatScreen(Screen):
    """Primary screen: header + metrics + chat conversation."""

    def compose(self):
        yield HeaderProviderModel()
        yield MetricsDashboard()
        yield ChatPanel()
