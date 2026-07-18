"""Kazma TUI — Professional terminal dashboard for the Kazma agent framework.

Architecture: Header · Tabs (Chat | Files | Swarm | Settings) · Footer

Features:
    - Enhanced visual design with premium styling
    - Vim-style keyboard navigation (j/k for scrolling)
    - Toast notifications for user feedback
    - Context-sensitive key bindings
    - Loading spinners for async operations
    - Live status bar with metrics
    - Adaptive refresh rates
    - Accessibility enhancements
"""

from __future__ import annotations

import os
import logging
import sys
from datetime import datetime
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, RichLog, TabbedContent, TabPane

from kazma_tui.chat import ChatPanel
from kazma_tui.dashboard import MetricsDashboard
from kazma_tui.files import FilesPanel
from kazma_tui.header import KazmaHeader
from kazma_tui.settings_panel import SettingsPanel
from kazma_tui.swarm import SwarmPanel
from kazma_tui.traces import TracesPanel
from kazma_tui.theme import KAZMA_THEME
from kazma_tui.widgets.accessibility import FocusManager, HighContrastMode
from kazma_tui.widgets.command_bar import CommandConsole
from kazma_tui.widgets.command_palette import CommandPalette
from kazma_tui.widgets.confirm_dialog import ConfirmDialog
from kazma_tui.widgets.status_bar import KazmaStatusBar
from kazma_tui.widgets.toast import Toast
from kazma_tui.widgets.tutorial import TutorialScreen
from kazma_tui.widgets.hitl_modal import HitlApprovalScreen

logger = logging.getLogger(__name__)


