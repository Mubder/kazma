"""Kazma TUI — Accessibility enhancements for inclusive design.

Features:
    - ARIA-like labels for screen readers
    - Non-color status indicators (symbols + text)
    - Focus management and navigation
    - High-contrast mode support
    - Keyboard-only operation
"""

from __future__ import annotations

import logging
from typing import Optional, Protocol, runtime_checkable

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Label, Static

logger = logging.getLogger(__name__)


@runtime_checkable
class Accessible(Protocol):
    """Protocol for widgets with accessibility attributes."""
    
    accessible_label: str
    accessible_role: str
    accessible_description: Optional[str]
    
    def get_accessibility_info(self) -> dict:
        """Return accessibility information."""
        ...


class AccessibleWidget(Widget):
    """Base widget with accessibility attributes.
    
    Provides ARIA-like labels and roles for better screen reader support.
    """
    
    DEFAULT_CSS = """
    AccessibleWidget {
        /* No default styling - base class */
    }
    """
    
    def __init__(
        self,
        label: str = "",
        role: str = "widget",
        description: Optional[str] = None,
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.accessible_label = label
        self.accessible_role = role
        self.accessible_description = description
    
    def get_accessibility_info(self) -> dict:
        """Return accessibility information for screen readers."""
        return {
            "label": self.accessible_label,
            "role": self.accessible_role,
            "description": self.accessible_description,
        }
    
    def set_label(self, label: str) -> None:
        """Update the accessible label."""
        self.accessible_label = label
    
    def set_description(self, description: str) -> None:
        """Update the accessible description."""
        self.accessible_description = description


class FocusManager:
    """Manages focus navigation through panels.
    
    Provides predictable focus cycling for keyboard-only users.
    """
    
    def __init__(self, focus_order: list[str]) -> None:
        """Initialize with ordered list of widget IDs to focus."""
        self.focus_order = focus_order
        self._current_index = 0
    
    def focus_next(self, app) -> None:
        """Focus the next widget in order."""
        if not self.focus_order:
            return
        
        self._current_index = (self._current_index + 1) % len(self.focus_order)
        widget_id = self.focus_order[self._current_index]
        
        try:
            widget = app.query_one(f"#{widget_id}")
            widget.focus()
        except Exception as e:
            logger.debug(f"Error focusing {widget_id}: {e}")
    
    def focus_previous(self, app) -> None:
        """Focus the previous widget in order."""
        if not self.focus_order:
            return
        
        self._current_index = (self._current_index - 1) % len(self.focus_order)
        widget_id = self.focus_order[self._current_index]
        
        try:
            widget = app.query_one(f"#{widget_id}")
            widget.focus()
        except Exception as e:
            logger.debug(f"Error focusing {widget_id}: {e}")
    
    def focus_first(self, app) -> None:
        """Focus the first widget."""
        self._current_index = 0
        if self.focus_order:
            try:
                widget = app.query_one(f"#{self.focus_order[0]}")
                widget.focus()
            except Exception as e:
                logger.debug(f"Error focusing first widget: {e}")
    
    def focus_by_id(self, app, widget_id: str) -> None:
        """Focus a specific widget by ID."""
        try:
            widget = app.query_one(f"#{widget_id}")
            widget.focus()
            if widget_id in self.focus_order:
                self._current_index = self.focus_order.index(widget_id)
        except Exception as e:
            logger.debug(f"Error focusing {widget_id}: {e}")
    
    def reset(self) -> None:
        """Reset focus index to beginning."""
        self._current_index = 0


# Status indicator symbols (non-color dependent)
STATUS_SYMBOLS = {
    "online": "●",
    "offline": "○",
    "connecting": "◐",
    "error": "✕",
    "warning": "⚠",
    "success": "✓",
    "info": "ℹ",
}

# Status text labels (always paired with symbols)
STATUS_LABELS = {
    "online": "Online",
    "offline": "Offline",
    "connecting": "Connecting",
    "error": "Error",
    "warning": "Warning",
    "success": "Success",
    "info": "Info",
}


class AccessibleStatusIndicator(AccessibleWidget):
    """Status indicator with both color and symbol for accessibility.
    
    Never relies on color alone - always includes text label.
    """
    
    DEFAULT_CSS = """
    AccessibleStatusIndicator {
        width: auto;
        padding: 0 1;
        content-align: center middle;
    }
    AccessibleStatusIndicator.online {
        color: $success;
    }
    AccessibleStatusIndicator.offline {
        color: $error;
    }
    AccessibleStatusIndicator.connecting {
        color: $warning;
    }
    AccessibleStatusIndicator.error {
        color: $error;
    }
    AccessibleStatusIndicator.warning {
        color: $warning;
    }
    """
    
    def __init__(self, status: str = "offline", **kwargs) -> None:
        label = f"{STATUS_SYMBOLS.get(status, '○')} {STATUS_LABELS.get(status, 'Unknown')}"
        super().__init__(
            label=label,
            role="status",
            description=f"Current status: {STATUS_LABELS.get(status, 'unknown')}",
            **kwargs
        )
        self._status = status
    
    def compose(self) -> ComposeResult:
        yield Static(self.accessible_label)
    
    def set_status(self, status: str) -> None:
        """Update status with accessible label."""
        self._status = status
        self.accessible_label = f"{STATUS_SYMBOLS.get(status, '○')} {STATUS_LABELS.get(status, 'Unknown')}"
        self.accessible_description = f"Current status: {STATUS_LABELS.get(status, 'unknown')}"
        
        # Update CSS class for color (secondary cue)
        self.remove_class("online", "offline", "connecting", "error", "warning")
        self.add_class(status)
        
        # Update displayed text
        try:
            self.query_one(Static).update(self.accessible_label)
        except Exception:
            pass
    
    def get_accessibility_info(self) -> dict:
        """Return enhanced accessibility info."""
        info = super().get_accessibility_info()
        info["status"] = self._status
        info["symbol"] = STATUS_SYMBOLS.get(self._status, "○")
        return info


class SkipLink(Static):
    """Skip link for keyboard users to bypass repetitive content.
    
    Appears on Tab press at start of page, allows skipping to main content.
    """
    
    DEFAULT_CSS = """
    SkipLink {
        display: none;
        background: $primary;
        color: $text;
        padding: 1 2;
        position: absolute;
        offset: 0 0;
    }
    SkipLink:focus {
        display: block;
    }
    """
    
    def __init__(self, target_id: str = "main-content", **kwargs) -> None:
        super().__init__(f"Skip to {target_id}", id="skip-link", **kwargs)
        self.target_id = target_id
    
    def on_focus(self) -> None:
        """When focused, announce skip option."""
        self.styles.display = "block"
    
    def on_blur(self) -> None:
        """Hide when not focused."""
        self.styles.display = "none"
    
    def on_key(self, event) -> None:
        """Handle Enter key to skip."""
        if event.key == "enter":
            try:
                app = self.app
                target = app.query_one(f"#{self.target_id}")
                target.focus()
            except Exception as e:
                logger.debug(f"Error skipping to {self.target_id}: {e}")


class HighContrastMode:
    """Manages high contrast mode for visually impaired users.
    
    Provides enhanced contrast ratios meeting WCAG AAA standards.
    """
    
    HIGH_CONTRAST_CSS = """
    /* High contrast theme overrides */
    Screen {
        background: black;
        color: white;
    }
    
    Static, Label, Input, Button {
        color: yellow;
    }
    
    .panel, Container {
        background: black;
        border: solid yellow;
    }
    
    Button {
        background: black;
        border: heavy yellow;
    }
    
    Button:hover, Button:focus {
        background: yellow;
        color: black;
    }
    
    /* Ensure all links are underlined */
    Link {
        text-style: underline;
    }
    
    /* Bold all text for better readability */
    * {
        text-style: bold;
    }
    """
    
    def __init__(self, app) -> None:
        self.app = app
        self._enabled = False
        self._prev_stylesheet = None
    
    def enable(self) -> None:
        """Enable high contrast mode."""
        self._enabled = True
        # Save current stylesheet to restore later
        self._prev_stylesheet = self.app.stylesheet
        # Build a proper Stylesheet object (not a raw string)
        from textual.stylesheet import Stylesheet
        ss = Stylesheet()
        # Preserve widget default CSS
        try:
            for css_path, css_text, tie_breaker, scope in self.app._get_default_css():
                ss.add_source(
                    css_text,
                    path=css_path,
                    tie_breaker=tie_breaker,
                    scope=scope,
                )
        except Exception:
            pass
        # Add high contrast overrides
        ss.add_source(self.HIGH_CONTRAST_CSS, path="<high-contrast>")
        ss.apply(self.app)
        self.app.stylesheet = ss
        try:
            self.app.refresh_css()
        except Exception:
            pass
    
    def disable(self) -> None:
        """Disable high contrast mode."""
        self._enabled = False
        # Restore previous stylesheet
        if self._prev_stylesheet is not None:
            try:
                self.app.stylesheet = self._prev_stylesheet
                self.app.refresh_css()
            except Exception:
                logger.debug("[HighContrast] Failed to restore previous stylesheet", exc_info=True)
                self._prev_stylesheet = None
        # Fallback: re-apply the default theme
        if self._prev_stylesheet is None:
            try:
                from kazma_tui.themes.theme_manager import ThemeManager
                tm = ThemeManager(self.app)
                tm.apply_theme("kazma-dark")
            except Exception:
                logger.debug("[HighContrast] ThemeManager fallback failed", exc_info=True)
    
    def toggle(self) -> bool:
        """Toggle high contrast mode."""
        if self._enabled:
            self.disable()
            return False
        else:
            self.enable()
            return True
    
    @property
    def is_enabled(self) -> bool:
        """Check if high contrast mode is active."""
        return self._enabled


class AccessibilityAnnouncement(Static):
    """Live region for announcing dynamic content changes.
    
    Similar to ARIA live regions, announces updates to screen readers.
    """
    
    DEFAULT_CSS = """
    AccessibilityAnnouncement {
        height: 1;
        visibility: hidden;
    }
    """
    
    def __init__(self, **kwargs) -> None:
        super().__init__("", id="aria-live", **kwargs)
        self.politeness = "polite"  # or "assertive"
    
    def announce(self, message: str, politeness: str = "polite") -> None:
        """Announce a message to screen readers.
        
        Args:
            message: Message to announce
            politeness: "polite" or "assertive"
        """
        self.politeness = politeness
        self.update(message)
        
        # Briefly make visible for screen readers
        self.styles.visibility = "visible"
        
        # Hide after announcement
        def hide():
            self.styles.visibility = "hidden"
            self.update("")
        
        self.set_timer(1.0, hide)


class FocusTrap(ModalScreen):
    """Modal that traps focus for accessibility.
    
    Ensures keyboard users cannot tab outside the modal.
    """
    
    DEFAULT_CSS = """
    FocusTrap {
        align: center middle;
    }
    FocusTrap > Container {
        width: 80%;
        height: auto;
        background: $surface;
        border: solid $primary;
        padding: 2 4;
    }
    """
    
    def __init__(
        self,
        title: str,
        content: str,
        confirm_text: str = "OK",
        cancel_text: str = "Cancel",
        **kwargs
    ) -> None:
        super().__init__(**kwargs)
        self.title = title
        self.content = content
        self.confirm_text = confirm_text
        self.cancel_text = cancel_text
    
    def compose(self) -> ComposeResult:
        with Container():
            yield Label(f"[bold]{self.title}[/]", id="modal-title")
            yield Label(self.content, id="modal-content")
            with Container(classes="button-row"):
                yield Button(self.confirm_text, variant="primary", id="confirm")
                yield Button(self.cancel_text, variant="default", id="cancel")
    
    def on_mount(self) -> None:
        """Focus first button when modal opens."""
        self.query_one("#confirm", Button).focus()
    
    def action_confirm(self) -> None:
        """Handle confirmation."""
        self._safe_dismiss(True)
    
    def action_cancel(self) -> None:
        """Handle cancellation."""
        self._safe_dismiss(False)
    
    def _safe_dismiss(self, result=None) -> None:
        """Dismiss without returning AwaitComplete (Textual 8.x crash fix)."""
        try:
            self.dismiss(result)
        except Exception:
            pass
    
    BINDINGS = [
        ("enter", "confirm", "Confirm"),
        ("escape", "cancel", "Cancel"),
    ]
