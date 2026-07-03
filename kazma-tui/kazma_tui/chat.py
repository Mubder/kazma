"""Chat panel — RichLog + ProgressBar + Input + token-by-token streaming."""

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
    """Chat: RichLog + ProgressBar + Input. Supports token-by-token streaming."""

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

    # ── Message display ────────────────────────────────────────────

    def write(self, role: str, text: str) -> None:
        log = self.query_one(RichLog)
        ts = datetime.now().strftime("%H:%M")
        c = ROLE_HEX.get(role, "#8b949e")
        log.write(f"[dim]{ts}[/] [{c}]▌ {role.upper()}[/] {text}")

    def show_progress(self, visible: bool) -> None:
        bar = self.query_one(ProgressBar)
        bar.display = visible
        if visible:
            bar.update(progress=0)
            self._pulse_timer = self.set_interval(0.3, self._pulse_progress)

    def _pulse_progress(self) -> None:
        bar = self.query_one(ProgressBar)
        if bar.display:
            bar.advance(5)
            if bar.progress >= 100:
                bar.update(progress=0)

    # ── Streaming ──────────────────────────────────────────────────

    async def write_stream(self, prompt: str) -> None:
        """Stream tokens from provider, writing each chunk to RichLog."""
        log = self.query_one(RichLog)
        ts = datetime.now().strftime("%H:%M")
        log.write(f"[dim]{ts}[/] [#a855f7]▌ KAZMA[/] ")
        self.show_progress(True)

        try:
            from kazma_core.model_registry import get_model_registry

            registry = get_model_registry()
            provider = registry.get_client()
            messages = [{"role": "user", "content": prompt}]

            # True token-by-token streaming via provider.chat(stream=True)
            response = await provider.chat(messages, stream=True)
            async for chunk in response:
                delta = getattr(chunk, "content", None) or ""
                if delta:
                    log.write(delta)
        except Exception as e:
            log.write(f"\n[#ef4444]Error: {e}[/]")
        finally:
            self.show_progress(False)

    # ── Input handling ─────────────────────────────────────────────

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
        await self.write_stream(prompt)

    # ── Copy ───────────────────────────────────────────────────────

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
