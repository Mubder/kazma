"""Settings tab — SelectionList for feature toggles, persisted to ConfigStore.

Enhanced with theme switching and user preferences.
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll, Container
from textual.widgets import SelectionList, Static, Button, Label, Input, Select

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
        padding: 1 2;
        background: $panel;
        border: tall $border;
    }
    SettingsPanel .settings-title {
        color: $primary;
        text-style: bold;
        margin-bottom: 1;
    }
    SettingsPanel .settings-hint {
        color: $text-muted;
        margin-bottom: 1;
        height: auto;
    }
    SettingsPanel .section-label {
        color: $primary;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    SettingsPanel .theme-buttons {
        align: left middle;
        height: auto;
        padding: 1 0;
    }
    SettingsPanel .theme-buttons Button {
        margin: 0 1 0 0;
        min-width: 12;
    }
    """

    BINDINGS = [
        Binding("r", "refresh_settings", "Refresh"),
    ]

    SETTINGS = [
        ("Enable RAG memory", "memory.enabled", True),
        ("Per-turn memory retrieval", "memory.per_turn_retrieval", True),
        ("Auto-store durable facts", "memory.auto_store", True),
        ("Memory consolidator (librarian)", "memory.consolidation.enabled", True),
        ("Consolidator use LLM", "memory.consolidation.use_llm", True),
        ("V2 cognitive stack (use_new_stack)", "memory.v2.use_new_stack", False),
        ("V2 skip LLM if heuristic extracted", "memory.v2.skip_llm_if_heuristic_extracted", False),
        ("Enable auto-summarization", "context.auto_summarize", True),
        ("Enable cost breaker", "cost.breaker_enabled", True),
        ("Enable tracing", "tracing.enabled", False),
        ("Enable cron", "cron.enabled", False),
        ("HITL approval (danger tools)", "safety.hitl_enabled", True),
    ]

    # V2 numeric tunables: (label, configstore key, type, default)
    V2_NUMERIC = [
        ("Decay λ identity", "memory.v2.decay_lambda_identity", float, "0.0001"),
        ("Decay λ general", "memory.v2.decay_lambda_general", float, "0.01"),
        ("Decay λ ephemeral", "memory.v2.decay_lambda_ephemeral", float, "0.1"),
        ("Recall TTL (days)", "memory.v2.recall_ttl_days", int, "90"),
        ("Episodic TTL (days)", "memory.v2.episodic_ttl_days", int, "30"),
        ("Archive after (days)", "memory.v2.archive_after_days", int, "180"),
        ("LLM extraction every N turns", "memory.v2.extraction_every_n_turns", int, "1"),
        ("PPR alpha (restart)", "memory.v2.ppr_alpha", float, "0.15"),
        ("Entity merge threshold", "memory.v2.entity_vector_merge_threshold", float, "0.12"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_saved: dict[str, bool] = {}
        self._live_settings: dict[str, Any] | None = None
        self._settings_source = "local"

    def _fetch_live_settings(self) -> dict[str, Any]:
        """Flatten GET /api/settings (grouped by category) into dotted keys."""
        try:
            from kazma_core.runtime.local_api import request_json

            grouped = request_json("GET", "/api/settings", timeout=2.0)
        except Exception:
            logger.debug("TUI settings live GET failed", exc_info=True)
            self._settings_source = "local"
            return {}
        flat: dict[str, Any] = {}
        if isinstance(grouped, dict):
            for keys in grouped.values():
                if isinstance(keys, dict):
                    flat.update(keys)
        self._settings_source = "server" if flat else "local"
        return flat

    def _live_map(self) -> dict[str, Any]:
        if self._live_settings is None:
            self._live_settings = self._fetch_live_settings()
        return self._live_settings

    def _persist_setting(self, key: str, value: Any, category: str) -> str:
        """Write via the live server; fall back to this-process ConfigStore."""
        try:
            from kazma_core.runtime.local_api import request_json

            request_json(
                "PUT",
                "/api/settings/single",
                {"key": key, "value": value, "category": category},
                timeout=2.0,
            )
            if self._live_settings is not None:
                self._live_settings[key] = value
            self._settings_source = "server"
            return "server"
        except Exception:
            logger.debug("TUI settings live PUT failed", exc_info=True)
            from kazma_core.config_store import get_config_store

            get_config_store().set(key, value, category=category)
            return "local"

    @property
    def theme_manager(self) -> ThemeManager:
        if hasattr(self, "app") and hasattr(self.app, "theme_manager") and self.app.theme_manager:
            return self.app.theme_manager
        if not hasattr(self, "_theme_manager_fallback"):
            self._theme_manager_fallback = ThemeManager()
        return self._theme_manager_fallback

    def _read_config(self, key: str, default: bool) -> bool:
        live = self._live_map()
        if key in live:
            return bool(live[key])
        try:
            from kazma_core.config_store import get_config_store
            val = get_config_store().get(key)
            return bool(val) if val is not None else default
        except Exception:
            return default

    def _read_config_num(self, key: str, cast: type, default: str):
        """Read a numeric V2 tunable, falling back to the default string."""
        live = self._live_map()
        if key in live and live[key] is not None:
            try:
                return cast(live[key])
            except (TypeError, ValueError):
                pass
        try:
            from kazma_core.config_store import get_config_store
            val = get_config_store().get(key)
            if val is None:
                return cast(default)
            return cast(val)
        except Exception:
            return cast(default)

    def _v2_key_for_input(self, input_id: str) -> tuple[str, type] | None:
        """Resolve a v2-input widget id back to its (configstore key, cast)."""
        for _label, key, cast, _default in self.V2_NUMERIC:
            if input_id == "v2-input-" + key.replace(".", "-"):
                return key, cast
        return None

    def compose(self) -> ComposeResult:
        # Populate _last_saved from config before any change events fire
        for label, key, default in self.SETTINGS:
            self._last_saved[key] = self._read_config(key, default)

        yield Static("  SETTINGS  ·  features · memory · safety", classes="section-label")

        # Feature Toggles Section
        with Container(classes="settings-section"):
            yield Static("Feature & memory toggles", classes="settings-title")
            yield Static(
                "[dim]Toggles persist via PUT /api/settings/single (live server). "
                "If the server is unreachable they save in this TUI process only. "
                "Theme below stays local to this TUI.[/]",
                classes="settings-hint",
            )
            sel: SelectionList = SelectionList(id="settings-toggles")
            for label, key, default in self.SETTINGS:
                initial = self._read_config(key, default)
                sel.add_option((label, key, initial))
            yield sel

        # Preferences Section
        with Container(classes="settings-section"):
            yield Static("Preferences (this TUI only — not the Web theme)", classes="settings-title")
            yield Label(
                f"Auto-scroll: {'on' if self.theme_manager.auto_scroll else 'off'}",
                id="auto-scroll-label"
            )
            yield Label(
                f"Animations: {'on' if self.theme_manager.animations_enabled else 'off'}",
                id="animations-label"
            )

        # V2 Cognitive Engine numeric tunables
        with Container(classes="settings-section"):
            yield Static("V2 cognitive engine · tunables", classes="settings-title")
            yield Static(
                "[dim]Decay λ controls forgetting speed. TTLs control tier "
                "lifecycle. Edit + Enter to persist on the live server.[/]",
                classes="settings-hint",
            )
            for label, key, cast, default in self.V2_NUMERIC:
                val = self._read_config_num(key, cast, default)
                yield Label(label, markup=False)
                yield Input(
                    value=str(val),
                    id="v2-input-" + key.replace(".", "-"),
                    classes="v2-numeric-input",
                )
                # Stash the (key, cast) metadata on the widget for the handler
                # (Textual widgets support .meta-style attrs via a private dict)
            # Hidden status line for V2 health
            yield Label("", id="v2-health-line", markup=False)

        # Memory Isolation (tenant mode)
        with Container(classes="settings-section"):
            yield Static("Memory isolation · tenant mode", classes="settings-title")
            yield Static(
                "[dim]shared = all platforms share one pool (single-user). "
                "per_platform = each platform isolates. "
                "per_user = each sender/session fully isolated.[/]",
                classes="settings-hint",
            )
            _cur_mode = "shared"
            live = self._live_map()
            if "memory.tenant_mode" in live:
                _cur_mode = str(live.get("memory.tenant_mode") or "shared")
            else:
                try:
                    from kazma_core.config_store import get_config_store

                    _cur_mode = get_config_store().get("memory.tenant_mode", "shared") or "shared"
                except Exception:
                    _cur_mode = "shared"
            yield Select(
                options=[
                    ("Share everything (single-user)", "shared"),
                    ("Per platform (platform isolation)", "per_platform"),
                    ("Per user (full multi-tenant)", "per_user"),
                ],
                value=_cur_mode if _cur_mode in ("shared", "per_platform", "per_user") else "shared",
                id="tenant-mode-select",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        pass

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        sel = event.selection_list
        try:
            # Only persist keys whose value actually flipped — avoid the
            # round-trip per hover/move event that SelectedChanged fires on.
            changed = 0
            via = "server"
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
                    category = key.split(".", 1)[0] if "." in key else "general"
                    via = self._persist_setting(key, new_val, category)
                    self._last_saved[key] = new_val
                    changed += 1
            if changed:
                where = "live server" if via == "server" else "this TUI process (server unreachable)"
                self.notify(f"Settings saved ({changed}) on {where}", severity="information")
            if confirm_hitl_disable:
                self._confirm_hitl_disable(sel)
        except Exception as e:
            self.notify(f"Failed to save settings: {e}", severity="error")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Persist a V2 numeric tunable when the user presses Enter."""
        meta = self._v2_key_for_input(event.input.id)
        if meta is None:
            return  # not a V2 input
        key, cast = meta
        raw = (event.value or "").strip()
        try:
            val = cast(raw)
        except (TypeError, ValueError):
            self.notify(f"Invalid value for {key}: '{raw}'", severity="error")
            return
        try:
            via = self._persist_setting(key, val, "memory")
            where = "live server" if via == "server" else "this TUI process"
            self.notify(f"Saved {key} = {val} ({where})", severity="information")
        except Exception as e:
            self.notify(f"Failed to save {key}: {e}", severity="error")

    def on_select_changed(self, event: Select.Changed) -> None:
        """Handle the tenant-mode dropdown change."""
        if event.select.id != "tenant-mode-select":
            return
        try:
            val = event.value if event.value else "shared"
            via = self._persist_setting("memory.tenant_mode", val, "memory")
            where = "live server" if via == "server" else "this TUI process"
            self.notify(f"Memory isolation: {val} on {where} (next turn)")
        except Exception as e:
            self.notify(f"Failed to save tenant mode: {e}", severity="error")

    def _confirm_hitl_disable(self, sel: SelectionList) -> None:
        """Ask for explicit confirmation before disabling HITL approval."""

        def on_confirm(confirmed: bool) -> None:
            if confirmed:
                via = self._persist_setting(_HITL_KEY, False, "safety")
                self._last_saved[_HITL_KEY] = False
                where = "live server" if via == "server" else "this TUI process"
                self.notify(
                    f"HITL approval disabled on {where} — danger tools will run without approval",
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
        self._live_settings = None
        self.notify("Settings refreshed", severity="information")
