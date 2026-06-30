"""Chat interface widget for the Kazma TUI.

Uses RichLog with properly-escaped user text so ``[bold]`` and other
tags display literally.  System/Assistant messages can use Rich markup.
Includes Ctrl+Y to copy the last message to the clipboard.
"""

from __future__ import annotations

import logging
import subprocess

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Input, RichLog

logger = logging.getLogger(__name__)

_HELP_TEXT = """\
Available commands:
  /help   — Show this help message
  /clear  — Clear the chat log
  /quit   — Exit the application

Keyboard shortcuts:
  Enter   — Send message
  Ctrl+Y  — Copy last message to clipboard
  Ctrl+Q  — Quit
"""


class ChatPanel(Widget):
    """Chat interface — RichLog with escaped user input."""

    DEFAULT_CSS = """
    ChatPanel { height: 1fr; border: solid $primary; layout: vertical; }
    ChatPanel > RichLog { height: 1fr; }
    ChatPanel > Input { dock: bottom; height: 3; margin: 1; }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._last_raw: str = ""

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", wrap=True, highlight=True, markup=True)
        yield Input(placeholder="Type a message...", id="chat-input")

    def on_mount(self) -> None:
        self._focus_input()
        self.add_message("System", "[dim]Chat ready. Type /help for commands.[/dim]")

    # ── Copy to clipboard ────────────────────────────────────────────

    def action_copy_last(self) -> None:
        """Copy the last raw message to the system clipboard."""
        if not self._last_raw:
            return
        try:
            proc = subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=self._last_raw, text=True, timeout=2,
            )
            if proc.returncode == 0:
                self.add_message("System", "[dim]Copied to clipboard[/dim]")
            else:
                self.add_message("System", f"[red]Copy failed (exit {proc.returncode})[/red]")
        except FileNotFoundError:
            self.add_message("System", "[red]xclip not installed — run: sudo apt install xclip[/red]")
        except Exception:
            self.add_message("System", "[red]Copy failed[/red]")

    # ── Message handling ─────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        self._last_raw = text
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self.add_message("You", _escape_markup(text))
            self.app.call_later(self._generate_response, text)
        self._focus_input()

    def _generate_response(self, prompt: str) -> None:
        import inspect
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            if registry is None:
                self.add_message("System", "[dim]ModelRegistry not yet ready[/dim]")
                return
            agent = registry.get_agent()
            if agent is None:
                self.add_message("System", "[dim]No agent configured. Set up a model in kazma.yaml[/dim]")
                return
            response = agent.invoke(prompt)
            self.add_message("Assistant", response)
        except (RuntimeError, ImportError) as exc:
            self.add_message("System", f"[dim]Agent: {exc}[/dim]")
        except Exception as exc:
            self.add_message("System", f"[red]Error: {exc}[/red]")

    def _handle_command(self, raw: str) -> None:
        cmd = raw.strip().lower()
        if cmd == "/help":
            self.add_message("System", _HELP_TEXT.strip())
        elif cmd == "/clear":
            try:
                self.query_one("#chat-log", RichLog).clear()
            except Exception:
                pass
        elif cmd == "/quit":
            self.app.exit()
        else:
            self.add_message("System", f"Unknown command: {raw.strip()}")

    def add_message(self, role: str, text: str) -> None:
        """Write a message.  Role is styled; text may contain Rich markup."""
        try:
            log = self.query_one("#chat-log", RichLog)
            log.write(f"[bold $primary]{role}:[/bold $primary] {text}")
        except Exception:
            logger.debug("Chat log not yet mounted", exc_info=True)

    def _focus_input(self) -> None:
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass


def _escape_markup(text: str) -> str:
    """Escape Rich markup so user input displays literally."""
    import re
    return re.sub(r"(\[)", r"\\\1", text)
