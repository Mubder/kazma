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
        yield Static("[bold $primary]Settings[/]  ·  [dim]Toggle features[/]", classes="section-label")
        sel: SelectionList = SelectionList()
        for label, key, default in self.SETTINGS:
            try:
                from kazma_core.config_store import get_config_store
                val = get_config_store().get(key)
                if val is None:
                    val = default
            except Exception:
                val = default
            # SelectionList.add_option() takes (label, id)
            sel.add_option((label, key))
            if val:
                sel.select(key)
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
