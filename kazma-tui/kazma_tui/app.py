"""Kazma TUI — Professional terminal dashboard for the Kazma agent framework.

Architecture: Header · Tabs (Chat | Files | Swarm | Settings) · Footer

Features:
    - Enhanced visual design with premium styling
    - Vim-style keyboard navigation (j/k for scrolling)
    - Toast notifications for user feedback
    - Context-sensitive key bindings
    - Loading spinners for async operations
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime

from textual.app import App, ComposeResult
from textual.widgets import Footer, TabbedContent, TabPane
from textual.binding import Binding

from kazma_tui.chat import ChatPanel
from kazma_tui.files import FilesPanel
from kazma_tui.header import KazmaHeader
from kazma_tui.settings_panel import SettingsPanel
from kazma_tui.swarm import SwarmPanel
from kazma_tui.theme import KAZMA_THEME
from kazma_tui.widgets.toast import Toast

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
        Binding("ctrl+n", "next_tab", "Next Tab", show=False),
        Binding("ctrl+b", "prev_tab", "Prev Tab", show=False),
        # Help
        Binding("?", "help_screen", "Help", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield KazmaHeader()
        with TabbedContent(initial="chat", id="main-tabs"):
            with TabPane("Chat", id="chat"):
                yield ChatPanel()
            with TabPane("Files", id="files"):
                yield FilesPanel()
            with TabPane("Swarm", id="swarm"):
                yield SwarmPanel()
            with TabPane("Settings", id="settings"):
                yield SettingsPanel()
        yield KazmaFooter()

    def on_mount(self) -> None:
        """Initialize app state and show welcome notification."""
        self.push_screen(
            Toast("Welcome to Kazma TUI! Press ? for help", "info", duration=2.0)
        )

    def action_copy_clipboard(self) -> None:
        """Copy selected text or last KAZMA response to the system clipboard."""
        try:
            chat = self.query_one(ChatPanel)
            chat.copy_to_clipboard()
            self.push_screen(Toast("Copied to clipboard", "success", duration=1.5))
        except Exception:
            pass

    def action_command_palette(self) -> None:
        from kazma_tui.command_palette import CommandPalette
        self.push_screen(CommandPalette())

    def action_focus_input(self) -> None:
        try:
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    def action_scroll_down(self) -> None:
        """Scroll down in the focused widget."""
        focused = self.focused
        if focused:
            try:
                focused.scroll_relative(y=3)
            except Exception:
                pass

    def action_scroll_up(self) -> None:
        """Scroll up in the focused widget."""
        focused = self.focused
        if focused:
            try:
                focused.scroll_relative(y=-3)
            except Exception:
                pass

    def action_scroll_top(self) -> None:
        """Scroll to top of focused widget."""
        focused = self.focused
        if focused:
            try:
                focused.scroll_home()
            except Exception:
                pass

    def action_scroll_bottom(self) -> None:
        """Scroll to bottom of focused widget."""
        focused = self.focused
        if focused:
            try:
                focused.scroll_end()
            except Exception:
                pass

    def action_next_tab(self) -> None:
        """Switch to next tab."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            current = tabs.active
            tab_order = ["chat", "files", "swarm", "settings"]
            if current in tab_order:
                next_idx = (tab_order.index(current) + 1) % len(tab_order)
                tabs.active = tab_order[next_idx]
        except Exception:
            pass

    def action_prev_tab(self) -> None:
        """Switch to previous tab."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            current = tabs.active
            tab_order = ["chat", "files", "swarm", "settings"]
            if current in tab_order:
                prev_idx = (tab_order.index(current) - 1) % len(tab_order)
                tabs.active = tab_order[prev_idx]
        except Exception:
            pass

    def action_help_screen(self) -> None:
        """Show contextual help based on current tab."""
        try:
            tabs = self.query_one("#main-tabs", TabbedContent)
            current = tabs.active
            help_messages = {
                "chat": "Chat: Type message, Ctrl+Enter send, /help commands",
                "files": "Files: Browse workspace files, Click to open",
                "swarm": "Swarm: Monitor workers, View task history",
                "settings": "Settings: Configure model, provider, preferences",
            }
            msg = help_messages.get(current, "Press Ctrl+P for command palette")
            self.push_screen(Toast(msg, "info", duration=3.0))
        except Exception:
            pass


def main() -> None:
    try:
        KazmaTUI().run()
    except Exception:
        logger.exception("Kazma TUI crashed")
        sys.exit(1)
