"""Kazma TUI — A Textual-based Terminal User Interface with Arabic support.

Features:
- Full Arabic/RTL text rendering via arabic_reshaper + python-bidi
- Arabic-aware input: reshapes on-the-fly while keeping value raw
- RTL layout: input on left, prompt label on right
- Chat interface with real-time KazmaAgent integration
- Status bar showing model, tools, and session info
"""

from __future__ import annotations

import logging

from kazma_core.agent import AgentConfig, KazmaAgent, load_config
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Label, RichLog, Static

logger = logging.getLogger(__name__)

try:
    from bidi.algorithm import get_display
except ImportError:
    get_display = None


# ── Arabic text support ─────────────────────────────────────────────


def _fix_arabic(text: str) -> str:
    """Apply Unicode bidi algorithm for Arabic text display.

    Uses python-bidi to reorder Arabic text for RTL display
    WITHOUT reshaping characters — this avoids font clipping
    issues in fixed-width terminal cells.

    Falls back to the original text if libraries aren't installed.
    """
    if get_display is None:
        return text
    try:
        result = get_display(text)
        return str(result) if not isinstance(result, str) else result
    except Exception:
        return text


def _has_arabic(text: str) -> bool:
    """Check if text contains Arabic script characters."""
    return any(
        "\u0600" <= c <= "\u06ff"
        or "\u0750" <= c <= "\u077f"
        or "\u08a0" <= c <= "\u08ff"
        or "\ufb50" <= c <= "\ufdff"
        or "\ufe70" <= c <= "\ufeff"
        for c in text
    )


# ── Arabic-aware Input ──────────────────────────────────────────────


class ArabicInput(Input):
    """Input widget that reshapes Arabic text for display.

    Overrides the _value property so the display shows correctly
    reshaped Arabic characters while keeping self.value (raw text)
    untouched for submission.
    """

    @property
    def _value(self) -> RichText:
        """Value rendered with Arabic reshaping for display."""
        if self.password:
            return RichText("•" * len(self.value), no_wrap=True, overflow="ignore", end="")

        raw = self.value
        display = _fix_arabic(raw) if _has_arabic(raw) else raw
        text = RichText(display, no_wrap=True, overflow="ignore", end="")
        if self.highlighter is not None:
            text = self.highlighter(text)
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
        padding: 0 1 0 0;
        text-align: right;
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
        width: 10;
        background: $primary;
        color: $text;
        padding: 0 1;
        text-align: right;
    }

    #message-input {
        height: 3;
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

    TITLE = "كاظمه — Kazma"

    def __init__(self, config: AgentConfig | None = None) -> None:
        super().__init__()
        self._config = config or load_config()
        self._agent: KazmaAgent | None = None
        self._running = False

    def compose(self) -> ComposeResult:
        """Build the TUI layout."""
        model_info = self._config.raw.get("llm", {}).get("model", self._config.default_model)
        yield Static(
            _fix_arabic(f" 🇰🇼 كاظمه v{self._config.version}  •  {model_info}"),
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
        # Label on the RIGHT side (last child = rightmost)
        with Horizontal(id="input-row"):
            yield ArabicInput(
                id="message-input",
                placeholder=_fix_arabic("اكتب رسالتك هنا..."),
            )
            yield Label(_fix_arabic("كاظمه> "), id="prompt-label")

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
            tool_info = f" 🔧 {n_tools} tools" if n_tools else ""
            model_name = self._agent.llm_config.model
            status.update(_fix_arabic(f" 🇰🇼 كاظمه v{self._config.version}  •  {model_name}{tool_info}"))
            log.write(_fix_arabic(f"[dim]Connected to {model_name}{tool_info}[/dim]"))
            log.write("")
        except Exception as e:
            log.write(_fix_arabic(f"[red]❌ Failed to initialize: {e}[/red]"))

        self.query_one("#message-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle user input submission."""
        if not self._agent or not self._running:
            return

        user_text = event.value.strip()
        if not user_text:
            return

        input_widget = self.query_one("#message-input", Input)
        input_widget.value = ""

        log = self.query_one("#chat-log", RichLog)
        log.write(_fix_arabic(f"[bold][دخول][/bold] {user_text}"))
        log.write(_fix_arabic("[dim]⏳ كاظمه تفكر...[/dim]"))

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
