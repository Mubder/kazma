"""Custom header widget showing active provider and model from ModelRegistry.

This module provides ``HeaderProviderModel``, a Textual widget that displays
the application title along with the currently active provider and model name
sourced from the ``ModelRegistry`` singleton.

The widget is a read-only consumer — it never calls mutation methods on
``ModelRegistry``.
"""

from __future__ import annotations

import logging

from kazma_core.model_registry import get_model_registry  # type: ignore[import-untyped]
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)

_FALLBACK_TEXT = "Not configured"


class HeaderProviderModel(Widget):
    """Header bar displaying app title and active provider/model info.

    Reads from ``ModelRegistry.get_active_profile()`` on mount and exposes
    ``refresh_profile()`` for periodic or on-demand updates.

    Layout::

        Kazma TUI  |  openai / gpt-4o
    """

    DEFAULT_CSS = """
    HeaderProviderModel {
        dock: top;
        width: 100%;
        height: 1;
        background: $accent;
        color: $text;
        content-align: left middle;
        padding: 0 1;
    }

    HeaderProviderModel > #header-title {
        width: auto;
        text-style: bold;
    }

    HeaderProviderModel > #header-separator {
        width: auto;
        margin: 0 1;
    }

    HeaderProviderModel > #header-profile {
        width: auto;
    }
    """

    provider: reactive[str] = reactive(_FALLBACK_TEXT)
    model: reactive[str] = reactive(_FALLBACK_TEXT)

    # ── Public helpers (used by tests) ──────────────────────────────

    def _build_header_text(self) -> str:
        """Return the provider/model display string.

        Calls ``get_active_profile()`` on the ``ModelRegistry`` singleton.
        Returns a human-readable fallback when the registry is not initialised
        or the profile is empty.
        """
        try:
            registry = get_model_registry()
            profile = registry.get_active_profile()
        except (RuntimeError, Exception) as exc:
            logger.debug("ModelRegistry unavailable: %s", exc)
            return _FALLBACK_TEXT

        provider_name = (profile.get("provider") or "").strip()
        model_name = (profile.get("model") or "").strip()

        if not provider_name and not model_name:
            return _FALLBACK_TEXT
        if not provider_name:
            return model_name
        if not model_name:
            return provider_name
        return f"{provider_name} / {model_name}"

    # ── Textual lifecycle ───────────────────────────────────────────

    def compose(self) -> ComposeResult:
        """Compose the header layout: title | separator | profile info."""
        yield Static("Kazma TUI", id="header-title")
        yield Static("|", id="header-separator")
        yield Static(_FALLBACK_TEXT, id="header-profile")

    def on_mount(self) -> None:
        """Fetch the active profile once on mount."""
        self.refresh_profile()

    def refresh_profile(self) -> None:
        """Re-read the active profile and update the display."""
        text = self._build_header_text()
        self.provider = text
        profile_widget = self.query_one("#header-profile", Static)
        profile_widget.update(text)
