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

    ALLOW_SELECT = True

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

    BINDINGS = [
        ("ctrl+a", "select_all", "Select All"),
        ("shift+enter", "insert_newline", "Newline"),
        ("ctrl+enter", "insert_newline", "Newline"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._last_response: str = ""

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True, auto_scroll=True)
        yield ProgressBar(id="chat-progress", total=100, show_eta=False)
        yield Input(placeholder="Type... /help for commands", id="chat-input")

    # ── Message display ────────────────────────────────────────────

    def write(self, role: str, text: str) -> None:
        """Write a message to the chat log with role prefix."""
        log = self.query_one("#chat-log", RichLog)
        ts = datetime.now().strftime("%H:%M")
        c = ROLE_HEX.get(role, "#8b949e")
        log.write(f"[dim]{ts}[/] [{c}]▌ {role.upper()}[/] {text}")

    def add_message(self, role: str, text: str) -> None:
        """Alias for write() - adds a message to the chat log."""
        self.write(role, text)

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
        """Send prompt to provider and write response to RichLog."""
        log = self.query_one("#chat-log", RichLog)
        ts = datetime.now().strftime("%H:%M")
        log.write(f"[dim]{ts}[/] [#a855f7]▌ KAZMA[/] ")
        self.show_progress(True)

        try:
            from kazma_core.model_registry import get_model_registry

            try:
                registry = get_model_registry()
                provider = registry.get_client()
            except RuntimeError:
                log.write(
                    "\n[#ef4444]Error: ModelRegistry not initialized. "
                    "Start the kazma-ui server first, or run "
                    "kazma_core.bootstrap.initialize().[/]"
                )
                return
            if provider is None:
                log.write(
                    "\n[#ef4444]Error: No LLM provider configured. "
                    "Add a provider via /models in the chat, or via kazma.yaml.[/]"
                )
                return

            messages = [{"role": "user", "content": prompt}]
            # Inject system prompt from kazma.yaml so the model knows to
            # respond in the user's language and follow Kazma's persona.
            system_prompt = self._get_system_prompt()
            if system_prompt:
                messages.insert(0, {"role": "system", "content": system_prompt})
            response = await provider.chat(messages)
            content = getattr(response, "content", "") or ""
            if content:
                self._last_response = content
                log.write(content)
            else:
                log.write("[dim](empty response)[/]")
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
            self.write("system", "Commands: /help /clear /model /quit | Copy: Ctrl+A then Ctrl+Shift+C, or Shift+drag mouse to select text")
        elif cmd == "/clear":
            self.query_one("#chat-log", RichLog).clear()
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

    @staticmethod
    def _get_system_prompt() -> str:
        """Load the system prompt from kazma.yaml or ConfigStore.

        The TUI chat is a direct LLM call (no LangGraph supervisor),
        so we must inject the system prompt ourselves to ensure the
        model follows Kazma's persona and language-matching rules.
        """
        try:
            from kazma_core.config_store import get_config_store
            cs = get_config_store()
            prompt = cs.get("system_prompt")
            if prompt:
                return str(prompt)
        except Exception:
            pass
        # Fallback: read directly from kazma.yaml
        try:
            from pathlib import Path
            import yaml
            yaml_path = Path("kazma.yaml")
            if yaml_path.exists():
                with open(yaml_path, encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                prompt = data.get("system_prompt")
                if prompt:
                    return str(prompt)
        except Exception:
            pass
        return ""

    # ── Copy ───────────────────────────────────────────────────────

    def action_select_all(self) -> None:
        """Select all text in the chat log."""
        try:
            self.query_one("#chat-log", RichLog).text_select_all()
        except Exception:
            pass

    def action_insert_newline(self) -> None:
        """Insert a newline character at the cursor in the chat input.

        Required so users can compose multi-line prompts without sending
        them prematurely on Enter.
        """
        try:
            chat_input = self.query_one("#chat-input", Input)
            chat_input.insert("\n")
        except Exception:
            pass

    def copy_to_clipboard(self) -> None:
        """Copy currently selected text or last KAZMA response to system clipboard.

        Tries screen-level text selection first (from mouse drag or
        Ctrl+A).  Falls back to the last assistant response tracked in
        _last_response, since RichLog has no .text property to read back.
        """
        try:
            selected = self.screen.get_selected_text()
            if selected:
                self.app.copy_to_clipboard(selected)
                return
        except Exception:
            pass
        # Fallback: copy the last tracked KAZMA response
        if self._last_response:
            self.app.copy_to_clipboard(self._last_response)
            return
