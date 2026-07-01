"""Chat interface widget for the Kazma TUI.

Uses TextArea for chat output (supports native mouse text selection)
and Input for message entry.  Ctrl+C copies selected text to clipboard.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Input, TextArea

logger = logging.getLogger(__name__)

_HELP_TEXT = """\
Available commands:
  /help   — Show this help message
  /clear  — Clear the chat log
  /quit   — Exit the application

Text selection: drag mouse to select text, then Ctrl+C to copy."""


class ChatPanel(Widget):
    """Chat interface — TextArea output (selectable) + Input field."""

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        border: solid $primary;
        border-title-align: center;
        border-title-color: $primary;
        border-title-background: $surface;
        border-title-style: bold;
        layout: vertical;
    }
    ChatPanel > TextArea {
        height: 1fr;
        background: transparent;
        border: none;
    }
    ChatPanel > Input {
        dock: bottom;
        height: 3;
        margin: 1;
        background: #18181b;
        border: solid #1e293b;
        color: #e2e8f0;
    }
    ChatPanel > Input:focus {
        border: solid $primary;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._chat_output: TextArea | None = None
        self._system_shown = False

    def compose(self) -> ComposeResult:
        self._chat_output = TextArea(
            text="Chat ready. Type /help for commands.\n",
            read_only=True,
            language=None,
            show_line_numbers=False,
            id="chat-output",
        )
        yield self._chat_output
        yield Input(placeholder="Type a message...", id="chat-input")

    def on_mount(self) -> None:
        self._focus_input()

    # ── Copy to clipboard ────────────────────────────────────────────

    def action_copy_selection(self) -> None:
        """Copy selected text from the chat output to clipboard."""
        try:
            output = self.query_one("#chat-output", TextArea)
            selected = output.selected_text
            if selected:
                import subprocess
                try:
                    proc = subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=selected, text=True, timeout=2,
                    )
                    if proc.returncode == 0:
                        self._add_system("Copied to clipboard")
                    else:
                        self._add_system(f"Copy failed (exit {proc.returncode})")
                except FileNotFoundError:
                    # Try pyperclip as fallback
                    try:
                        import pyperclip
                        pyperclip.copy(selected)
                        self._add_system("Copied to clipboard")
                    except ImportError:
                        self._add_system("xclip not installed. Run: sudo apt install xclip")
            else:
                self._add_system("No text selected. Drag mouse to select first.")
        except Exception:
            pass

    # ── Message handling ─────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self.add_message("You", text)
            self.app.call_later(self._generate_response, text)
        self._focus_input()

    async def _generate_response(self, prompt: str) -> None:
        """Generate AI response via ModelRegistry provider."""
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            provider = registry.get_client()
            if provider is None:
                self.add_message("System", "No provider configured. Set up a model in Settings.")
                return
            messages = [{"role": "user", "content": prompt}]
            response = await provider.chat(messages)
            output = response.content if hasattr(response, "content") else str(response)
            self.add_message("Assistant", output)
        except ImportError as exc:
            self.add_message("System", f"ModelRegistry not available: {exc}")
        except Exception as exc:
            self.add_message("System", f"Error: {exc}")

    def _handle_command(self, raw: str) -> None:
        cmd = raw.strip().lower()
        if cmd == "/help":
            self.add_message("System", _HELP_TEXT.strip())
        elif cmd == "/clear":
            try:
                output = self.query_one("#chat-output", TextArea)
                output.text = ""
            except Exception:
                pass
        elif cmd == "/quit":
            self.app.exit()
        elif cmd == "/model" or cmd == "/models":
            self._show_models()
        else:
            self.add_message("System", f"Unknown command: {raw.strip()}")

    def add_message(self, role: str, text: str) -> None:
        """Append a message to the chat output (plain text, no markup)."""
        try:
            output = self.query_one("#chat-output", TextArea)
            output.text += f"{role}: {text}\n"
            # Auto-scroll to bottom
            if hasattr(output, "move_cursor"):
                output.move_cursor(output.document.line_count - 1)
        except Exception:
            logger.debug("Chat output not yet mounted", exc_info=True)

    def _add_system(self, text: str) -> None:
        """Quick system message without cluttering history."""
        self.add_message("System", text)

    def _focus_input(self) -> None:
        try:
            self.query_one("#chat-input", Input).focus()
        except Exception:
            pass

    def _show_models(self) -> None:
        """Display available models from the Universal Model Registry."""
        try:
            from kazma_core.settings.model_registry import get_model_list_text
            text = get_model_list_text("tui")
            self.add_message("System", text)
        except Exception as exc:
            self.add_message("System", f"Failed to load models: {exc}")
