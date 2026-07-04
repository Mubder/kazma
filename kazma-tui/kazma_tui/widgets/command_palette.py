"""Enhanced command palette with fuzzy search and command categories."""

from __future__ import annotations

import re
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, ListItem, ListView, Static, Label, TabbedContent, RichLog
from textual.binding import Binding


class FuzzyMatcher:
    """Simple fuzzy matching algorithm for command search."""

    @staticmethod
    def score(query: str, text: str) -> int:
        """Return match score (higher = better match)."""
        if not query:
            return 0
        
        query = query.lower()
        text = text.lower()
        
        # Exact match gets highest score
        if query == text:
            return 100
        
        # Starts with query
        if text.startswith(query):
            return 90
        
        # Contains query as substring
        if query in text:
            return 80
        
        # Fuzzy match - all chars present in order
        text_idx = 0
        matches = 0
        for char in query:
            idx = text.find(char, text_idx)
            if idx == -1:
                break
            matches += 1
            text_idx = idx + 1
        
        if matches == len(query):
            return 50
        
        return 0

    @staticmethod
    def highlight_match(text: str, query: str) -> str:
        """Add [bold] tags around matched characters."""
        if not query:
            return text
        
        query = query.lower()
        result = []
        text_idx = 0
        in_match = False
        
        for char in text:
            if text_idx < len(query) and char.lower() == query[text_idx]:
                if not in_match:
                    result.append("[bold $primary]")
                    in_match = True
                result.append(char)
                text_idx += 1
            else:
                if in_match:
                    result.append("[/]")
                    in_match = False
                result.append(char)
        
        if in_match:
            result.append("[/]")
        
        return "".join(result)


