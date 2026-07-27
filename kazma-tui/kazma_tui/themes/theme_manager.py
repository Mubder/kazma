"""Kazma TUI Theme Manager — Multiple professional themes (v2 shell-aligned).

Alternate themes only override color tokens; structural CSS comes from
``kazma_tui.theme.KAZMA_SHELL_CSS`` so light/monokai/HC match the dark shell.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from textual.app import App

__all__ = [
    "HIGH_CONTRAST_THEME",
    "LIGHT_THEME",
    "MONOKAI_THEME",
    "RTL_CSS_OVERRIDES",
    "THEMES",
    "ThemeManager",
    "compose_theme",
]


def compose_theme(vars_css: str) -> str:
    """Vars + shared v2 shell rules."""
    from kazma_tui.theme import KAZMA_SHELL_CSS

    return (vars_css or "") + "\n" + KAZMA_SHELL_CSS


# ── Token-only themes (structure from KAZMA_SHELL_CSS) ──────────────────

LIGHT_VARS = """
/* Light — daytime high readability */
$primary:    #0e7490;
$secondary:  #7c3aed;
$accent:     #0e7490;
$error:      #dc2626;
$success:    #059669;
$warning:    #d97706;
$surface:    #f8fafc;
$panel:      #ffffff;
$boost:      #f1f5f9;
$border:     #cbd5e1;
$border-dim: #e2e8f0;
$text:       #0f172a;
$text-muted: #475569;
$text-disabled: #94a3b8;
$screen-selection-background: rgba(14,116,144,0.18);
$screen-selection-foreground: #0f172a;
"""

HIGH_CONTRAST_VARS = """
/* High contrast — WCAG-oriented */
$primary:    #00e5ff;
$secondary:  #ff80ff;
$accent:     #00e5ff;
$error:      #ff5555;
$success:    #55ff55;
$warning:    #ffff55;
$surface:    #000000;
$panel:      #121212;
$boost:      #1e1e1e;
$border:     #ffffff;
$border-dim: #aaaaaa;
$text:       #ffffff;
$text-muted: #dddddd;
$text-disabled: #aaaaaa;
$screen-selection-background: rgba(0,229,255,0.35);
$screen-selection-foreground: #ffffff;
"""

MONOKAI_VARS = """
/* Monokai-inspired console */
$primary:    #a6e22e;
$secondary:  #ae81ff;
$accent:     #66d9ef;
$error:      #f92672;
$success:    #a6e22e;
$warning:    #e6db74;
$surface:    #272822;
$panel:      #1e1f1c;
$boost:      #3e3d32;
$border:     #49483e;
$border-dim: #3e3d32;
$text:       #f8f8f2;
$text-muted: #cfcfc2;
$text-disabled: #75715e;
$screen-selection-background: rgba(166,226,46,0.22);
$screen-selection-foreground: #f8f8f2;
"""

# Backward-compatible full theme strings (vars + shell)
LIGHT_THEME = compose_theme(LIGHT_VARS)
HIGH_CONTRAST_THEME = compose_theme(HIGH_CONTRAST_VARS)
MONOKAI_THEME = compose_theme(MONOKAI_VARS)

THEMES = {
    "kazma-dark": None,
    "light": LIGHT_THEME,
    "high-contrast": HIGH_CONTRAST_THEME,
    "monokai": MONOKAI_THEME,
}

RTL_CSS_OVERRIDES = """
/* Arabic / RTL soft overrides — chrome stays LTR; logs may use bidi text */
"""


def _preferences_paths() -> tuple[Path, Path]:
    """Return (config_dir, preferences_file) under project-local Kazma home."""
    try:
        from kazma_core.paths import preferences_path, user_home

        prefs = preferences_path()
        return prefs.parent, prefs
    except Exception:
        d = Path.home() / ".kazma"
        return d, d / "preferences.json"


class ThemeManager:
    """Manage theme switching and user preferences."""

    DEFAULT_PREFERENCES = {
        "theme": "kazma-dark",
        "font_size": "medium",
        "auto_scroll": True,
        "animations_enabled": True,
        "language": "en",
    }

    def __init__(self) -> None:
        self.CONFIG_DIR, self.CONFIG_FILE = _preferences_paths()
        self._preferences = self.DEFAULT_PREFERENCES.copy()
        self.load()

    @property
    def current_theme(self) -> str:
        return self._preferences.get("theme", "kazma-dark")

    @property
    def language(self) -> str:
        return self._preferences.get("language", "en")

    @property
    def auto_scroll(self) -> bool:
        """Chat/log auto-scroll preference (Settings panel)."""
        return bool(self._preferences.get("auto_scroll", True))

    @property
    def animations_enabled(self) -> bool:
        """UI animation preference (Settings panel)."""
        return bool(self._preferences.get("animations_enabled", True))

    @property
    def font_size(self) -> str:
        return str(self._preferences.get("font_size", "medium") or "medium")

    def set_auto_scroll(self, enabled: bool) -> None:
        self._preferences["auto_scroll"] = bool(enabled)
        self.save()

    def set_animations_enabled(self, enabled: bool) -> None:
        self._preferences["animations_enabled"] = bool(enabled)
        self.save()

    def load(self) -> None:
        try:
            if self.CONFIG_FILE.exists():
                data = json.loads(self.CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._preferences.update(
                        {k: v for k, v in data.items() if k in self.DEFAULT_PREFERENCES}
                    )
        except Exception:
            pass

    def save(self) -> None:
        try:
            self.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            self.CONFIG_FILE.write_text(
                json.dumps(self._preferences, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def set_theme(self, theme_name: str) -> None:
        if theme_name not in THEMES and theme_name != "kazma-dark":
            raise ValueError(f"Unknown theme: {theme_name}")
        self._preferences["theme"] = theme_name
        self.save()

    def set_language(self, lang: str) -> None:
        self._preferences["language"] = lang if lang in ("en", "ar") else "en"
        self.save()

    def apply_theme(self, app: App, theme_name: str | None = None) -> None:
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

        from textual.css.stylesheet import Stylesheet

        new_stylesheet = Stylesheet()
        for key, source in app.stylesheet.source.items():
            if source.is_defaults:
                new_stylesheet.add_source(
                    source.content,
                    read_from=key,
                    is_default_css=True,
                    tie_breaker=source.tie_breaker,
                    scope=source.scope,
                )
        new_stylesheet.add_source(css)
        app.stylesheet = new_stylesheet
        try:
            app.refresh_css(animate=False)
        except TypeError:
            app.refresh_css()
        try:
            app.screen.refresh(layout=True)
        except Exception:
            pass

    def list_themes(self) -> list[str]:
        return list(THEMES.keys())
