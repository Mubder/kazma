"""Model picker — interactive modal for selecting a model."""

from __future__ import annotations

import logging
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static
from textual.binding import Binding

__all__ = ["ModelPicker"]

logger = logging.getLogger(__name__)


class ModelPicker(ModalScreen[str | None]):
    """Interactive model selection with fuzzy search.

    Displays all available models grouped by provider.
    Type to filter, arrow keys to navigate, Enter to select.
    """

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "dismiss", "Close", show=False),
    ]

    DEFAULT_CSS = """
    ModelPicker {
        align: center middle;
    }
    ModelPicker > Container {
        width: 50%;
        max-width: 60;
        max-height: 70%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    ModelPicker .picker-header {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
        padding: 1 0;
        border-bottom: solid $border;
    }
    ModelPicker Input {
        width: 100%;
        margin-bottom: 1;
        background: $boost;
        border: solid $border;
        padding: 0 1;
    }
    ModelPicker ListView {
        height: 1fr;
        background: transparent;
        border: solid $border;
    }
    ModelPicker ListItem {
        padding: 0 1;
        color: $text-muted;
        height: auto;
    }
    ModelPicker ListItem.-highlight {
        background: $primary 20%;
        color: $text;
    }
    ModelPicker .model-provider {
        text-style: bold;
        color: $primary;
        padding: 1 1 0 1;
        background: $surface;
    }
    ModelPicker .model-name {
        color: $text;
        padding-left: 2;
    }
    ModelPicker .model-active {
        color: $success;
    }
    ModelPicker .search-info {
        text-align: right;
        color: $text-muted;
        margin-bottom: 1;
        height: 1;
    }
    """

    def __init__(self, active_model: str = "") -> None:
        super().__init__()
        self._active_model = active_model
        self._all_items: list[tuple[str, str, str]] = []  # (type, name, provider)
        self._filtered: list[tuple[str, str, str]] = []

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("Select Model", classes="picker-header")
            yield Input(placeholder="Type to filter...", id="model-search")
            yield Static("", classes="search-info")
            yield ListView(id="model-list")

    def on_mount(self) -> None:
        self._load_models()
        self._filter("")
        self.query_one(Input).focus()

    def _load_models(self) -> None:
        """Load all models from the registry."""
        try:
            from kazma_core.settings.model_registry import get_universal_models
            models = get_universal_models()
            # Group by provider
            by_provider: dict[str, list[str]] = {}
            for m in models:
                provider = m.get("provider", "unknown")
                name = m.get("name", "")
                if name:
                    by_provider.setdefault(provider, []).append(name)
            # Build items: provider headers + model entries
            for provider in sorted(by_provider):
                self._all_items.append(("provider", provider, ""))
                for model_name in sorted(by_provider[provider]):
                    self._all_items.append(("model", model_name, provider))
        except Exception as exc:
            logger.debug("Model picker load failed: %s", exc)

    def _filter(self, query: str) -> None:
        """Filter models by query string."""
        self._filtered = []
        if not query:
            self._filtered = list(self._all_items)
        else:
            q = query.lower()
            for item_type, name, provider in self._all_items:
                if item_type == "provider":
                    # Keep provider if any of its models match
                    has_match = any(
                        n.lower().startswith(q) or q in n.lower()
                        for t, n, p in self._all_items
                        if t == "model" and p == name
                    )
                    if has_match:
                        self._filtered.append((item_type, name, provider))
                elif name.lower().startswith(q) or q in name.lower():
                    self._filtered.append((item_type, name, provider))

        # Rebuild list view
        lst = self.query_one("#model-list", ListView)
        lst.clear()
        for item_type, name, provider in self._filtered:
            if item_type == "provider":
                lst.append(ListItem(Static(f"  {provider}", classes="model-provider")))
            else:
                is_active = name == self._active_model
                cls = "model-name model-active" if is_active else "model-name"
                marker = " * " if is_active else "   "
                lst.append(ListItem(Static(f"{marker}{name}", classes=cls)))

        # Update search info
        model_count = sum(1 for t, _, _ in self._filtered if t == "model")
        info = self.query_one(".search-info", Static)
        info.update(f"{model_count} models")

    def on_input_changed(self, event: Input.Changed) -> None:
        self._filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter in the search box = select the highlighted model."""
        self.action_select()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Click or Enter on a list item = select that model."""
        try:
            child = event.item
            if child is None:
                return
            # Only select model items, not provider headers
            static = child.query_one(Static)
            classes = static.classes
            if "model-provider" in classes:
                return  # Ignore clicks on provider headers
            raw = str(static.render()).strip()
            model_name = raw.lstrip("* ").strip()
            if model_name:
                self.dismiss(model_name)
        except Exception as exc:
            logger.debug("Model picker click select failed: %s", exc)

    def action_cursor_up(self) -> None:
        try:
            self.query_one("#model-list", ListView).action_cursor_up()
        except Exception:
            pass

    def action_cursor_down(self) -> None:
        try:
            self.query_one("#model-list", ListView).action_cursor_down()
        except Exception:
            pass

    def action_select(self) -> None:
        try:
            lst = self.query_one("#model-list", ListView)
            child = lst.highlighted_child

            # If nothing highlighted, pick the first model in the filtered list
            if child is None:
                for item_type, name, _ in self._filtered:
                    if item_type == "model":
                        self.dismiss(name)
                        return
                return

            # Extract model name from the highlighted ListItem's Static
            static = child.query_one(Static)
            classes = static.classes
            # Skip provider headers
            if "model-provider" in classes:
                # Find the next model after this provider in filtered list
                found_provider = False
                for item_type, name, _ in self._filtered:
                    if item_type == "provider" and name in str(static.render()):
                        found_provider = True
                    elif found_provider and item_type == "model":
                        self.dismiss(name)
                        return
                # Fallback: first model
                for item_type, name, _ in self._filtered:
                    if item_type == "model":
                        self.dismiss(name)
                        return
                return

            raw = str(static.render()).strip()
            model_name = raw.lstrip("* ").strip()
            if model_name:
                self.dismiss(model_name)
        except Exception as exc:
            logger.debug("Model picker select failed: %s", exc)
            # Fallback: try first model
            for item_type, name, _ in self._filtered:
                if item_type == "model":
                    self.dismiss(name)
                    return
            self.dismiss(None)

    def action_dismiss(self) -> None:
        self.dismiss(None)
