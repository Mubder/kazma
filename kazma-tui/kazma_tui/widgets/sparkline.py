"""Sparkline widget for displaying mini trend charts.

Provides inline visualization of metric trends over time.
"""

from __future__ import annotations

from textual.widget import Widget

__all__ = ["Sparkline"]


class Sparkline(Widget):
    """Inline mini chart for metrics trend visualization.
    
    Displays a sequence of values as a sparkline using Unicode block elements.
    Useful for showing CPU, memory, or task count trends in compact spaces.
    
    Example:
        sparkline = Sparkline([10, 25, 18, 30, 45, 35, 50])
    """
    
    DEFAULT_CSS = """
    Sparkline {
        height: 1;
        width: 100%;
        color: $primary;
    }
    """
    
    # Unicode block characters for sparkline visualization
    BLOCKS = " ▁▂▃▄▅▆▇█"
    
    def __init__(
        self,
        data: list[float] | None = None,
        max_points: int = 20,
        **kwargs,
    ) -> None:
        """Initialize sparkline widget.
        
        Args:
            data: Initial data points (list of floats)
            max_points: Maximum number of points to display
        """
        super().__init__(**kwargs)
        self._data = data or []
        self._max_points = max_points
    
    @property
    def data(self) -> list[float]:
        """Get current data points."""
        return self._data
    
    @data.setter
    def data(self, value: list[float]) -> None:
        """Set data points and refresh display."""
        self._data = value[-self._max_points:] if value else []
        self.refresh()
    
    def add_point(self, value: float) -> None:
        """Add a new data point, removing oldest if at capacity."""
        self._data.append(value)
        if len(self._data) > self._max_points:
            self._data.pop(0)
        self.refresh()
    
    def clear(self) -> None:
        """Clear all data points."""
        self._data = []
        self.refresh()
    
    def render(self) -> str:
        """Render the sparkline as a string of block characters."""
        if not self._data:
            return "─" * min(20, self.size.width)
        
        max_val = max(self._data) if self._data else 1
        min_val = min(self._data) if self._data else 0
        
        # Handle flat data (all same value)
        if max_val == min_val:
            return "▄" * len(self._data)
        
        # Normalize values to block indices
        range_val = max_val - min_val
        chars = []
        for val in self._data:
            normalized = (val - min_val) / range_val
            idx = int(normalized * 8)
            idx = max(0, min(8, idx))  # Clamp to valid range
            chars.append(self.BLOCKS[idx])
        
        return "".join(chars)
