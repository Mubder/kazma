"""Kazma TUI Theme Manager — Multiple professional themes.

Provides theme switching and user preference persistence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App

# Light theme with high contrast for readability
LIGHT_THEME = """
/* ═══════════════════════════════════════════════════════════════════════
   Light Theme — High contrast for daytime use
   ═══════════════════════════════════════════════════════════════════════ */

/* Base colors */
$primary:    #0891b2;      /* cyan-600 */
$secondary:  #7c3aed;      /* violet-600 */
$accent:     #0891b2;
$error:      #dc2626;      /* red-600 */
$success:    #059669;      /* emerald-600 */
$warning:    #d97706;      /* amber-600 */
$surface:    #ffffff;      /* white bg */
$panel:      #f8fafc;      /* slate-50 */
$boost:      #f1f5f9;      /* slate-100 */
$border:     rgba(0,0,0,0.1);

$text:       #0f172a;      /* slate-900 */
$text-muted: #475569;      /* slate-600 */
$text-disabled: #94a3b8;   /* slate-400 */

Screen {
    background: $surface;
    color: $text;
}

Header {
    dock: top;
    height: 4;
    background: $panel;
    border-bottom: double $primary 60%;
    color: $text;
    text-style: bold;
}

Footer {
    dock: bottom;
    height: 1;
    background: $primary 15%;
    color: $primary;
}

TabbedContent {
    height: 1fr;
}
TabPane {
    background: $surface;
    padding: 1 2;
}
ContentTabs {
    background: $panel;
    border-bottom: solid $border;
    height: 3;
}
ContentTabs > Tab {
    padding: 0 3;
    color: $text-muted;
    background: transparent;
    text-style: bold;
    border: none;
}
ContentTabs > Tab:hover { color: $text; }
ContentTabs > Tab.-active {
    color: $primary;
    background: $surface;
    border-bottom: double $primary;
}

ChatPanel {
    height: 1fr;
    background: $surface;
}
ChatPanel > RichLog {
    height: 1fr;
    background: transparent;
    border: none;
    padding: 1 2;
}
ChatPanel > Input {
    dock: bottom;
    height: 3;
    margin: 1 2;
    background: $panel;
    border: solid $border;
    color: $text;
}
ChatPanel > Input:focus { border: solid $primary; }

WorkerTable {
    height: 1fr;
    background: transparent;
}
WorkerTable > .datatable--header {
    background: $panel;
    color: $primary;
    text-style: bold;
}

DataTable {
    background: transparent;
    border: solid $border;
}
DataTable > .datatable--header {
    background: $panel;
    color: $primary;
}
DataTable > .datatable--cursor {
    background: $primary 10%;
}

RichLog {
    background: transparent;
    scrollbar-color: $primary $panel;
    scrollbar-color-hover: $primary;
    scrollbar-color-active: $primary;
    scrollbar-size: 1 1;
}

Input {
    background: $panel;
    border: solid $border;
    color: $text;
}
Input:focus { border: solid $primary; }

Button {
    background: $panel;
    border: solid $border;
    color: $text;
}
Button:hover { border: solid $primary; background: $primary 8%; }

SelectionList {
    background: transparent;
    border: solid $border;
}
SelectionList > ListItem {
    padding: 0 2;
}
SelectionList > ListItem.-highlight {
    background: $primary 10%;
}

Tree {
    background: transparent;
}
Tree > .tree--cursor {
    background: $primary 10%;
}

ProgressBar {
    height: 1;
}

Scrollbar {
    scrollbar-color: $border;
    scrollbar-color-hover: $primary;
    scrollbar-color-active: $primary;
    scrollbar-size: 1 0;
}
"""

# High Contrast Theme — WCAG AAA compliant
HIGH_CONTRAST_THEME = """
/* ═══════════════════════════════════════════════════════════════════════
   High Contrast Theme — WCAG AAA compliant for accessibility
   ═══════════════════════════════════════════════════════════════════════ */

