"""Chat panel — RichLog output with colored messages + Input with copy/paste."""

from __future__ import annotations

import logging
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, RichLog

logger = logging.getLogger(__name__)

# Color palette per role (Rich markup uses CSS color names or hex)
ROLE_COLOR = {
    "user": "#e6edf3",
    "assistant": "#a855f7",
    "tool": "#f59e0b",
    "system": "#8b949e",
    "error": "#ef4444",
    "thinking": "#22d3ee",
}


class ChatPanel(Vertical):
    """Chat: RichLog (markdown output) + Input at bottom."""

    DEFAULT_CSS = """
    ChatPanel {
        height: 1fr;
        border: solid $border;
        background: $surface;
    }
    ChatPanel > RichLog {
        height: 1fr;
        background: transparent;
        border: none;
        padding: 1 2;
    }
    ChatPanel > Input {
        dock: bottom;
        height: 3;
        margin: 1 2;
        background: $panel;
        border: solid $border;
        color: $text;
    }
    ChatPanel > Input:focus {
        border: solid $primary;
    }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True, auto_scroll=True)
        yield Input(placeholder="Type a message...  /help for commands", id="chat-input")

    # ── Message handling ─────────────────────────────────────────

    def write(self, role: str, text: str) -> None:
        """Write a color-coded message to the log."""
        log = self.query_one(RichLog)
        ts = datetime.now().strftime("%H:%M")
        color = ROLE_COLOR.get(role, "$text-disabled")
        label = role.upper()
        log.write(f"[dim]{ts}[/] [{color}]▌ {label}[/] {text}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.clear()
        if text.startswith("/"):
            self._handle_command(text)
        else:
            self.write("user", text)
            self.app.call_later(self._generate_response, text)

    def _handle_command(self, text: str) -> None:
        cmd = text.lower().split()[0]
        if cmd == "/help":
            self.write("system", "/help, /clear, /model, /quit — Ctrl+P for command palette")
        elif cmd == "/clear":
            self.query_one(RichLog).clear()
        elif cmd == "/quit":
            self.app.exit()
        elif cmd in ("/model", "/models"):
            try:
                from kazma_core.settings.model_registry import get_model_list_text
                self.write("system", get_model_list_text("tui"))
            except Exception as e:
                self.write("error", f"Model registry: {e}")
        else:
            self.write("system", f"Unknown: {cmd}")

    async def _generate_response(self, prompt: str) -> None:
        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            provider = registry.get_client()
            self.write("thinking", "Thinking...")
            response = await provider.chat([{"role": "user", "content": prompt}])
            content = response.content if hasattr(response, "content") else str(response)
            # Remove thinking line
            log = self.query_one(RichLog)
            log.write(f"[bold #22d3ee]▌ KAZMA[/] {content}")
        except Exception as e:
            self.write("error", f"Error: {e}")

    def action_copy_last(self) -> None:
        """Copy last assistant message to clipboard. RichLog text selection also works with mouse."""
        try:
            import pyperclip
            log = self.query_one(RichLog)
            lines = log.text.split("\n")
            for line in reversed(lines):
                if "KAZMA" in line or "Thinking" in line:
                    # Find the message body after the label
                    parts = line.split(" ", 2)
                    if len(parts) > 2:
                        pyperclip.copy(parts[2])
                    return
        except Exception:
            pass
