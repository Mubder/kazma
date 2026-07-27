"""Kazma TUI header — brand + provider/model strip (v2 shell)."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widgets import Static

__all__ = ["KazmaHeader"]

_FALLBACK_TEXT = "no model"


class KazmaHeader(Static):
    """Top chrome: brand · connection · provider/model.

    Compact single-line header (height 3 with padding) for a denser pro shell.
    """

    DEFAULT_CSS = """
    KazmaHeader {
        dock: top;
        height: 3;
        background: $panel;
        border-bottom: tall $border;
        color: $text;
        content-align: left middle;
        padding: 0 2;
        text-style: none;
    }
    """

    provider: reactive[str] = reactive("?")
    model: reactive[str] = reactive("?")

    def _build_header_text(self) -> str:
        """Build header markup from ModelRegistry."""
        try:
            registry = _get_model_registry()
            profile = registry.get_active_profile()
            model = str(profile.get("model", "?") or "?")
            provider = str(profile.get("provider", "?") or "?")
            # Truncate long model ids for terminal width
            if len(model) > 36:
                model = model[:33] + "…"
            return (
                f"[bold $primary]◆ KAZMA[/]  "
                f"[dim]│[/]  "
                f"[$success]●[/] [dim]ready[/]  "
                f"[dim]│[/]  "
                f"[dim]{provider}[/][dim]/[/][$text]{model}[/]"
            )
        except Exception:
            return (
                f"[bold $primary]◆ KAZMA[/]  "
                f"[dim]│[/]  "
                f"[$warning]○[/] [dim]{_FALLBACK_TEXT}[/]"
            )

    def on_mount(self) -> None:
        """Initialize header with provider/model info."""
        self.update(self._build_header_text())

    def refresh_model(self) -> None:
        """Re-read active model (call after provider switch)."""
        self.update(self._build_header_text())


def _get_model_registry():
    """Get the ModelRegistry singleton (module-level for testability)."""
    from kazma_core.model_registry import get_model_registry as _get_reg

    return _get_reg()