/* Base colors — Maximum contrast ratios */
$primary:    #00ffff;      /* pure cyan */
$secondary:  #ff00ff;      /* pure magenta */
$accent:     #00ffff;
$error:      #ff0000;      /* pure red */
$success:    #00ff00;      /* pure green */
$warning:    #ffff00;      /* pure yellow */
$surface:    #000000;      /* pure black */
$panel:      #1a1a1a;      /* very dark gray */
$boost:      #2a2a2a;      /* dark gray */
$border:     rgba(255,255,255,0.3);

$text:       #ffffff;      /* pure white */
$text-muted: #cccccc;      /* light gray */
$text-disabled: #999999;   /* medium gray */

Screen {
    background: $surface;
    color: $text;
}

Header {
    dock: top;
    height: 4;
    background: $panel;
    border-bottom: solid $primary;
    color: $text;
    text-style: bold;
}

Footer {
    dock: bottom;
    height: 1;
    background: $primary 20%;
    color: $primary;
}

TabbedContent {
    height: 1fr;
}
TabPane {
    background: $surface;
    padding: 1 2;
}
ContentTabs {
    background: $panel;
    border-bottom: solid $border;
    height: 3;
}
ContentTabs > Tab {
    padding: 0 3;
    color: $text-muted;
    background: transparent;
    text-style: bold;
    border: none;
}
ContentTabs > Tab:hover { color: $text; }
ContentTabs > Tab.-active {
    color: $surface;
    background: $primary;
    border-bottom: solid $text;
}

ChatPanel {
    height: 1fr;
    background: $surface;
}
ChatPanel > RichLog {
    height: 1fr;
    background: transparent;
    border: none;
    padding: 1 2;
}
ChatPanel > Input {
    dock: bottom;
    height: 3;
    margin: 1 2;
    background: $panel;
    border: solid $border;
    color: $text;
}
ChatPanel > Input:focus { border: solid $primary; }

WorkerTable {
    height: 1fr;
    background: transparent;
}
WorkerTable > .datatable--header {
    background: $panel;
    color: $primary;
    text-style: bold;
}

DataTable {
    background: transparent;
    border: solid $border;
}
DataTable > .datatable--header {
    background: $panel;
    color: $primary;
}
DataTable > .datatable--cursor {
    background: $primary 20%;
}

RichLog {
    background: transparent;
    scrollbar-color: $primary $panel;
    scrollbar-color-hover: $primary;
    scrollbar-color-active: $primary;
    scrollbar-size: 2 2;
}

Input {
    background: $panel;
    border: solid $border;
    color: $text;
}
Input:focus { border: solid $primary; }

Button {
    background: $panel;
    border: solid $border;
    color: $text;
}
Button:hover { border: solid $primary; background: $primary 15%; }

SelectionList {
    background: transparent;
    border: solid $border;
}
SelectionList > ListItem {
    padding: 0 2;
}
SelectionList > ListItem.-highlight {
    background: $primary 20%;
}

Tree {
    background: transparent;
}
Tree > .tree--cursor {
    background: $primary 20%;
}

ProgressBar {
    height: 2;
}

Scrollbar {
    scrollbar-color: $primary;
    scrollbar-color-hover: $text;
    scrollbar-color-active: $primary;
    scrollbar-size: 2 0;
}
"""

# Monokai Theme — Popular developer theme
MONOKAI_THEME = """
/* ═══════════════════════════════════════════════════════════════════════
   Monokai Theme — Classic developer color scheme
   ═══════════════════════════════════════════════════════════════════════ */

/* Base colors */
$primary:    #66d9ef;      /* monokai cyan */
$secondary:  #ae81ff;      /* monokai purple */
$accent:     #a6e22e;      /* monokai green */
$error:      #f92672;      /* monokai pink/red */
$success:    #a6e22e;      /* monokai green */
$warning:    #e6db74;      /* monokai yellow */
$surface:    #272822;      /* monokai bg */
$panel:      #3e3d32;      /* monokai panel */
$boost:      #49483e;      /* monokai boost */
$border:     rgba(255,255,255,0.15);

$text:       #f8f8f2;      /* monokai text */
$text-muted: #a59f93;      /* monokai muted */
$text-disabled: #75715e;   /* monokai disabled */

Screen {
    background: $surface;
    color: $text;
}

