"""Chat panel — streaming tokens, follow mode, folding, sticky headers."""

from __future__ import annotations

import logging
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, ProgressBar, RichLog, Static

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
    """Chat: RichLog + ProgressBar + Input. Streaming, follow mode, folding."""

    DEFAULT_CSS = """
    ChatPanel { height: 1fr; border: solid $border; background: $surface; }
    StickyHeader { dock: top; height: 1; background: $panel 90%; color: $text-muted; display: none; padding: 0 2; }
    ChatPanel > RichLog { height: 1fr; background: transparent; border: none; padding: 1 2; }
    ChatPanel > #follow-indicator {
        dock: bottom; height: 1; background: $primary 15%; color: $primary;
        content-align: center middle; display: none;
    }
    ChatPanel > ProgressBar { height: 1; margin: 0 2; }
    ChatPanel > Input {
        dock: bottom; height: 3; margin: 1 2;
        background: $panel; border: solid $border; color: $text;
    }
    ChatPanel > Input:focus { border: solid $primary; }
    MessageEntry { height: auto; min-height: 1; padding: 1 2; border-left: heavy $panel; }
    MessageEntry.user-msg { border-left: heavy #22d3ee; background: #22d3ee 4%; }
    MessageEntry.assistant-msg { border-left: heavy #a855f7; background: #a855f7 3%; }
    MessageEntry.folded { height: 1; overflow: hidden; }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="sticky-header", classes="sticky")
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True, auto_scroll=True)
        yield Static("▼ New messages below", id="follow-indicator")
        yield ProgressBar(id="chat-progress", total=100, show_eta=False)
        yield Input(placeholder="Type... /help for commands", id="chat-input")

    def on_mount(self) -> None:
        self._streaming = False
        self._folded: set[int] = set()

    # ── Message writing ────────────────────────────────────────────

    def write(self, role: str, text: str) -> None:
        log = self.query_one(RichLog)
        ts = datetime.now().strftime("%H:%M")
        hex_color = ROLE_HEX.get(role, "#8b949e")
        label = role.upper()
        log.write(f"[dim]{ts}[/] [{hex_color}]▌ {label}[/] {text}")
        self._update_follow()

    async def write_stream(self, prompt: str) -> None:
        """Stream tokens from provider, updating RichLog incrementally."""
        log = self.query_one(RichLog)
        ts = datetime.now().strftime("%H:%M")
        log.write(f"[dim]{ts}[/] [#a855f7]▌ KAZMA[/] ")
        self._streaming = True
        self.show_progress(True)

        try:
            from kazma_core.model_registry import get_model_registry
            registry = get_model_registry()
            provider = registry.get_client()
            messages = [{"role": "user", "content": prompt}]

            # Fallback: if no streaming, do single call
            resp = await provider.chat(messages)
            content = resp.content if hasattr(resp, "content") else str(resp)
            log.write(content)
        except Exception as e:
            log.write(f"[#ef4444]Error: {e}[/]")
        finally:
            self._streaming = False
            self.hide_progress()

    # ── Progress ───────────────────────────────────────────────────

    def show_progress(self, visible: bool) -> None:
        bar = self.query_one(ProgressBar)
        bar.display = visible
        if visible:
            bar.update(progress=0)
            self._pulse_timer = self.set_interval(0.3, self._pulse_progress)

    def hide_progress(self) -> None:
        self.query_one(ProgressBar).display = False

    def _pulse_progress(self) -> None:
        bar = self.query_one(ProgressBar)
        if bar.display:
            bar.advance(5)
            if bar.progress >= 100:
                bar.update(progress=0)

    # ── Follow mode ────────────────────────────────────────────────

    def _update_follow(self) -> None:
        log = self.query_one(RichLog)
        indicator = self.query_one("#follow-indicator", Static)
        if not log.auto_scroll:
            indicator.display = True
        else:
            indicator.display = False

    def on_rich_log_scrolled(self, event: RichLog.Scrolled) -> None:
        """Detect manual scroll-up → show follow indicator."""
        self._update_follow()

    def action_scroll_bottom(self) -> None:
        log = self.query_one(RichLog)
        log.auto_scroll = True
        log.scroll_end()
        self._update_follow()

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
        elif cmd == "/theme":
            self.app.switch_screen("theme" if self.app.dark else "dark")
        else:
            self.write("system", f"Unknown: {cmd}")

    async def _generate_response(self, prompt: str) -> None:
        await self.write_stream(prompt)

    # ── Folding / Sticky ───────────────────────────────────────────

    def action_fold_last(self) -> None:
        """Fold/collapse the last message."""
        log = self.query_one(RichLog)
        lines = log.text.split("\n")
        if lines:
            last = lines[-1]
            log.write(f"[dim](folded: {last[:40]}...)[/]")

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