class KazmaTUI(App[None]):
    """Kazma Terminal Dashboard — kazma.ai Web UI theme."""

    TITLE = "Kazma"
    CSS = KAZMA_THEME

    BINDINGS = [
        # Core navigation
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+p", "command_palette", "Commands"),
        Binding("ctrl+shift+c", "copy_clipboard", "Copy"),
        Binding("ctrl+f", "focus_input", "Focus Chat"),
        # Vim-style navigation
        Binding("j", "scroll_down", "Scroll Down", show=False),
        Binding("k", "scroll_up", "Scroll Up", show=False),
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
        # Tab navigation
        Binding("ctrl+n", "next_tab", "Next Tab"),
        Binding("ctrl+b", "prev_tab", "Prev Tab"),
        # Help
        Binding("?", "help_screen", "Help", show=False),
        # Accessibility
        Binding("Tab", "focus_next", "Next Focus", show=False),
        Binding("shift+tab", "focus_previous", "Prev Focus", show=False),
        Binding("ctrl+h", "toggle_high_contrast", "High Contrast", show=False),
        Binding(":", "command_bar", "Console", show=False),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        from kazma_tui.themes.theme_manager import ThemeManager
        self.theme_manager = ThemeManager()
        self._focus_manager: Optional[FocusManager] = None
        self._high_contrast: Optional[HighContrastMode] = None
        self._status_bar: Optional[KazmaStatusBar] = None
        self._shown_approvals: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="dashboard", id="main-tabs"):
            with TabPane("Dashboard", id="dashboard"):
                yield MetricsDashboard()
            with TabPane("Chat", id="chat"):
                yield ChatPanel()
            with TabPane("Files", id="files"):
                yield FilesPanel()
            with TabPane("Traces", id="traces"):
                yield TracesPanel()
            with TabPane("Swarm", id="swarm"):
                yield SwarmPanel()
            with TabPane("Settings", id="settings"):
                yield SettingsPanel()
        yield KazmaStatusBar(id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        """Initialize app state, core singletons, and show welcome notification."""
        # Apply the saved theme and language preference on startup
        try:
            self.theme_manager.apply_theme(self)
            self.update_localization()
        except Exception as e:
            logger.exception(f"Error applying initial theme/localization: {e}")

        # ── Core initialization (ModelRegistry + SwarmEngine) ──────────
        # When the TUI is launched standalone (python -m kazma_tui), the
        # ModelRegistry and SwarmEngine singletons have not been created.
        # We initialize them here so chat, header, and swarm panel all work.
        self._initialize_core()

        # Initialize status bar reference
        try:
            self._status_bar = self.query_one("#status-bar", KazmaStatusBar)
        except Exception as e:
            logger.debug(f"Status bar not found: {e}")

        # Initialize focus manager
        self._focus_manager = FocusManager(
            [
                "chat-input",
                "chat-log",
                "worker-table",
                "file-tree",
                "trace-search",
                "trace-table",
                "settings-panel",
            ]
        )

        # Initialize high contrast mode
        self._high_contrast = HighContrastMode(self)

        # Check if first run - show tutorial
        try:
            from pathlib import Path

            config_dir = Path.home() / ".kazma"
            prefs_file = config_dir / "preferences.json"

            if not prefs_file.exists():
                # First run - show tutorial
                self.push_screen(TutorialScreen())
            # No tutorial for returning users - silent start
        except Exception as e:
            logger.exception(f"Error in on_mount: {e}")

        # Periodic background polling to check for pending HITL approvals (every 3.0s)
        self.set_interval(3.0, self._check_pending_approvals)

    def _initialize_core(self) -> None:
        """Initialize ModelRegistry and SwarmEngine if not already done.

        Mirrors the startup sequence from kazma_ui.app:create_app() but
        trimmed to the essentials the TUI needs: ConfigStore, ModelRegistry,
        and an empty SwarmEngine (loaded from kazma.yaml if present).
        """
        # ── ConfigStore ─────────────────────────────────────────────
        try:
            from kazma_core.config_store import ConfigStore, get_config_store, set_config_store
            import kazma_core.config_store as _cs_mod

            # get_config_store() lazily creates a singleton and never raises.
            # Check if the singleton is already set; if not, create one
            # and load values from kazma.yaml.
            # Always use the process-wide singleton (never ConfigStore() here).
            cs = get_config_store()
            try:
                cs.reconcile_from_yaml()
            except Exception:
                pass
            logger.info("[TUI] ConfigStore ready (singleton)")
        except Exception as e:
            logger.warning("[TUI] ConfigStore init failed: %s", e)

        # ── ModelRegistry ───────────────────────────────────────────
        try:
            from kazma_core.model_registry import get_model_registry, initialize_model_registry

            try:
                get_model_registry()
                # Already initialized
            except RuntimeError:
                from kazma_core.config_store import get_config_store
                initialize_model_registry(get_config_store())
                logger.info("[TUI] ModelRegistry initialized")
        except Exception as e:
            logger.warning("[TUI] ModelRegistry init failed: %s", e)

        # ── SwarmEngine ─────────────────────────────────────────────
        try:
            from kazma_core.swarm import get_swarm_engine, set_swarm_engine
            from kazma_core.swarm.config import SwarmConfig
            from kazma_core.swarm.engine import SwarmEngine
            from kazma_core.swarm.task_store import TaskStore

            if get_swarm_engine() is None:
                swarm_cfg = SwarmConfig.from_yaml("kazma.yaml")
                if swarm_cfg is not None and swarm_cfg.enabled:
                    task_store = TaskStore()
                    engine = SwarmEngine(swarm_cfg, task_store=task_store)
                    # Register workers from config
                    for wc in swarm_cfg.workers:
                        engine.add_worker(wc)
                else:
                    engine = SwarmEngine(
                        SwarmConfig(enabled=True, workers=[]),
                        task_store=TaskStore(),
                    )
                set_swarm_engine(engine)

                # Load persisted workers from WorkerRegistry (swarm_registry.json).
                # The Web UI saves workers here when users add them via the
                # Swarm panel; the TUI must read the same file to see them.
                try:
                    from kazma_core.swarm.registry import get_worker_registry
                    from kazma_core.swarm.config import WorkerConfig as _WC
                    reg = get_worker_registry()
                    for entry in reg.list_all():
                        if entry.name not in engine._workers:
                            engine.add_worker(_WC(
                                name=entry.name,
                                type=entry.worker_type or "in_process",
                                model=entry.model,
                                provider=entry.provider,
                                role=entry.roles[0] if entry.roles else "",
                                system_prompt=entry.system_prompt,
                            ))
                    logger.info(
                        "[TUI] SwarmEngine initialized — %d worker(s) from YAML + %d from registry",
                        len(swarm_cfg.workers) if swarm_cfg else 0,
                        len(engine._workers) - len(swarm_cfg.workers if swarm_cfg else []),
                    )
                except Exception as exc:
                    logger.warning("[TUI] Failed to load workers from registry: %s", exc)
        except Exception as e:
            logger.warning("[TUI] SwarmEngine init failed: %s", e)

        # ── Update status bar with active model info ──────────────────
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            profile = registry.get_active_profile()
            provider = profile.get("provider", "?")
            model = profile.get("model", "?")
            self._status_bar.set_model_info(provider, model)
        except Exception:
            pass  # status bar stays at defaults if registry unavailable

    def action_copy_clipboard(self) -> None:
        """Copy selected text or last KAZMA response to the system clipboard."""
        try:
            chat = self.query_one(ChatPanel)
            if chat.copy_to_clipboard():
                self.push_screen(Toast("Copied to clipboard", "success", duration=1.5))
            else:
                self.push_screen(Toast("Nothing to copy", "warning", duration=1.5))
        except Exception as exc:
            logger.debug("Copy to clipboard failed: %s", exc)

    def action_command_palette(self) -> None:
        """Show enhanced command palette with fuzzy search."""
        self.push_screen(CommandPalette())

    def action_command_bar(self) -> None:
        """Show Vim/Tmux-style Command Console."""
        self.push_screen(CommandConsole())

    def action_clear_chat(self) -> None:
        """Clear the chat log."""
        try:
            from kazma_tui.chat import ChatPanel

            chat = self.query_one(ChatPanel)

            # Show confirmation dialog
            def on_confirm(confirmed: bool) -> None:
                if confirmed:
                    chat.query_one(RichLog).clear()
                    self.push_screen(Toast("Chat cleared", "success", duration=1.5))

            dialog = ConfirmDialog(
                "Are you sure you want to clear the chat history?",
                title="Clear Chat",
                confirm_text="Clear",
            )
            self.push_screen(dialog, on_confirm)
        except Exception as e:
            logger.exception(f"Error clearing chat: {e}")

    def action_show_help(self) -> None:
        """Show help information."""
        self.action_help_screen()

    def action_list_models(self) -> None:
        """List available models."""
        try:
            from kazma_tui.chat import ChatPanel

            chat = self.query_one(ChatPanel)
            from kazma_core.settings.model_registry import get_model_list_text

            chat.write("system", get_model_list_text("tui"))
            self.push_screen(Toast("Model list displayed", "info", duration=2.0))
        except Exception as e:
            self.push_screen(Toast(f"Error listing models: {e}", "error", duration=3.0))

    def action_refresh_all(self) -> None:
        """Refresh all panels."""
        self.push_screen(Toast("Refreshing...", "info", duration=1.0))
        # Trigger refresh in active panel
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            current = tabs.active
            if current == "swarm":
                from kazma_tui.swarm import SwarmPanel

                swarm = self.query_one(SwarmPanel)
                if hasattr(swarm, "refresh_data"):
                    swarm.refresh_data()
        except Exception as exc:
            logger.debug("Refresh all panels failed: %s", exc)

    def action_focus_input(self) -> None:
        try:
            self.query_one("#chat-input").focus()
        except Exception as exc:
            logger.debug("Focus input failed: %s", exc)

    def action_scroll_down(self) -> None:
        """Scroll down in the focused widget."""
        focused = self.focused
        if focused:
            try:
                focused.scroll_relative(y=3)
            except Exception as exc:
                logger.debug("Scroll down failed: %s", exc)

    def action_scroll_up(self) -> None:
        """Scroll up in the focused widget."""
        focused = self.focused
        if focused:
            try:
                focused.scroll_relative(y=-3)
            except Exception as exc:
                logger.debug("Scroll up failed: %s", exc)

    def action_scroll_top(self) -> None:
        """Scroll to top of focused widget."""
        focused = self.focused
        if focused:
            try:
                focused.scroll_home()
            except Exception as exc:
                logger.debug("Scroll top failed: %s", exc)

    def action_scroll_bottom(self) -> None:
        """Scroll to bottom of focused widget."""
        focused = self.focused
        if focused:
            try:
                focused.scroll_end()
            except Exception as exc:
                logger.debug("Scroll bottom failed: %s", exc)

    def action_next_tab(self) -> None:
        """Switch to next tab."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            current = tabs.active
            tab_order = ["dashboard", "chat", "files", "traces", "swarm", "settings"]
            if current in tab_order:
                next_idx = (tab_order.index(current) + 1) % len(tab_order)
                tabs.active = tab_order[next_idx]
        except Exception as exc:
            logger.debug("Next tab switch failed: %s", exc)

    def action_prev_tab(self) -> None:
        """Switch to previous tab."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            current = tabs.active
            tab_order = ["dashboard", "chat", "files", "traces", "swarm", "settings"]
            if current in tab_order:
                prev_idx = (tab_order.index(current) - 1) % len(tab_order)
                tabs.active = tab_order[prev_idx]
        except Exception as exc:
            logger.debug("Prev tab switch failed: %s", exc)

    def action_help_screen(self) -> None:
        """Show contextual help based on current tab."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            current = tabs.active
            help_messages = {
                "dashboard": "Dashboard: Live resource usage trend lines and agent framework health.",
                "chat": "Chat: Type message, Ctrl+Enter send, /help commands",
                "files": "Files: Browse workspace files, Click to open",
                "traces": "Traces: Live log audit trail, filter by term, navigate with keys",
                "swarm": "Swarm: Monitor workers, View task history",
                "settings": "Settings: Configure model, provider, preferences",
            }
            msg = help_messages.get(current, "Press Ctrl+P for command palette")
            self.push_screen(Toast(msg, "info", duration=3.0))
        except Exception as exc:
            logger.debug("Help screen failed: %s", exc)

    def action_focus_next(self) -> None:
        """Focus next widget in order (accessibility)."""
        if self._focus_manager:
            self._focus_manager.focus_next(self)
        else:
            # Fallback: let Textual handle default focus
            pass

    def action_focus_previous(self) -> None:
        """Focus previous widget in order (accessibility)."""
        if self._focus_manager:
            self._focus_manager.focus_previous(self)
        else:
            # Fallback: let Textual handle default focus
            pass

    def action_toggle_high_contrast(self) -> None:
        """Toggle high contrast mode for accessibility."""
        if not self._high_contrast:
            return
        try:
            enabled = self._high_contrast.toggle()
        except Exception as exc:
            logger.exception("Failed to toggle high contrast mode")
            self.push_screen(Toast(f"High contrast toggle failed: {exc}", "error"))
            return
        mode = "enabled" if enabled else "disabled"
        self.push_screen(Toast(f"High contrast mode {mode}", "info", duration=2.0))

    def action_record_activity(self) -> None:
        """Record user activity (no-op — adaptive refresh removed)."""
        pass

    async def _check_pending_approvals(self) -> None:
        """Background checker for pending HITL approval tasks."""
        import httpx
        try:
            headers = {}
            secret = os.environ.get("KAZMA_SECRET", "")
            if secret:
                headers["X-Kazma-Secret"] = secret

            _port = os.environ.get("KAZMA_PORT", "8000")
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"http://127.0.0.1:{_port}/api/pending-approvals", headers=headers)
                if response.status_code == 200:
                    data = response.json()
                    pending_list = data.get("pending", [])
                    
                    for item in pending_list:
                        thread_id = item.get("thread_id")
                        if thread_id and thread_id not in self._shown_approvals:
                            # Don't stack multiple modals — skip if one is already open
                            if isinstance(self.screen, HitlApprovalScreen):
                                break
                            self._shown_approvals.add(thread_id)
                            tool_name = item.get("tool_name", "unknown")
                            arguments = item.get("arguments", {})
                            message = item.get("message", "")
                            
                            def handle_dismiss(approved: bool | None) -> None:
                                self.run_worker(self._submit_hitl_decision(thread_id, approved))
                            
                            screen = HitlApprovalScreen(
                                thread_id=thread_id,
                                tool_name=tool_name,
                                arguments=arguments,
                                message=message
                            )
                            self.push_screen(screen, handle_dismiss)
                            break  # Show one modal at a time
        except Exception as exc:
            logger.debug("Failed to check pending approvals: %s", exc)

    async def _submit_hitl_decision(self, thread_id: str, approved: bool | None) -> None:
        """Post the user's HITL decision back to the FastAPI backend."""
        import httpx
        from kazma_tui.widgets.toast import Toast

        decision = "approve" if approved else "deny"
        try:
            headers = {}
            secret = os.environ.get("KAZMA_SECRET", "")
            if secret:
                headers["X-Kazma-Secret"] = secret

            _port = os.environ.get("KAZMA_PORT", "8000")
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"http://127.0.0.1:{_port}/api/approve/{thread_id}",
                    json={"action": decision},
                    headers=headers,
                )
                if response.status_code in (200, 202):
                    self.push_screen(Toast(f"HITL task {decision}d successfully", "success"))
                else:
                    self.push_screen(Toast(f"Error submitting decision: {response.text}", "error"))
        except Exception as exc:
            logger.exception("Failed to submit HITL decision")
            self.push_screen(Toast(f"Failed to submit decision: {exc}", "error"))
        finally:
            if thread_id in self._shown_approvals:
                self._shown_approvals.remove(thread_id)

    def update_localization(self) -> None:
        """Apply dynamic translations and text mirroring based on preferred language."""
        lang = self.theme_manager.language
        
        # 1. Toggle the 'rtl-mode' class on the screen
        try:
            screen = self.screen
            if lang == "ar":
                screen.add_class("rtl-mode")
            else:
                screen.remove_class("rtl-mode")
        except Exception as exc:
            logger.debug("Failed to toggle rtl-mode class: %s", exc)

        # 2. Translate Tab labels dynamically
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            labels = {
                "en": {
                    "dashboard": "Dashboard",
                    "chat": "Chat",
                    "files": "Files",
                    "traces": "Traces",
                    "swarm": "Swarm",
                    "settings": "Settings"
                },
                "ar": {
                    "dashboard": "\u0644\u0648\u062d\u0629 \u0627\u0644\u0642\u064a\u0627\u062f\u0629",
                    "chat": "\u0627\u0644\u0645\u062d\u0627\u062f\u062b\u0629",
                    "files": "\u0627\u0644\u0645\u0644\u0641\u0627\u062a",
                    "traces": "\u0627\u0644\u062a\u062a\u0628\u0639\u0627\u062a",
                    "swarm": "\u0627\u0644\u0633\u0631\u0628",
                    "settings": "\u0627\u0644\u0625\u0639\u062f\u0627\u062f\u0627\u062a"
                }
            }
            for tab_id, label in labels[lang].items():
                try:
                    tab = tabs.tabs.get_tab(tab_id)
                    tab.label = label
                except Exception as tab_exc:
                    logger.debug("Failed to update tab label for %s: %s", tab_id, tab_exc)
        except Exception as exc:
            logger.debug("Failed to locate or update main-tabs: %s", exc)

        # 3. Update Header title (stock Textual Header)
        try:
            header = self.query_one(Header)
            if lang == "ar":
                header.add_class("header-title")
        except Exception as exc:
            logger.debug("Failed to update header localization: %s", exc)


def main() -> None:
    try:
        KazmaTUI().run()
    except Exception:
        logger.exception("Kazma TUI crashed")
        sys.exit(1)