Header {
    dock: top;
    height: 4;
    background: $panel;
    border-bottom: solid $primary 50%;
    color: $text;
    text-style: bold;
}

Footer {
    dock: bottom;
    height: 1;
    background: $primary 20%;
    color: $primary;
}

TabbedContent {
    height: 1fr;
}
TabPane {
    background: $surface;
    padding: 1 2;
}
ContentTabs {
    background: $panel;
    border-bottom: solid $border;
    height: 3;
}
ContentTabs > Tab {
    padding: 0 3;
    color: $text-muted;
    background: transparent;
    text-style: bold;
    border: none;
}
ContentTabs > Tab:hover { color: $text; }
ContentTabs > Tab.-active {
    color: $surface;
    background: $primary;
    border-bottom: double $accent;
}

ChatPanel {
    height: 1fr;
    background: $surface;
}
ChatPanel > RichLog {
    height: 1fr;
    background: transparent;
    border: none;
    padding: 1 2;
}
ChatPanel > Input {
    dock: bottom;
    height: 3;
    margin: 1 2;
    background: $panel;
    border: solid $border;
    color: $text;
}
ChatPanel > Input:focus { border: solid $accent; }

WorkerTable {
    height: 1fr;
    background: transparent;
}
WorkerTable > .datatable--header {
    background: $panel;
    color: $primary;
    text-style: bold;
}

DataTable {
    background: transparent;
    border: solid $border;
}
DataTable > .datatable--header {
    background: $panel;
    color: $primary;
}
DataTable > .datatable--cursor {
    background: $primary 15%;
}

RichLog {
    background: transparent;
    scrollbar-color: $primary $panel;
    scrollbar-color-hover: $accent;
    scrollbar-color-active: $primary;
    scrollbar-size: 1 1;
}

Input {
    background: $panel;
    border: solid $border;
    color: $text;
}
Input:focus { border: solid $accent; }

Button {
    background: $panel;
    border: solid $border;
    color: $text;
}
Button:hover { border: solid $accent; background: $accent 10%; }

SelectionList {
    background: transparent;
    border: solid $border;
}
SelectionList > ListItem {
    padding: 0 2;
}
SelectionList > ListItem.-highlight {
    background: $primary 15%;
}

Tree {
    background: transparent;
}
Tree > .tree--cursor {
    background: $primary 15%;
}

ProgressBar {
    height: 1;
}

