"""Swarm screen — worker registry + live log stream."""

from __future__ import annotations

from textual.screen import Screen

from kazma_tui.panels.swarm_panel import SwarmPanel


class SwarmScreen(Screen):
    """Swarm orchestration: worker table + live bus events."""

    def compose(self):
        yield SwarmPanel()
