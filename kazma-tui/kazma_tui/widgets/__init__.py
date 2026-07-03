"""Kazma TUI widgets package.

Provides reusable custom widgets for the Kazma terminal dashboard.
"""

from kazma_tui.widgets.log_stream import LogStream, LoadingSpinner
from kazma_tui.widgets.toast import Toast
from kazma_tui.widgets.sparkline import Sparkline
from kazma_tui.widgets.circular_progress import CircularProgress
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
from kazma_tui.widgets.performance import (
    PerformanceManager,
    AdaptiveRefresh,
    Debouncer,
    debounce,
    TaskManager,
    ResourceMonitor,
)
from kazma_tui.widgets.accessibility import (
    AccessibleWidget,
    AccessibleStatusIndicator,
    FocusManager,
    HighContrastMode,
    AccessibilityAnnouncement,
    FocusTrap,
    SkipLink,
    STATUS_SYMBOLS,
    STATUS_LABELS,
)

__all__ = [
    # Core widgets
    "LogStream",
    "LoadingSpinner",
    "Toast",
    "Sparkline",
    "CircularProgress",
    "ConfirmDialog",
    "CommandPalette",
    "TutorialScreen",
    
    # Status bar
    "KazmaStatusBar",
    "StatusIndicator",
    "ClockWidget",
    "TokenCounter",
    "OperationStatus",
    
    # Performance
    "PerformanceManager",
    "AdaptiveRefresh",
    "Debouncer",
    "debounce",
    "TaskManager",
    "ResourceMonitor",
    
    # Accessibility
    "AccessibleWidget",
    "AccessibleStatusIndicator",
    "FocusManager",
    "HighContrastMode",
    "AccessibilityAnnouncement",
    "FocusTrap",
    "SkipLink",
    "STATUS_SYMBOLS",
    "STATUS_LABELS",
]