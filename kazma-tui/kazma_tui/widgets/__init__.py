"""Kazma TUI widgets package.

Provides reusable custom widgets for the Kazma terminal dashboard.
"""

from kazma_tui.widgets.log_stream import LogStream
from kazma_tui.widgets.toast import Toast
from kazma_tui.widgets.confirm_dialog import ConfirmDialog
from kazma_tui.widgets.command_palette import CommandPalette
from kazma_tui.widgets.tutorial import TutorialScreen
from kazma_tui.widgets.status_bar import (
    KazmaStatusBar,
    StatusIndicator,
    ClockWidget,
    TokenCounter,
    OperationStatus,
)
from kazma_tui.widgets.accessibility import (
    FocusManager,
    HighContrastMode,
)

__all__ = [
    # Core widgets
    "LogStream",
    "Toast",
    "ConfirmDialog",
    "CommandPalette",
    "TutorialScreen",
    # Status bar
    "KazmaStatusBar",
    "StatusIndicator",
    "ClockWidget",
    "TokenCounter",
    "OperationStatus",
    # Accessibility
    "FocusManager",
    "HighContrastMode",
]