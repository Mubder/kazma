"""Real-time log stream widget for the Kazma TUI.

Renders a scrollable, color-coded log view that subscribes to the
SwarmMessageBus and displays worker log lines as they arrive.
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.widgets import RichLog, Static

logger = logging.getLogger(__name__)

# Maximum number of log lines to retain in the buffer.
_MAX_LINES = 500

# Color mapping for log levels.
_LEVEL_COLORS: dict[str, str] = {
    "info":    "#00ff00",   # green
    "warn":    "#ffff00",   # yellow
    "error":   "#ff4444",   # red
    "success": "#00ff00",   # green
}


class LogStream(RichLog):
    """Scrollable log view that subscribes to the SwarmMessageBus.

    Color-codes lines by level (info=green, warn=yellow, error=red).
    Auto-scrolls to the latest entry.  Caps buffer at 500 lines.
    """

    DEFAULT_CSS = """
    LogStream {
        height: 1fr;
        border: solid $primary;
        background: $surface;
    }
    """

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
        max_lines: int = _MAX_LINES,
    ) -> None:
        super().__init__(name=name, id=id, max_lines=max_lines, highlight=True, markup=True)

    def on_mount(self) -> None:
        self.write("🐝 SwarmMessageBus log stream started.\n")

    def add_entry(
        self,
        worker_name: str,
        content: str,
        level: str = "info",
        timestamp: str = "",
    ) -> None:
        """Append a color-coded log entry."""
        color = _LEVEL_COLORS.get(level, "#ffffff")
        icon = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "success": "✅"}.get(level, "📍")
        ts = f"[{timestamp[11:19]}] " if timestamp else ""
        self.write(f"[{color}]{icon} {ts}[{worker_name}][/{color}] {content}")

    def handle_bus_event(self, event_type: str, data: dict) -> None:
        """Callback for SwarmMessageBus.subscribe()."""
        if event_type == "stream":
            self.add_entry(
                worker_name=data.get("worker_name", "?"),
                content=data.get("content", ""),
                level=data.get("level", "info"),
                timestamp=data.get("timestamp", ""),
            )
        elif event_type == "report":
            self.add_entry(
                worker_name=data.get("worker_name", "?"),
                content=f"Report: {data.get('status', 'unknown')} ({data.get('duration_ms', 0):.0f}ms)",
                level="success" if data.get("status") == "success" else "warn",
                timestamp="",
            )


class LoadingSpinner(Static):
    """Animated loading spinner for async operations.
    
    Displays a rotating spinner animation during loading states.
    Uses Unicode braille characters for smooth animation.
    """

    SPINNER_CHARS = "⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏"
    
    DEFAULT_CSS = """
    LoadingSpinner {
        width: auto;
        height: 1;
        color: $primary;
        text-style: bold;
    }
    """
    
    def __init__(
        self,
        message: str = "Loading...",
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.message = message
        self._spinner_index = 0

    def compose(self) -> ComposeResult:
        yield Static(self._render_spinner())

    def on_mount(self) -> None:
        self._animate_timer = self.set_interval(0.1, self._advance_spinner)

    def _advance_spinner(self) -> None:
        self._spinner_index = (self._spinner_index + 1) % len(self.SPINNER_CHARS.split())
        spinner_char = self.SPINNER_CHARS.split()[self._spinner_index]
        self.update(f"{spinner_char}  {self.message}")

    def _render_spinner(self) -> str:
        spinner_char = self.SPINNER_CHARS.split()[self._spinner_index]
        return f"{spinner_char}  {self.message}"

    def stop(self) -> None:
        """Stop the animation and hide the spinner."""
        if hasattr(self, "_animate_timer"):
            self._animate_timer.stop()
        self.display = False
