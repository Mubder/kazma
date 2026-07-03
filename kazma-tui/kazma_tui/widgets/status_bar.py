"""Kazma TUI — Professional status bar with live metrics.

Features:
    - Connection status indicator
    - Current operation display
    - Real-time clock
    - Token usage counter
    - Model/provider info
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)


class StatusIndicator(Static):
    """Animated connection status indicator."""
    
    status = reactive("online")
    
    DEFAULT_CSS = """
    StatusIndicator {
        width: auto;
        padding: 0 1;
        content-align: center middle;
    }
    StatusIndicator.online {
        color: $success;
    }
    StatusIndicator.offline {
        color: $error;
    }
    StatusIndicator.connecting {
        color: $warning;
    }
    """
    
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pulse = True
    
    def on_mount(self) -> None:
        """Start pulse animation for connecting state."""
        self.set_interval(1.0, self._toggle_pulse)
    
    def _toggle_pulse(self) -> None:
        """Toggle pulse state for animation."""
        if self.status == "connecting":
            self._pulse = not self._pulse
            self.update("●" if self._pulse else "○")
        else:
            icons = {"online": "●", "offline": "○", "connecting": "◐"}
            self.update(icons.get(self.status, "○"))
    
    def watch_status(self, new_status: str) -> None:
        """Update styling when status changes."""
        self.remove_class("online", "offline", "connecting")
        self.add_class(new_status)
        if new_status != "connecting":
            icons = {"online": "● online", "offline": "○ offline", "connecting": "◐ connecting"}
            self.update(icons.get(new_status, "○"))


class ClockWidget(Static):
    """Real-time clock widget."""
    
    DEFAULT_CSS = """
    ClockWidget {
        width: auto;
        padding: 0 1;
        color: $text-muted;
    }
    """
    
    def on_mount(self) -> None:
        """Start clock update timer."""
        self.update_clock()
        self.set_interval(1.0, self.update_clock)
    
    def update_clock(self) -> None:
        """Update clock display."""
        now = datetime.now()
        self.update(now.strftime("%Y-%m-%d %H:%M:%S"))


class TokenCounter(Static):
    """Token usage counter."""
    
    tokens = reactive(0)
    
    DEFAULT_CSS = """
    TokenCounter {
        width: auto;
        padding: 0 1;
        color: $primary;
    }
    """
    
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.total_tokens = 0
        self.session_tokens = 0
    
    def add_tokens(self, count: int) -> None:
        """Add tokens to the counter."""
        self.total_tokens += count
        self.session_tokens += count
        self.tokens = self.session_tokens
    
    def reset_session(self) -> None:
        """Reset session token count."""
        self.session_tokens = 0
        self.tokens = 0
    
    def watch_tokens(self, new_count: int) -> None:
        """Update display when token count changes."""
        self.update(f"🪙 {new_count:,} tokens")


class OperationStatus(Static):
    """Current operation status display."""
    
    DEFAULT_CSS = """
    OperationStatus {
        width: 1fr;
        padding: 0 2;
        content-align: center middle;
        color: $text-muted;
    }
    """
    
    def set_operation(self, operation: str, status: str = "idle") -> None:
        """Set current operation and status."""
        icons = {
            "idle": "",
            "loading": "⏳ ",
            "success": "✅ ",
            "error": "❌ ",
            "warning": "⚠️ ",
        }
        icon = icons.get(status, "")
        self.update(f"{icon}{operation}" if operation else "Ready")


class KazmaStatusBar(Widget):
    """Professional status bar with multiple sections."""
    
    DEFAULT_CSS = """
    KazmaStatusBar {
        height: 3;
        background: $panel;
        border-top: solid $primary 30%;
        padding: 0 2;
        align: center middle;
    }
    
    KazmaStatusBar > .status-container {
        width: 100%;
        height: 100%;
    }
    
    KazmaStatusBar > .status-container > Horizontal {
        height: 100%;
        align: center middle;
    }
    """
    
    def __init__(
        self,
        provider: str = "Unknown",
        model: str = "Unknown",
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.provider = provider
        self.model = model
    
    def compose(self) -> ComposeResult:
        with Container(classes="status-container"):
            with Horizontal():
                # Left section: Connection status
                yield StatusIndicator(id="status-indicator")
                
                # Center-left: Provider/Model info
                yield Static(
                    f"[dim]{self.provider}[/] [bold]$primary[/] | [bold]{self.model}[/]",
                    id="model-info",
                )
                
                # Center: Operation status
                yield OperationStatus(id="operation-status")
                
                # Right section: Token counter and clock
                yield TokenCounter(id="token-counter")
                yield Static("|", classes="separator")
                yield ClockWidget(id="clock")
    
    def on_mount(self) -> None:
        """Initialize status bar."""
        # Set initial status
        self.set_status("online")
        self.set_operation("Ready", "idle")
    
    def set_status(self, status: str) -> None:
        """Set connection status (online, offline, connecting)."""
        try:
            indicator = self.query_one("#status-indicator", StatusIndicator)
            indicator.status = status
        except Exception as e:
            logger.debug(f"Error setting status: {e}")
    
    def set_operation(self, operation: str, status: str = "idle") -> None:
        """Set current operation and status."""
        try:
            op_status = self.query_one("#operation-status", OperationStatus)
            op_status.set_operation(operation, status)
        except Exception as e:
            logger.debug(f"Error setting operation: {e}")
    
    def add_tokens(self, count: int) -> None:
        """Add tokens to the counter."""
        try:
            counter = self.query_one("#token-counter", TokenCounter)
            counter.add_tokens(count)
        except Exception as e:
            logger.debug(f"Error adding tokens: {e}")
    
    def reset_session(self) -> None:
        """Reset session token count."""
        try:
            counter = self.query_one("#token-counter", TokenCounter)
            counter.reset_session()
        except Exception as e:
            logger.debug(f"Error resetting tokens: {e}")
    
    def set_model_info(self, provider: str, model: str) -> None:
        """Update provider and model information."""
        try:
            model_info = self.query_one("#model-info", Static)
            model_info.update(
                f"[dim]{provider}[/] [bold]$primary[/] | [bold]{model}[/]"
            )
        except Exception as e:
            logger.debug(f"Error updating model info: {e}")
