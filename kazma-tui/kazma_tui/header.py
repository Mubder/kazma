"""Custom header widget — kazma.ai styled ASCII logo + provider/model info."""

from __future__ import annotations

import logging

from kazma_core.model_registry import get_model_registry
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)

_FALLBACK_TEXT = "Not configured"

KA_LOGO = r"""  [bold $primary]██╗  ██╗  █████╗  ███████╗ ███╗   ███╗  █████╗[/bold $primary]
  [bold $secondary]██║ ██╔╝ ██╔══██╗ ╚══███╔╝ ████╗ ████║ ██╔══██╗[/bold $secondary]
  [bold $primary]█████╔╝  ███████║   ███╔╝  ██╔████╔██║ ███████║[/bold $primary]
  [bold $secondary]██╔═██╗  ██╔══██║  ███╔╝   ██║╚██╔╝██║ ██╔══██║[/bold $secondary]
  [bold $primary]██║  ██╗ ██║  ██║ ███████╗ ██║ ╚═╝ ██║ ██║  ██║[/bold $primary]
  [bold $secondary]╚═╝  ╚═╝ ╚═╝  ╚═╝ ╚══════╝ ╚═╝     ╚═╝ ╚═╝  ╚═╝[/bold $secondary]"""

KA_TAGLINE = "Production-grade autonomous AI agent framework"


class HeaderProviderModel(Widget):
    """Header bar with ASCII KA logo, tagline, and provider/model info.

    Reads from ``ModelRegistry.get_active_profile()`` on mount.
    """

    DEFAULT_CSS = """
    HeaderProviderModel {
        dock: top;
        width: 100%;
        height: auto;
        min-height: 8;
        background: $surface;
        padding: 0 2;
    }

    HeaderProviderModel > #ka-logo {
        width: 100%;
        content-align: center middle;
        margin: 1 0 0 0;
    }

    HeaderProviderModel > #ka-tagline {
        width: 100%;
        content-align: center middle;
        color: $text-muted;
        text-style: italic;
        margin: 0 0 1 0;
    }

    HeaderProviderModel > #header-profile {
        width: 100%;
        content-align: center middle;
        color: $primary;
        text-style: bold;
        border-bottom: heavy $primary;
        padding-bottom: 1;
    }
    """

    provider: reactive[str] = reactive(_FALLBACK_TEXT)
    model: reactive[str] = reactive(_FALLBACK_TEXT)

    def _build_header_text(self) -> str:
        """Return the provider/model display string."""
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

    def compose(self) -> ComposeResult:
        """Compose the header with ASCII logo, tagline, and profile."""
        yield Static(KA_LOGO, id="ka-logo")
        yield Static(f"  {KA_TAGLINE}", id="ka-tagline")
        yield Static(_FALLBACK_TEXT, id="header-profile")

    def on_mount(self) -> None:
        """Fetch the active profile once on mount."""
        self.refresh_profile()

    def refresh_profile(self) -> None:
        """Re-read the active profile and update the display."""
        text = self._build_header_text()
        self.provider = text
        profile_widget = self.query_one("#header-profile", Static)
        profile_widget.update(f"  {text}")
