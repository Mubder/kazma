"""Kazma TUI — A Textual-based Terminal User Interface with Arabic support.

Features:
- Full Arabic/RTL text rendering (via arabic_reshaper + python-bidi)
- Chat interface with kazma> prompt
- Status bar showing model, tools, and session info
- Connected to KazmaAgent for real LLM responses
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

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
        content-align: left middle;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    #message-input {
        height: 3;
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
            yield Label(_fix_arabic(" كاظمه> "), id="prompt-label")
            yield Input(
                id="message-input",
                placeholder=_fix_arabic("اكتب رسالتك هنا..."),
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
            # Remove the typing indicator and show response
            # RichLog doesn't support removal, so just append
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
    import sys

    config = load_config()
    app = KazmaTUI(config)
    app.run()


if __name__ == "__main__":
    main()