Scrollbar {
    scrollbar-color: $border;
    scrollbar-color-hover: $accent;
    scrollbar-color-active: $primary;
    scrollbar-size: 1 0;
}
"""

# Theme registry
THEMES = {
    "kazma-dark": None,  # Will be loaded from theme.py
    "light": LIGHT_THEME,
    "high-contrast": HIGH_CONTRAST_THEME,
    "monokai": MONOKAI_THEME,
}


class ThemeManager:
    """Manage theme switching and user preferences.
    
    Usage:
        manager = ThemeManager()
        manager.apply_theme(app, "monokai")
    """
    
    CONFIG_DIR = Path.home() / ".kazma"
    CONFIG_FILE = CONFIG_DIR / "preferences.json"
    
    DEFAULT_PREFERENCES = {
        "theme": "kazma-dark",
        "font_size": "medium",
        "auto_scroll": True,
        "animations_enabled": True,
        "language": "en",
    }
    
    def __init__(self) -> None:
        self._preferences = self.DEFAULT_PREFERENCES.copy()
        self.load()
    
    @property
    def current_theme(self) -> str:
        """Get current theme name."""
        return self._preferences["theme"]
    
    @property
    def font_size(self) -> str:
        """Get current font size setting."""
        return self._preferences["font_size"]
    
    @property
    def auto_scroll(self) -> bool:
        """Get auto-scroll setting."""
        return self._preferences["auto_scroll"]
    
    @property
    def animations_enabled(self) -> bool:
        """Get animations enabled setting."""
        return self._preferences["animations_enabled"]

    @property
    def language(self) -> str:
        """Get bilingual localization language setting (en or ar)."""
        return self._preferences.get("language", "en")

    def set_language(self, lang: str) -> None:
        """Set bilingual language and save preferences."""
        if lang not in ("en", "ar"):
            raise ValueError(f"Invalid language: {lang}. Valid: en, ar")
        self._preferences["language"] = lang
        self.save()
    
    def load(self) -> None:
        """Load preferences from config file."""
        if self.CONFIG_FILE.exists():
            try:
                with open(self.CONFIG_FILE, "r") as f:
                    saved = json.load(f)
                    self._preferences.update(saved)
            except (json.JSONDecodeError, IOError):
                pass  # Use defaults on error
    
    def save(self) -> None:
        """Save preferences to config file."""
        self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.CONFIG_FILE, "w") as f:
            json.dump(self._preferences, f, indent=2)
    
    def set_theme(self, theme_name: str) -> None:
        """Set theme name and save preferences."""
        if theme_name not in THEMES:
            raise ValueError(f"Unknown theme: {theme_name}. Available: {list(THEMES.keys())}")
        self._preferences["theme"] = theme_name
        self.save()
    
    def set_font_size(self, size: str) -> None:
        """Set font size and save preferences."""
        valid_sizes = ["small", "medium", "large", "xlarge"]
        if size not in valid_sizes:
            raise ValueError(f"Invalid font size: {size}. Valid: {valid_sizes}")
        self._preferences["font_size"] = size
        self.save()
    
    def set_auto_scroll(self, enabled: bool) -> None:
        """Set auto-scroll preference."""
        self._preferences["auto_scroll"] = enabled
        self.save()
    
    def set_animations(self, enabled: bool) -> None:
        """Set animations preference."""
        self._preferences["animations_enabled"] = enabled
        self.save()
    
    def apply_theme(self, app: App, theme_name: str | None = None) -> None:
        """Apply theme to the app.
        
        Args:
            app: The Textual app instance
            theme_name: Theme name or None to use saved preference
        """
        theme_name = theme_name or self.current_theme
        
        if theme_name == "kazma-dark":
            from kazma_tui.theme import KAZMA_THEME
            css = KAZMA_THEME
        else:
            css = THEMES.get(theme_name)
            if css is None:
                raise ValueError(f"Unknown theme: {theme_name}")
        
        if self.language == "ar":
            css = css + "\n" + RTL_CSS_OVERRIDES

        # Build a fresh Stylesheet with the new theme CSS + all widget
        # DEFAULT_CSS.  We do NOT pass the old app variables because the
        # new theme redefines them (passing old + new causes parse errors
        # on some themes like high-contrast).
        from textual.css.stylesheet import Stylesheet
        
        new_stylesheet = Stylesheet()
        # Re-add all widget default CSS (preserves widget styling)
        for read_from, css_text, tie_breaker, scope in app._get_default_css():
            new_stylesheet.add_source(
                css_text,
                read_from=read_from,
                is_default_css=True,
                tie_breaker=tie_breaker,
                scope=scope,
              )
        # Add the new theme CSS (replaces the old App.CSS with new variables)
        new_stylesheet.add_source(css)
        app.stylesheet = new_stylesheet
        # refresh_css() sets the app's CSS variables (framework defaults
        # + theme variables) and re-parses the stylesheet.  This is the
        # correct way to apply a new stylesheet in Textual 8.x.
        app.refresh_css()
    
    def get_available_themes(self) -> list[str]:
        """Get list of available theme names."""
        return list(THEMES.keys())


RTL_CSS_OVERRIDES = """
/* ═══════════════════════════════════════════════════════════════════════
   Arabic RTL Mode Styling Overrides
   ═══════════════════════════════════════════════════════════════════════ */

Screen.rtl-mode Label, Screen.rtl-mode Static, Screen.rtl-mode Input, Screen.rtl-mode Button, Screen.rtl-mode ListItem, Screen.rtl-mode Option {
    text-align: right;
}
Screen.rtl-mode Header {
    text-align: right;
}
Screen.rtl-mode Footer {
    text-align: right;
}
Screen.rtl-mode TabPane {
    align: right top;
}
Screen.rtl-mode ContentTabs {
    align: right top;
}
"""
