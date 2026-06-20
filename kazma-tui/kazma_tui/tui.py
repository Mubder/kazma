"""Kazma TUI — A Textual-based Terminal User Interface with Arabic support.

Features:
- Full Arabic/RTL text rendering (via arabic_reshaper + python-bidi)
- RTL layout for prompt and user input
- Chat interface with kazma> prompt
- Status bar showing model, tools, and session info
- Connected to KazmaAgent for real LLM responses
"""

from __future__ import annotations

import logging
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, RichLog, Static

from kazma_core.agent import AgentConfig, KazmaAgent, load_config

logger = logging.getLogger(__name__)

try:
    import arabic_reshaper
except ImportError:
    arabic_reshaper = None

try:
    from bidi.algorithm import get_display
except ImportError:
    get_display = None

# Unicode directional markers
RLE = "\u202b"  # Right-to-Left Embedding
POP = "\u202c"  # Pop Directional Formatting
LRM = "\u200e"  # Left-to-Right Mark
RLM = "\u200f"  # Right-to-Left Mark


# ── Arabic text support ─────────────────────────────────────────────


def _fix_arabic(text: str) -> str:
    """Reshape Arabic text for proper terminal display.

    Converts isolated Arabic letters to their connected forms
    and applies the Unicode bidirectional algorithm so that
    Arabic renders right-to-left in the terminal.

    Falls back to the original text if libraries aren't installed.
    """
    if arabic_reshaper is None or get_display is None:
        return text
    try:
        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        return text


def _rtl_embed(text: str) -> str:
    """Wrap text with Unicode RTL embedding markers.

    This nudges terminal emulators to render the text RTL.
    """
    return f"{RLE}{text}{POP}"


# ── Custom RTL-aware Input ──────────────────────────────────────────


class RTLInput(Input):
    """An Input widget that wraps Arabic input with RTL markers."""

    def _render_text(self, text: str) -> str:
        """Render text with RTL embedding for Arabic content."""
        if arabic_reshaper is not None and get_display is not None:
            # If text contains Arabic, wrap it for RTL rendering
            if any('\u0600' <= c <= '\u06FF' or '\u0750' <= c <= '\u077F' or '\u08A0' <= c <= '\u08FF' or '\uFB50' <= c <= '\uFDFF' or '\uFE70' <= c <= '\uFEFF' for c in text):
                reshaped = arabic_reshaper.reshape(text)
                display = get_display(reshaped)
                return f"{RLE}{display}{POP}"
        return text


# ── TUI App ─────────────────────────────────────────────────────────


class KazmaTUI(App):
    """Kazma TUI — terminal chat interface for the Kazma agent."""

    CSS = """
    Screen {
        background: $surface;
    }

    #status-bar {
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }

    #chat-area {
        height: 1fr;
        border: solid $primary;
        margin: 0 1;
    }

    #chat-log {
        height: 100%;
        background: $surface;
    }

    #input-row {
        height: 3;
        margin: 0 1 1 1;
    }

    #prompt-label {
        width: 8;
        content-align: right middle;
        background: $primary;
        color: $text;
        padding: 0 1;
        text-align: right;
    }

    #message-input {
        height: 3;
        text-align: right;
    }

    Label {
        padding: 0 1;
    }

    .msg-user {
        color: $accent;
    }

    .msg-assistant {
        color: $text;
    }

    .msg-system {
        color: $warning;
    }

    .msg-error {
        color: $error;
    }
    """

    TITLE = "\u202bكاظمه\u202c — Kazma"

    def __init__(self, config: AgentConfig | None = None) -> None:
        super().__init__()
        self._config = config or load_config()
        self._agent: KazmaAgent | None = None
        self._running = False

    def compose(self) -> ComposeResult:
        """Build the TUI layout."""
        model_info = self._config.raw.get("llm", {}).get("model", self._config.default_model)
        yield Static(
            _fix_arabic(f"🇰🇼 كاظمه v{self._config.version}  •  {model_info}"),
            id="status-bar",
        )
        with Vertical(id="chat-area"):
            yield RichLog(
                id="chat-log",
                highlight=True,
                markup=True,
                wrap=True,
                max_lines=10_000,
            )
        with Horizontal(id="input-row"):
            yield Label(_fix_arabic(_rtl_embed(" كاظمه> ")), id="prompt-label")
            yield RTLInput(
                id="message-input",
                placeholder=_fix_arabic(_rtl_embed("اكتب رسالتك هنا...")),
            )

    async def on_mount(self) -> None:
        """Initialize the agent and connect MCP servers."""
        log = self.query_one("#chat-log", RichLog)
        log.write(_fix_arabic("[bold]🇰🇼 كاظمه — Kazma Autonomous Agent[/bold]"))
        log.write("")

        try:
            self._agent = KazmaAgent(self._config)
            n_tools = await self._agent.connect_mcp_servers()
            self._running = True
            status = self.query_one("#status-bar", Static)
            tool_info = f" • 🔧 {n_tools} tools" if n_tools else ""
            model_name = self._agent.llm_config.model
            status.update(
                _fix_arabic(
                    f"🇰🇼 كاظمه v{self._config.version}  •  {model_name}{tool_info}"
                )
            )
            log.write(_fix_arabic(f"[dim]Connected to {model_name}{tool_info}[/dim]"))
            log.write("")
        except Exception as e:
            log.write(_fix_arabic(f"[red]❌ Failed to initialize: {e}[/red]"))

        # Focus the input
        self.query_one("#message-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        if not self._agent or not self._running:
            return

        user_text = event.value.strip()
        if not user_text:
            return

        # Clear input
        input_widget = self.query_one("#message-input", Input)
        input_widget.value = ""

        # Show user message
        log = self.query_one("#chat-log", RichLog)
        log.write(_fix_arabic(f"[bold][دخول][/bold] {user_text}"))

        # Show typing indicator
        log.write(_fix_arabic("[dim]⏳ كاظمه تفكر...[/dim]"))

        # Get response
        try:
            response = await self._agent.run(user_text)
            log.write(_fix_arabic(f"[bold][كاظمه][/bold] {response}"))
        except Exception as e:
            log.write(_fix_arabic(f"[red]❌ خطأ: {e}[/red]"))

        log.write("")
        input_widget.focus()

    async def on_exit(self) -> None:
        """Clean shutdown."""
        self._running = False
        if self._agent:
            await self._agent.shutdown()


def main() -> None:
    """Entry point for the Kazma TUI."""
    config = load_config()
    app = KazmaTUI(config)
    app.run()


if __name__ == "__main__":
    main()
