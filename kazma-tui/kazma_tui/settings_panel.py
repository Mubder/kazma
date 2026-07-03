"""Settings tab — SelectionList for feature toggles, persisted to ConfigStore.

Enhanced with theme switching and user preferences.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll, Container
from textual.widgets import SelectionList, Static, Button, Label

from kazma_tui.themes.theme_manager import ThemeManager


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
        self.theme_manager = ThemeManager()

    def _read_config(self, key: str, default: bool) -> bool:
        try:
            from kazma_core.config_store import get_config_store
            val = get_config_store().get(key)
            return bool(val) if val is not None else default
        except Exception:
            return default

    def compose(self) -> ComposeResult:
        # Feature Toggles Section
        yield Static(
            "[bold $primary]⚙️ Settings[/]  ·  [dim]Configure features and preferences[/]",
            classes="section-label"
        )
        
        with Container(classes="settings-section"):
            yield Static("[bold]$toggle Feature Toggles[/]", classes="settings-title")
            sel: SelectionList = SelectionList()
            for label, key, default in self.SETTINGS:
                initial = self._read_config(key, default)
                sel.add_option((label, key, initial))
            sel.border_title = ""
            yield sel
        
        # Theme Selection Section
        with Container(classes="settings-section"):
            yield Static("[bold]$palette Theme Selection[/]", classes="settings-title")
            yield Static(
                f"Current: [bold $primary]{self.theme_manager.current_theme}[/]",
                id="current-theme-label"
            )
            with Container(classes="theme-buttons"):
                for theme_name in self.theme_manager.get_available_themes():
                    display_name = theme_name.replace("-", " ").title()
                    btn = Button(display_name, id=f"theme-{theme_name}", variant="default")
                    if theme_name == self.theme_manager.current_theme:
                        btn.variant = "primary"
                    yield btn
        
        # Preferences Section
        with Container(classes="settings-section"):
            yield Static("[bold]$gauge Preferences[/]", classes="settings-title")
            yield Label(
                f"Auto-scroll: {'✓' if self.theme_manager.auto_scroll else '✗'}",
                id="auto-scroll-label"
            )
            yield Label(
                f"Animations: {'✓' if self.theme_manager.animations_enabled else '✗'}",
                id="animations-label"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle theme button presses."""
        button_id = event.button.id
        if button_id and button_id.startswith("theme-"):
            theme_name = button_id[6:]  # Remove "theme-" prefix
            try:
                self.theme_manager.set_theme(theme_name)
                # Apply theme to app
                self.theme_manager.apply_theme(self.app, theme_name)
                # Update UI
                self._update_theme_buttons()
                self.query_one("#current-theme-label", Static).update(
                    f"Current: [bold $primary]{theme_name}[/]"
                )
                self.notify(f"Theme changed to {theme_name}", severity="information")
            except Exception as e:
                self.notify(f"Failed to change theme: {e}", severity="error")

    def _update_theme_buttons(self) -> None:
        """Update button variants to reflect current theme."""
        current = self.theme_manager.current_theme
        for theme_name in self.theme_manager.get_available_themes():
            try:
                btn = self.query_one(f"#theme-{theme_name}", Button)
                btn.variant = "primary" if theme_name == current else "default"
            except Exception:
                pass

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        sel = event.selection_list
        try:
            from kazma_core.config_store import get_config_store
            cs = get_config_store()
            for label, key, _default in self.SETTINGS:
                cs.set(key, key in sel.selected)
            self.notify("Settings saved", severity="information")
        except Exception as e:
            self.notify(f"Failed to save settings: {e}", severity="error")

    def action_refresh_settings(self) -> None:
        """Refresh settings display."""
        self.notify("Settings refreshed", severity="information")
