"""Interactive tutorial for first-time users."""

from __future__ import annotations

import logging
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ProgressBar, Static

logger = logging.getLogger(__name__)

__all__ = ["TutorialScreen"]


class TutorialScreen(ModalScreen[bool]):
    """Interactive tutorial for first-time Kazma TUI users.

    Features:
        - Step-by-step guided tour
        - Widget highlighting
        - Keyboard shortcut demonstrations
        - Skip/Complete options
    """

    STEPS = [
        {
            "title": "👋 Welcome to Kazma TUI",
            "message": (
                "Kazma TUI is a professional terminal dashboard for the Kazma agent framework.\n\n"
                "This quick tutorial will show you the key features and keyboard shortcuts.\n\n"
                "Use [bold]Next[/] to continue or [bold]Skip[/] to explore on your own."
            ),
            "highlight": None,
            "buttons": ["skip", "next"],
        },
        {
            "title": "💬 Chat Panel",
            "message": (
                "The [bold]Chat[/] panel is where you interact with AI agents.\n\n"
                "• Type messages and press [bold]Enter[/] to send\n"
                "• Use [bold]Ctrl+Enter[/] for multi-line messages\n"
                "• Type [bold]/help[/] for available commands\n"
                "• Press [bold]Ctrl+C[/] to copy responses"
            ),
            "highlight": "chat",
            "buttons": ["back", "next"],
        },
        {
            "title": "📁 Files Panel",
            "message": (
                "Browse and manage workspace files in the [bold]Files[/] panel.\n\n"
                "• Click files to view contents\n"
                "• Navigate directories with breadcrumbs\n"
                "• Search files with [bold]Ctrl+F[/]"
            ),
            "highlight": "files",
            "buttons": ["back", "next"],
        },
        {
            "title": "🐝 Swarm Monitoring",
            "message": (
                "Monitor your agent swarm in real-time.\n\n"
                "• View active workers and their status\n"
                "• Track task progress and history\n"
                "• See resource usage metrics\n"
                "• Refresh with [bold]F5[/] or [bold]r[/]"
            ),
            "highlight": "swarm",
            "buttons": ["back", "next"],
        },
        {
            "title": "⚙️ Settings & Preferences",
            "message": (
                "Customize Kazma to your workflow.\n\n"
                "• Switch between [bold]4 themes[/]\n"
                "• Configure model and provider\n"
                "• Toggle auto-scroll and animations\n"
                "• Preferences saved automatically"
            ),
            "highlight": "settings",
            "buttons": ["back", "next"],
        },
        {
            "title": "⌨️ Keyboard Navigation",
            "message": (
                "Master these essential shortcuts:\n\n"
                "[bold]j/k[/]       Scroll down/up\n"
                "[bold]g/G[/]       Go to top/bottom\n"
                "[bold]Ctrl+N/B[/]  Next/Previous tab\n"
                "[bold]Ctrl+P[/]    Command palette\n"
                "[bold]?[/]         Contextual help\n"
                "[bold]Ctrl+Q[/]    Quit"
            ),
            "highlight": None,
            "buttons": ["back", "next"],
        },
        {
            "title": "🎉 You're Ready!",
            "message": (
                "You've completed the Kazma TUI tutorial!\n\n"
                "Tips for getting started:\n"
                "• Press [bold]Ctrl+P[/] anytime for the command palette\n"
                "• Use [bold]?[/] for context-sensitive help\n"
                "• Check [bold]Settings[/] to customize your experience\n\n"
                "Happy coding with Kazma! 🚀"
            ),
            "highlight": None,
            "buttons": ["finish"],
        },
    ]

    DEFAULT_CSS = """
    TutorialScreen {
        align: center middle;
    }
    TutorialScreen > Container {
        width: 65%;
        max-width: 75;
        max-height: 70%;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
    }
    TutorialScreen .tutorial-header {
        text-align: center;
        text-style: bold;
        color: $primary;
        padding: 1 0;
        margin-bottom: 1;
        border-bottom: solid $border;
    }
    TutorialScreen .step-indicator {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
        height: 1;
    }
    TutorialScreen .tutorial-content {
        height: 1fr;
        padding: 1 2;
        background: $surface;
        border: solid $border;
        overflow-y: auto;
    }
    TutorialScreen .tutorial-content Label {
        width: 100%;
    }
    TutorialScreen Horizontal {
        align: center middle;
        margin-top: 1;
        height: 3;
    }
    TutorialScreen Button {
        min-width: 15;
        margin: 0 1;
    }
    TutorialScreen Button.hidden {
        display: none;
    }
    TutorialScreen ProgressBar {
        margin-top: 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.current_step = 0
        self.completed = False

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("📚 Kazma TUI Tutorial", classes="tutorial-header")
            yield Static(
                f"Step {self.current_step + 1} of {len(self.STEPS)}",
                id="step-indicator",
                classes="step-indicator",
            )
            yield ProgressBar(
                total=len(self.STEPS),
                show_eta=False,
                id="tutorial-progress",
            )
            with Vertical(classes="tutorial-content"):
                yield Label("", id="tutorial-message")
            # Always create all buttons, but hide/show as needed
            with Horizontal(id="button-container"):
                yield Button("← Back", variant="default", id="btn-back")
                yield Button("Skip Tutorial", variant="warning", id="btn-skip")
                yield Button("Next →", variant="primary", id="btn-next")
                yield Button("Get Started! 🚀", variant="success", id="btn-finish")

    def _create_buttons(self):
        """Create buttons for current step."""
        step = self.STEPS[self.current_step]
        buttons = []

        if "back" in step["buttons"]:
            buttons.append(Button("← Back", variant="default", id="btn-back"))
        if "skip" in step["buttons"]:
            buttons.append(Button("Skip Tutorial", variant="warning", id="btn-skip"))
        if "next" in step["buttons"]:
            buttons.append(Button("Next →", variant="primary", id="btn-next"))
        if "finish" in step["buttons"]:
            buttons.append(Button("Get Started! 🚀", variant="success", id="btn-finish"))

        return buttons

    def on_mount(self) -> None:
        """Initialize tutorial state."""
        step = self.STEPS[self.current_step]
        try:
            self.query_one("#tutorial-message", Label).update(f"[bold]{step['title']}[/]\n\n{step['message']}")
            # Set initial button visibility - hide back/finish, show skip/next
            self.query_one("#btn-back", Button).set_class(True, "hidden")
            self.query_one("#btn-skip", Button).set_class(False, "hidden")
            self.query_one("#btn-next", Button).set_class(False, "hidden")
            self.query_one("#btn-finish", Button).set_class(True, "hidden")
            self.query_one("#btn-next", Button).focus()
        except Exception as exc:
            logger.debug("Tutorial button update failed: %s", exc)

    def _update_step(self) -> None:
        """Update UI for current step."""
        step = self.STEPS[self.current_step]

        try:
            self.query_one("#step-indicator", Static).update(f"Step {self.current_step + 1} of {len(self.STEPS)}")
            self.query_one("#tutorial-progress", ProgressBar).update(progress=self.current_step + 1)

            # Update content
            content = f"[bold]{step['title']}[/]\n\n{step['message']}"
            self.query_one("#tutorial-message", Label).update(content)

            # Update button visibility
            buttons = step["buttons"]
            self.query_one("#btn-back", Button).set_class("back" not in buttons, "hidden")
            self.query_one("#btn-skip", Button).set_class("skip" not in buttons, "hidden")
            self.query_one("#btn-next", Button).set_class("next" not in buttons, "hidden")
            self.query_one("#btn-finish", Button).set_class("finish" not in buttons, "hidden")

            # Focus first visible button
            for btn_id in ["btn-back", "btn-skip", "btn-next", "btn-finish"]:
                btn = self.query_one(f"#{btn_id}", Button)
                if btn and not btn.has_class("hidden"):
                    btn.focus()
                    break

        except Exception as e:
            import logging

            logging.debug(f"Tutorial step update error: {e}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        btn_id = event.button.id

        if btn_id == "btn-back":
            if self.current_step > 0:
                self.current_step -= 1
                self._update_step()

        elif btn_id == "btn-next":
            if self.current_step < len(self.STEPS) - 1:
                self.current_step += 1
                self._update_step()

        elif btn_id == "btn-skip":
            self._complete_tutorial(False)

        elif btn_id == "btn-finish":
            self._complete_tutorial(True)

    def _complete_tutorial(self, completed: bool) -> None:
        """Handle tutorial completion."""
        try:
            import json
            from pathlib import Path

            config_dir = Path.home() / ".kazma"
            prefs_file = config_dir / "preferences.json"
            config_dir.mkdir(exist_ok=True)

            # Read existing preferences to preserve user customizations
            existing = {}
            if prefs_file.exists():
                try:
                    existing = json.loads(prefs_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    existing = {}

            # Merge tutorial completion into existing preferences
            existing["tutorial_completed"] = True
            if "theme" not in existing:
                existing["theme"] = "kazma-dark"

            prefs_file.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug("Tutorial prefs save failed: %s", exc)
        # Use call_next to schedule dismiss after current message is processed
        self.call_next(self._do_dismiss, completed)

    def _do_dismiss(self, completed: bool) -> None:
        """Actually dismiss the screen (called via call_next)."""
        try:
            self.dismiss(completed)
        except Exception as exc:
            logger.debug("Tutorial dismiss failed: %s", exc)

    def key_escape(self) -> None:
        """Allow escape to skip."""
        self._complete_tutorial(False)

    def key_enter(self) -> None:
        """Enter advances to next step or completes."""
        if self.current_step < len(self.STEPS) - 1:
            self.current_step += 1
            self._update_step()
        else:
            self._complete_tutorial(True)

    def key_n(self) -> None:
        """'n' for next."""
        if self.current_step < len(self.STEPS) - 1:
            self.current_step += 1
            self._update_step()

    def key_b(self) -> None:
        """'b' for back."""
        if self.current_step > 0:
            self.current_step -= 1
            self._update_step()
