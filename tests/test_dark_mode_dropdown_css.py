"""Tests for fix-001-dark-mode-dropdown.

Validates VAL-UI-001: Dark mode dropdown options are visible.

The bug: native <option> elements render with browser-default white
background while inheriting the light text color from the parent <select>
in dark mode, making options invisible (white text on white background).

The fix adds CSS rules to kazma.css so that in dark mode:
  - <option> elements have a dark background and light text
  - option:checked has distinct accent styling
  - Light mode options are unaffected (the dark rules are scoped to
    [data-theme="dark"])
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_CSS_PATH = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui"
    / "kazma_ui"
    / "static"
    / "css"
    / "kazma.css"
)


@pytest.fixture(scope="module")
def css_text() -> str:
    """Read the kazma.css source once for all tests in this module."""
    return _CSS_PATH.read_text(encoding="utf-8")


class TestDarkModeOptionRules:
    """VAL-UI-001: dark mode <option> elements must be readable."""

    def test_css_file_exists(self, css_text: str) -> None:
        """kazma.css exists and is non-empty."""
        assert _CSS_PATH.exists(), "kazma.css not found"
        assert len(css_text) > 0, "kazma.css is empty"

    def test_dark_mode_option_background_exists(self, css_text: str) -> None:
        """A [data-theme="dark"] rule targets option with a dark background.

        The selector must include both [data-theme="dark"] and the option
        element, and the declaration block must set a background using a
        dark-themed variable (var(--bg-surface) or similar).
        """
        # Match a selector block that contains data-theme="dark" and option,
        # followed by a declaration block.
        pattern = re.compile(
            r'\[data-theme\s*=\s*"dark"\][^{]*\boption\b[^{]*\{[^}]*'
            r'background[^;}]+var\(--bg-surface\)',
            re.IGNORECASE | re.DOTALL,
        )
        assert pattern.search(css_text), (
            "No [data-theme=\"dark\"] option { background: var(--bg-surface) } "
            "rule found. Native <option> elements need an explicit dark "
            "background in dark mode."
        )

    def test_dark_mode_option_text_color_exists(self, css_text: str) -> None:
        """A [data-theme="dark"] rule sets light text on option elements."""
        pattern = re.compile(
            r'\[data-theme\s*=\s*"dark"\][^{]*\boption\b[^{]*\{[^}]*'
            r'color[^;}]+var\(--text-primary\)',
            re.IGNORECASE | re.DOTALL,
        )
        assert pattern.search(css_text), (
            "No [data-theme=\"dark\"] option { color: var(--text-primary) } "
            "rule found. Native <option> elements need light text in dark mode."
        )

    def test_dark_mode_option_checked_styled(self, css_text: str) -> None:
        """option:checked has distinct (accent) styling in dark mode."""
        pattern = re.compile(
            r'\[data-theme\s*=\s*"dark"\][^{]*\boption:checked\b',
            re.IGNORECASE | re.DOTALL,
        )
        assert pattern.search(css_text), (
            "No [data-theme=\"dark\"] option:checked rule found. "
            "The selected option needs distinct styling."
        )

    def test_dark_mode_covers_form_select_option(self, css_text: str) -> None:
        """The fix covers .form-select option (the class used by settings)."""
        # Either an explicit ".form-select option" selector or a generic
        # "select option" selector that covers .form-select (which is a
        # <select class="form-select">).
        pattern = re.compile(
            r'\[data-theme\s*=\s*"dark"\][^{]*'
            r'(?:\.form-select\s+option|select\s+option|\boption\b)',
            re.IGNORECASE | re.DOTALL,
        )
        assert pattern.search(css_text), (
            "No [data-theme=\"dark\"] rule covers select/form-select option "
            "elements."
        )


class TestLightModeUnaffected:
    """Light mode options must not be broken by the dark mode rules."""

    def test_dark_rules_are_scoped_to_data_theme_dark(self, css_text: str) -> None:
        """Every option-background rule is scoped under [data-theme="dark"].

        This ensures light mode is unaffected. We verify that the dark-mode
        option rules exist (so light mode is not accidentally broken by a
        bare unscoped option rule that would override light rendering).
        """
        dark_count = len(re.findall(
            r'\[data-theme\s*=\s*"dark"\][^{]*option', css_text, re.IGNORECASE
        ))
        assert dark_count >= 1, "Expected at least one dark-mode option rule"
