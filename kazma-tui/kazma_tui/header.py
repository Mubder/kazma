"""Kazma TUI header — logo + active model/provider."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static
from textual.widget import Widget


class KazmaHeader(Static):
    """Header: Kazma logo + model info."""

    DEFAULT_CSS = """
    KazmaHeader {
        dock: top;
        height: 3;
        background: $panel;
        border-bottom: solid $primary 40%;
        color: $text;
        content-align: center middle;
        text-style: bold;
    }
    """

    def on_mount(self) -> None:
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            profile = registry.get_active_profile()
            model = profile.get("model", "?")
            provider = profile.get("provider", "?")
            self.update(f"[bold $primary]KAZMA[/]  ·  [dim]{provider} / {model}[/]")
        except Exception:
            self.update("[bold $primary]KAZMA[/]  ·  [dim]No config[/]")
