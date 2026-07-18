"""Settings tab — SelectionList for feature toggles, persisted to ConfigStore.

Enhanced with theme switching and user preferences.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll, Container
from textual.widgets import SelectionList, Static, Button, Label

from kazma_tui.themes.theme_manager import ThemeManager
from kazma_tui.widgets.confirm_dialog import ConfirmDialog

__all__ = ["SettingsPanel"]

logger = logging.getLogger(__name__)

# Toggling this off removes the human-approval gate for danger tools
# (file writes, shell exec, code exec) — see AGENTS.md "HITL Approval
# Gates". Disabling it is a safety-relevant action, so it needs an
# explicit confirmation rather than taking effect on a single checkbox
# click like the other feature toggles.
_HITL_KEY = "safety.hitl_enabled"


class SettingsPanel(VerticalScroll):
    """Settings: SelectionList toggles read/written to ConfigStore.
    
    Features:
    - Feature toggles persisted to ConfigStore
    - Theme switching with preview
    - User preferences management
    """

    DEFAULT_CSS = """
    SettingsPanel { 
        height: 1fr; 
        background: $surface; 
        padding: 1 2;
    }
    SettingsPanel .settings-section {
        margin: 1 0;
        padding: 1;
        background: $panel;
        border: solid $border;
    }
    SettingsPanel .settings-title {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }
    SettingsPanel .theme-buttons {
        align: center middle;
    }
    SettingsPanel .theme-buttons Button {
        margin: 0 1;
        min-width: 15;
    }
    """

    BINDINGS = [
        Binding("r", "refresh_settings", "Refresh"),
    ]

    SETTINGS = [
        ("Enable RAG memory", "memory.enabled", True),
        ("Enable auto-summarization", "context.auto_summarize", True),
        ("Enable cost breaker", "cost.breaker_enabled", True),
        ("Enable tracing", "tracing.enabled", False),
        ("Enable cron", "cron.enabled", False),
        ("HITL approval (danger tools)", "safety.hitl_enabled", True),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_saved: dict[str, bool] = {}

    @property
    def theme_manager(self) -> ThemeManager:
        if hasattr(self, "app") and hasattr(self.app, "theme_manager") and self.app.theme_manager:
            return self.app.theme_manager
        if not hasattr(self, "_theme_manager_fallback"):
            self._theme_manager_fallback = ThemeManager()
        return self._theme_manager_fallback

    def _read_config(self, key: str, default: bool) -> bool:
        try:
            from kazma_core.config_store import get_config_store
            val = get_config_store().get(key)
            return bool(val) if val is not None else default
        except Exception:
            return default

    def compose(self) -> ComposeResult:
        # Populate _last_saved from config before any change events fire
        for label, key, default in self.SETTINGS:
            self._last_saved[key] = self._read_config(key, default)

        yield Static("Settings", classes="section-label")

        # Feature Toggles Section
        with Container(classes="settings-section"):
            yield Static("Feature Toggles", classes="settings-title")
            sel: SelectionList = SelectionList()
            for label, key, default in self.SETTINGS:
                initial = self._read_config(key, default)
                sel.add_option((label, key, initial))
            yield sel

        # Preferences Section
        with Container(classes="settings-section"):
            yield Static("Preferences", classes="settings-title")
            yield Label(
                f"Auto-scroll: {'on' if self.theme_manager.auto_scroll else 'off'}",
                id="auto-scroll-label"
            )
            yield Label(
                f"Animations: {'on' if self.theme_manager.animations_enabled else 'off'}",
                id="animations-label"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        pass

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        sel = event.selection_list
        try:
            from kazma_core.config_store import get_config_store
            cs = get_config_store()
            # Only persist keys whose value actually flipped — avoid the
            # round-trip per hover/move event that SelectedChanged fires on.
            changed = 0
            confirm_hitl_disable = False
            for _label, key, _default in self.SETTINGS:
                new_val = key in sel.selected
                prev_val = self._last_saved.get(key)
                if prev_val is None or prev_val != new_val:
                    if key == _HITL_KEY and prev_val and not new_val:
                        # Defer persisting until the user confirms — leave
                        # _last_saved as-is so a cancel is a clean no-op.
                        confirm_hitl_disable = True
                        continue
                    cs.set(key, new_val)
                    self._last_saved[key] = new_val
                    changed += 1
            if changed:
                self.notify(f"Settings saved ({changed})", severity="information")
            if confirm_hitl_disable:
                self._confirm_hitl_disable(sel, cs)
        except Exception as e:
            self.notify(f"Failed to save settings: {e}", severity="error")

    def _confirm_hitl_disable(self, sel: SelectionList, cs) -> None:
        """Ask for explicit confirmation before disabling HITL approval."""

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                cs.set(_HITL_KEY, False)
                self._last_saved[_HITL_KEY] = False
                self.notify(
                    "HITL approval disabled — danger tools will run without approval",
                    severity="warning",
                )
            else:
                # Re-check the box; _last_saved was never flipped so this
                # is a no-op for ConfigStore.
                sel.select(_HITL_KEY)

        dialog = ConfirmDialog(
            "Disabling HITL approval means dangerous tools (file writes, "
            "shell commands, code execution) will run WITHOUT requiring "
            "your approval. Are you sure you want to disable it?",
            title="Disable HITL Approval",
            confirm_text="Disable",
        )
        self.app.push_screen(dialog, on_confirm)

    def action_refresh_settings(self) -> None:
        """Refresh settings display."""
        self.notify("Settings refreshed", severity="information")