class CommandPalette(ModalScreen[str | None]):
    """Enhanced modal overlay with fuzzy-searchable categorized command list.
    
    Features:
        - Fuzzy search with scoring and highlighting
        - Command categorization (Navigation, Chat, System, etc.)
        - Keyboard shortcuts display
        - Recent commands tracking
        - Context-aware command filtering
    """

    BINDINGS = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("escape", "dismiss", "Close", show=False),
        Binding("ctrl+k", "delete_prev_word", "Del Word", show=False),
    ]

    # Categorized commands with icons
    COMMANDS = {
        "Navigation": [
            ("tab:chat", "💬 Switch to Chat tab", "Ctrl+1"),
            ("tab:files", "📁 Switch to Files tab", "Ctrl+2"),
            ("tab:swarm", "🐝 Switch to Swarm tab", "Ctrl+3"),
            ("tab:settings", "⚙️ Switch to Settings", "Ctrl+4"),
            ("next-tab", "➡️ Next tab", "Ctrl+N"),
            ("prev-tab", "⬅️ Previous tab", "Ctrl+B"),
        ],
        "Chat": [
            ("/clear", "🗑️ Clear chat history", None),
            ("/help", "❓ Show help", None),
            ("/model", "🤖 List models", None),
            ("/export", "📤 Export conversation", None),
            ("send-message", "📮 Send message", "Ctrl+Enter"),
        ],
        "View": [
            ("toggle-sidebar", "📊 Toggle sidebar", "Ctrl+B"),
            ("zoom-in", "🔍 Zoom in", "Ctrl++"),
            ("zoom-out", "🔎 Zoom out", "Ctrl+-"),
            ("refresh", "🔄 Refresh", "F5"),
        ],
        "System": [
            ("settings:theme", "🎨 Change theme", None),
            ("settings:model", "⚙️ Configure model", None),
            ("copy-selection", "📋 Copy selection", "Ctrl+C"),
            ("copy-all", "📄 Copy all", "Ctrl+Shift+C"),
            ("quit", "🚪 Exit Kazma", "Ctrl+Q"),
        ],
    }

    DEFAULT_CSS = """
    CommandPalette {
        align: center middle;
    }
    CommandPalette > Container {
        width: 60%;
        max-width: 70;
        max-height: 60%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    CommandPalette .palette-header {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
        padding: 1 0;
        border-bottom: solid $border;
    }
    CommandPalette Input {
        width: 100%;
        margin-bottom: 1;
        background: $boost;
        border: solid $border;
        padding: 0 1;
    }
    CommandPalette .search-info {
        text-align: right;
        color: $text-muted;
        margin-bottom: 1;
        height: 1;
    }
    CommandPalette ListView {
        height: 1fr;
        background: transparent;
        border: solid $border;
    }
    CommandPalette ListItem {
        padding: 0 1;
        color: $text-muted;
        height: auto;
    }
    CommandPalette ListItem.-highlight {
        background: $primary 20%;
        color: $text;
    }
    CommandPalette .command-category {
        text-style: bold;
        color: $primary;
        padding: 1 1 0 1;
        background: $surface;
    }
    CommandPalette .command-name {
        color: $text;
    }
    CommandPalette .command-desc {
        color: $text-muted;
        padding-left: 1;
    }
    CommandPalette .command-shortcut {
        color: $primary;
        text-align: right;
        padding-right: 1;
    }
    """

    def __init__(self, context: str | None = None) -> None:
        super().__init__()
        self.context = context  # Current tab/panel context
        self.matcher = FuzzyMatcher()
        self._recent_commands: list[str] = []
        self._all_commands: list[tuple[str, str, str | None, str]] = []
        self._build_command_list()

    def _build_command_list(self) -> None:
        """Flatten categorized commands into searchable list."""
        self._all_commands = []
        for category, cmds in self.COMMANDS.items():
            for cmd_id, desc, shortcut in cmds:
                searchable_text = f"{category} {cmd_id} {desc}"
                self._all_commands.append((cmd_id, desc, shortcut, searchable_text))

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("🔍 Command Palette", classes="palette-header")
            yield Input(placeholder="Type to search commands...", id="palette-search")
            yield Static("", id="search-info", classes="search-info")
            items: list[ListItem] = []
            for cmd_id, desc, shortcut, _ in self._all_commands:
                item = self._create_list_item(cmd_id, desc, shortcut)
                items.append(item)
            yield ListView(*items, id="palette-list")

    def _create_list_item(self, cmd_id: str, desc: str, shortcut: str | None) -> ListItem:
        """Create a formatted list item with command details."""
        content = f"  {cmd_id:<20} {desc}"
        if shortcut:
            content += f"  [{shortcut:>10}]"
        item = ListItem(Static(content))
        item._cmd_id = cmd_id
        item._cmd_desc = desc
        return item

    def on_mount(self) -> None:
        """Focus the search input on mount."""
        self.query_one(Input).focus()
        self._update_search_info("")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter commands based on search query with fuzzy matching."""
        query = event.value.strip()
        lst = self.query_one(ListView)
        lst.clear()
        
        if not query:
            # Show all commands when no query
            for cmd_id, desc, shortcut, _ in self._all_commands:
                item = self._create_list_item(cmd_id, desc, shortcut)
                lst.append(item)
        else:
            # Score and sort commands
            scored = []
            for cmd_id, desc, shortcut, search_text in self._all_commands:
                score = self.matcher.score(query, search_text)
                if score > 0:
                    scored.append((score, cmd_id, desc, shortcut))
            
            # Sort by score descending
            scored.sort(key=lambda x: -x[0])
            
            for score, cmd_id, desc, shortcut in scored:
                item = self._create_list_item(cmd_id, desc, shortcut)
                lst.append(item)
        
        self._update_search_info(query, len(lst))

    def _update_search_info(self, query: str, count: int | None = None) -> None:
        """Update search info label with match count."""
        try:
            info = self.query_one("#search-info", Static)
            if query:
                total = len(self._all_commands)
                info.update(f"{count or 0}/{total} matches • ESC to close")
            else:
                info.update(f"{len(self._all_commands)} commands • Type to filter")
        except Exception:
            pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Handle command selection."""
        if event.item is None:
            return
        
        cmd_id = getattr(event.item, "_cmd_id", "")
        if not cmd_id:
            return
        
        # Track recent command
        if cmd_id not in self._recent_commands:
            self._recent_commands.insert(0, cmd_id)
            self._recent_commands = self._recent_commands[:10]
        
        self._execute_command(cmd_id)

    def _execute_command(self, cmd_id: str) -> None:
        """Route and execute the selected command."""
        # Tab switches
        if cmd_id.startswith("tab:"):
            tab_map = {
                "tab:chat": "chat",
                "tab:files": "files",
                "tab:swarm": "swarm",
                "tab:settings": "settings",
            }
            self._switch_tab(tab_map.get(cmd_id, "chat"))
            self.dismiss(cmd_id)
            return
        
        # Navigation commands
        if cmd_id == "next-tab":
            self._switch_tab("chat")  # will cycle via app.action_next_tab
            try:
                self.app.action_next_tab()
            except Exception:
                pass
            self.dismiss(cmd_id)
            return
        
        if cmd_id == "prev-tab":
            try:
                self.app.action_prev_tab()
            except Exception:
                pass
            self.dismiss(cmd_id)
            return
        
        # Built-in actions
        action_map = {
            "/clear": "clear_chat",
            "/help": "show_help",
            "/model": "list_models",
            "quit": "quit",
            "copy-selection": "copy_clipboard",
            "refresh": "refresh_all",
        }
        
        if cmd_id in action_map:
            action_name = action_map[cmd_id]
            try:
                action_method = getattr(self.app, f"action_{action_name}", None)
                if action_method:
                    action_method()
            except Exception:
                pass
            self.dismiss(cmd_id)
            return
        
        # Pass command to chat panel
        if cmd_id.startswith("/"):
            self._route_to_chat(cmd_id)
            self.dismiss(cmd_id)
            return
        
        self.dismiss(cmd_id)

    def _switch_tab(self, tab_id: str) -> None:
        """Switch to specified tab."""
        try:
            tabs = self.app.query_one(TabbedContent)
            tabs.active = tab_id
        except Exception:
            pass

    def _route_to_chat(self, cmd: str) -> None:
        """Send slash command to chat panel."""
        try:
            from kazma_tui.chat import ChatPanel
            chat = self.app.query_one(ChatPanel)
            
            if cmd == "/clear":
                try:
                    self.app.query_one("#chat-log", RichLog).clear()
                except Exception:
                    pass
            elif cmd == "/help":
                chat.write("system", "Commands: /clear, /help, /model, /export")
            elif cmd == "/model":
                try:
                    from kazma_core.settings.model_registry import get_model_list_text
                    chat.write("system", get_model_list_text("tui"))
                except Exception:
                    chat.write("error", "Model registry unavailable")
        except Exception:
            pass

    def key_escape(self) -> None:
        """Dismiss on escape."""
        try:
            self.dismiss(None)
        except Exception:
            pass

    def action_cursor_up(self) -> None:
        """Move cursor up in list."""
        try:
            lst = self.query_one(ListView)
            lst.action_cursor_up()
        except Exception:
            pass

    def action_cursor_down(self) -> None:
        """Move cursor down in list."""
        try:
            lst = self.query_one(ListView)
            lst.action_cursor_down()
        except Exception:
            pass

    def action_select(self) -> None:
        """Select highlighted item."""
        try:
            lst = self.query_one(ListView)
            child = lst.highlighted_child
            if child is not None:
                self.on_list_view_selected(
                    ListView.Selected(lst, child, lst.index)
                )
        except Exception:
            pass

    def action_delete_prev_word(self) -> None:
        """Delete previous word in search input."""
        try:
            input_widget = self.query_one(Input)
            words = input_widget.value.rsplit(" ", 1)
            if len(words) > 1:
                input_widget.value = words[0]
            else:
                input_widget.value = ""
        except Exception:
            pass
