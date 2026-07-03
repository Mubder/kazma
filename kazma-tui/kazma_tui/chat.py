"""Chat panel — RichLog output + Input + ProgressBar."""

from __future__ import annotations

import logging
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, ProgressBar, RichLog

logger = logging.getLogger(__name__)

ROLE_HEX: dict[str, str] = {
    "user": "#e6edf3",
    "assistant": "#a855f7",
    "tool": "#f59e0b",
    "system": "#8b949e",
    "error": "#ef4444",
    "thinking": "#22d3ee",
}


class ChatPanel(Vertical):
    """Chat: RichLog + ProgressBar + Input."""

    DEFAULT_CSS = """
    ChatPanel { height: 1fr; border: solid $border; background: $surface; }
    ChatPanel > RichLog { height: 1fr; background: transparent; border: none; padding: 1 2; }
    ChatPanel > ProgressBar { height: 1; margin: 0 2; }
    ChatPanel > Input {
        dock: bottom; height: 3; margin: 1 2;
        background: $panel; border: solid $border; color: $text;
    }
    ChatPanel > Input:focus { border: solid $primary; }
    """

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True, auto_scroll=True)
        yield ProgressBar(id="chat-progress", total=100, show_eta=False)
        yield Input(placeholder="Type... /help for commands", id="chat-input")

    def write(self, role: str, text: str) -> None:
        log = self.query_one(RichLog)
        ts = datetime.now().strftime("%H:%M")
        c = ROLE_HEX.get(role, "#8b949e")
        label = role.upper()
        log.write(f"[dim]{ts}[/] [{c}]▌ {label}[/] {text}")

    def show_progress(self, v: bool) -> None:
        self.query_one(ProgressBar).display = v

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
            self.write("system", "/help /clear /model /quit — Ctrl+P palette")
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
            self.show_progress(True)
            registry = get_model_registry()
            provider = registry.get_client()
            response = await provider.chat([{"role": "user", "content": prompt}])
            self.show_progress(False)
            content = response.content if hasattr(response, "content") else str(response)
            self.write("assistant", content)
        except Exception as e:
            self.show_progress(False)
            self.write("error", f"Error: {e}")

    def action_copy_last(self) -> None:
        try:
            import pyperclip
            log = self.query_one(RichLog)
            for line in reversed(log.text.split("\n")):
                if "KAZMA" in line:
                    parts = line.split(" ", 3)
                    if len(parts) > 3:
                        pyperclip.copy(parts[3])
                    return
        except Exception:
            pass
