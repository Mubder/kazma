"""Kazma TUI header — logo + active model/provider with enhanced styling."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

__all__ = ["KazmaHeader"]

_FALLBACK_TEXT = "No config"


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

    provider: reactive[str] = reactive("?")
    model: reactive[str] = reactive("?")

    def _build_header_text(self) -> str:
        """Build header text from ModelRegistry (for testing)."""
        try:
            registry = _get_model_registry()
            profile = registry.get_active_profile()
            model = profile.get("model", "?")
            provider = profile.get("provider", "?")
            return f"╭─ [bold $primary]KAZMA[/] ─╮  [dim]{provider} / {model}[/]"
        except Exception:
            return f"╭─ [bold $primary]KAZMA[/] ─╮  [dim]{_FALLBACK_TEXT}[/]"

    def on_mount(self) -> None:
        """Initialize header with provider/model info."""
        self.update(self._build_header_text())


def _get_model_registry():
    """Get the ModelRegistry singleton (module-level for testability)."""
    from kazma_core.model_registry import get_model_registry as _get_reg

    return _get_reg()
