"""Vim/Tmux-style Command Console overlay activated by `:`."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Input, Static

logger = logging.getLogger(__name__)


class CommandConsole(ModalScreen[None]):
    """Vim/Tmux-style command line interface overlay at the bottom of the screen."""

    DEFAULT_CSS = """
    CommandConsole {
        align: bottom center;
        background: transparent;
    }

    CommandConsole > #command-container {
        width: 100%;
        height: 1;
        background: $panel;
        layout: horizontal;
    }

    CommandConsole > #command-container > #command-prefix {
        width: 2;
        background: $primary;
        color: $surface;
        text-align: center;
        text-style: bold;
    }

    CommandConsole > #command-container > #command-input {
        width: 1fr;
        height: 1;
        border: none;
        padding: 0 1;
        background: transparent;
        color: $text;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="command-container"):
            yield Static(":", id="command-prefix")
            yield Input(placeholder="Type command (e.g. theme monokai, tab chat, help, q)...", id="command-input")

    def on_mount(self) -> None:
        """Auto-focus input when the console overlay is summoned."""
        self.query_one("#command-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Process and execute the submitted command."""
        command_text = event.value.strip()
        self.dismiss()  # Close the command line overlay first

        if not command_text:
            return

        # Split command and optional arguments
        parts = command_text.split(None, 1)
        cmd = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        # Dispatch commands
        try:
            self._execute_command(cmd, args)
        except Exception as e:
            logger.exception(f"Error executing command: {command_text}")
            self.app.push_screen(
                from_kazma_tui_toast(f"Command error: {e}", "error")
            )

    def _execute_command(self, cmd: str, args: str) -> None:
        """Map commands to App actions."""
        from kazma_tui.widgets.toast import Toast

        # Helper to push toast notifications safely
        def show_toast(msg: str, status: str = "info") -> None:
            self.app.push_screen(Toast(msg, status, duration=2.5))

        # 1. Quit/Exit
        if cmd in ("q", "quit", "exit"):
            self.app.exit()
            return

        # 2. Themes Live Switching
        if cmd == "theme":
            if not args:
                show_toast("Usage: :theme <kazma-dark|light|monokai|high-contrast>", "warning")
                return
            
            theme_name = args.lower()
            try:
                from kazma_tui.themes.theme_manager import ThemeManager
                tm = ThemeManager()
                available = tm.get_available_themes()
                if "kazma-dark" not in available:
                    available.append("kazma-dark")

                if theme_name not in available:
                    show_toast(f"Unknown theme. Available: {', '.join(available)}", "error")
                    return

                tm.apply_theme(self.app, theme_name)
                show_toast(f"Theme switched to [bold]{theme_name}[/]", "success")
            except Exception as exc:
                show_toast(f"Failed to apply theme: {exc}", "error")
            return

        # 3. Tab Switching
        if cmd == "tab":
            if not args:
                show_toast("Usage: :tab <dashboard|chat|files|traces|swarm|settings>", "warning")
                return

            tab_name = args.lower()
            tab_aliases = {
                "1": "dashboard", "db": "dashboard", "dashboard": "dashboard",
                "2": "chat", "chat": "chat",
                "3": "files", "files": "files", "file": "files",
                "4": "traces", "traces": "traces", "trace": "traces", "logs": "traces",
                "5": "swarm", "swarm": "swarm",
                "6": "settings", "settings": "settings", "set": "settings",
            }

            target_tab = tab_aliases.get(tab_name)
            if not target_tab:
                show_toast(f"Unknown tab: {tab_name}. Try: dashboard, chat, files, traces, swarm, settings", "error")
                return

            try:
                from textual.widgets import TabbedContent
                tabs = self.app.query_one("#main-tabs", TabbedContent)
                tabs.active = target_tab
                show_toast(f"Switched to [bold]{target_tab}[/] tab", "success")
            except Exception as exc:
                show_toast(f"Failed to switch tab: {exc}", "error")
            return

        # 4. Clear screen / chat logs
        if cmd == "clear":
            self.app.action_clear_chat()
            return

        # 5. Help / Information
        if cmd in ("h", "help", "?"):
            help_text = (
                "Commands:\n"
                "  :q, :quit         - Exit Kazma TUI\n"
                "  :theme <name>     - Set theme (light, monokai, kazma-dark, high-contrast)\n"
                "  :tab <name|idx>   - Switch tabs (1-6 or name)\n"
                "  :clear            - Clear active chat log\n"
                "  :toggle <setting> - Toggle high-contrast\n"
                "  :help, :h         - Show this guide"
            )
            from kazma_tui.chat import ChatPanel
            try:
                chat = self.app.query_one(ChatPanel)
                chat.write("system", help_text)
                show_toast("Help text written to Chat Log", "info")
            except Exception:
                show_toast("TUI Commands: :theme, :tab, :clear, :toggle, :quit", "info")
            return

        # 6. Toggle features
        if cmd == "toggle":
            if args == "high-contrast" or args == "hc":
                self.app.action_toggle_high_contrast()
            else:
                show_toast("Unknown toggle. Try: :toggle high-contrast", "warning")
            return

        show_toast(f"Unknown command: :{cmd}. Type :help for list.", "error")


def from_kazma_tui_toast(message: str, status: str) -> Any:
    """Helper to instantiate a Toast screen lazily."""
    from kazma_tui.widgets.toast import Toast
    return Toast(message, status, duration=2.5)
