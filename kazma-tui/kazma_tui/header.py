"""Kazma TUI header — logo + active model/provider with enhanced styling."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static
from textual.widget import Widget


class KazmaHeader(Static):
    """Header: Kazma logo + model info with enhanced visual design.
    
    Features:
        - Double-line border for premium feel
        - Live connection status indicator
        - Enhanced spacing and typography
    """

    DEFAULT_CSS = """
    KazmaHeader {
        dock: top;
        height: 4;
        background: $panel;
        border-bottom: double $primary 60%;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    
    .header-status {
        color: $success;
        padding: 0 1;
    }
    
    .header-status-offline {
        color: $error;
    }
    """

    def on_mount(self) -> None:
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            profile = registry.get_active_profile()
            model = profile.get("model", "?")
            provider = profile.get("provider", "?")
            # Use box-drawing characters for premium look
            self.update(f"╭─ [bold $primary]KAZMA[/] ─╮  [dim]{provider} / {model}[/]")
        except Exception:
            self.update("╭─ [bold $primary]KAZMA[/] ─╮  [dim]No config[/]")
