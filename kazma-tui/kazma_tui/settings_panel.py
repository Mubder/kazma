"""Settings tab — SelectionList for config management."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import SelectionList, Static


class SettingsPanel(VerticalScroll):
    """Settings: SelectionList for toggling features."""

    DEFAULT_CSS = """
    SettingsPanel { height: 1fr; background: $surface; padding: 1 2; }
    """

    SETTINGS = [
        ("Enable RAG memory", "memory.enabled", True),
        ("Enable auto-summarization", "context.auto_summarize", True),
        ("Enable cost breaker", "cost.breaker_enabled", True),
        ("Enable tracing", "tracing.enabled", False),
        ("Enable cron", "cron.enabled", False),
        ("HITL approval (danger tools)", "safety.hitl_enabled", True),
    ]

    def compose(self) -> ComposeResult:
        yield Static("[bold $primary]Settings[/]  ·  [dim]Toggle features on/off[/]", classes="section-label")
        sel = SelectionList[int]()
        for label, key, default in self.SETTINGS:
            # Try to read the config value, fall back to default
            try:
                from kazma_core.config_store import get_config_store
                cs = get_config_store()
                val = cs.get(key)
                if val is None:
                    val = default
            except Exception:
                val = default
            sel.add_option((key, label), bool(val))
        sel.border_title = "Feature Toggles"
        yield sel

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged[int]) -> None:
        """Persist the toggle change to ConfigStore."""
        sel = event.selection_list
        for option in sel.options:
            key = option.id
            checked = key in sel.selected
            try:
                from kazma_core.config_store import get_config_store
                get_config_store().set(key, checked)
            except Exception:
                pass
