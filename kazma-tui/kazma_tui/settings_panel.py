"""Settings tab — SelectionList for feature toggles, persisted to ConfigStore."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import SelectionList, Static


class SettingsPanel(VerticalScroll):
    """Settings: SelectionList toggles read/written to ConfigStore."""

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

    def _read_config(self, key: str, default: bool) -> bool:
        try:
            from kazma_core.config_store import get_config_store
            val = get_config_store().get(key)
            return bool(val) if val is not None else default
        except Exception:
            return default

    def compose(self) -> ComposeResult:
        yield Static("[bold $primary]Settings[/]  ·  [dim]Toggle features on/off[/]", classes="section-label")
        sel: SelectionList = SelectionList()
        for label, key, default in self.SETTINGS:
            initial = self._read_config(key, default)
            sel.add_option((label, key, initial))
        sel.border_title = "Feature Toggles"
        yield sel

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        sel = event.selection_list
        try:
            from kazma_core.config_store import get_config_store
            cs = get_config_store()
            for label, key, _default in self.SETTINGS:
                cs.set(key, key in sel.selected)
        except Exception:
            pass
