"""Circular progress indicator widget.

Displays percentage values in a compact circular format.
"""

from __future__ import annotations

from textual.widget import Widget


class CircularProgress(Widget):
    """Circular progress indicator for CPU/RAM usage display.
    
    Shows percentage as a compact circular gauge using Unicode characters.
    
    Example:
        cpu_gauge = CircularProgress(75)  # Shows 75%
    """
    
    DEFAULT_CSS = """
    CircularProgress {
        width: 6;
        height: 1;
        color: $primary;
    }
    CircularProgress.high {
        color: $error;
    }
    CircularProgress.medium {
        color: $warning;
    }
    CircularProgress.low {
        color: $success;
    }
    """
    
    def __init__(
        self,
        percentage: float = 0.0,
        show_label: bool = True,
        **kwargs,
    ) -> None:
        """Initialize circular progress widget.
        
        Args:
            percentage: Initial percentage (0-100)
            show_label: Whether to show percentage text
        """
        super().__init__(**kwargs)
        self._percentage = max(0.0, min(100.0, percentage))
        self._show_label = show_label
    
    @property
    def percentage(self) -> float:
        """Get current percentage."""
        return self._percentage
    
    @percentage.setter
    def percentage(self, value: float) -> None:
        """Set percentage and update styling."""
        self._percentage = max(0.0, min(100.0, value))
        self._update_style()
        self.refresh()
    
    def _update_style(self) -> None:
        """Update CSS classes based on percentage level."""
        self.remove_class("high", "medium", "low")
        if self._percentage >= 80:
            self.add_class("high")
        elif self._percentage >= 50:
            self.add_class("medium")
        else:
            self.add_class("low")
    
    def render(self) -> str:
        """Render the circular progress indicator."""
        if self._show_label:
            return f"[{self._percentage:.0f}%]"
        else:
            # Simple bar representation without label
            filled = int(self._percentage / 10)
            empty = 10 - filled
            return f"{'█' * filled}{'░' * empty}"
