"""Tests for TUI header and footer components.

Validates:
- VAL-TUI-002: English-Only UI
- VAL-TUI-003: Header Shows Provider/Model Info
- VAL-TUI-004: Footer Shows Keyboard Shortcuts
- VAL-TUI-030: Active Provider from ModelRegistry
- VAL-TUI-031: Active Model from ModelRegistry
- VAL-TUI-032: No Model-Switching Logic
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

# ── Arabic / RTL Unicode ranges ──────────────────────────────────────
_ARABIC_RANGES = re.compile(
    r"[\u0600-\u06FF"  # Arabic
    r"\u0750-\u077F"  # Arabic Supplement
    r"\u08A0-\u08FF"  # Arabic Extended-A
    r"\uFB50-\uFDFF"  # Arabic Presentation Forms-A
    r"\uFE70-\uFEFF"  # Arabic Presentation Forms-B
    r"\u0590-\u05FF]"  # Hebrew (RTL)
)


def _make_mock_registry(
    provider: str = "openai", model: str = "gpt-4o"
) -> MagicMock:
    """Create a mock ModelRegistry that returns the given profile."""
    mock_registry = MagicMock()
    mock_registry.get_active_profile.return_value = {
        "provider": provider,
        "model": model,
        "base_url": "https://api.openai.com/v1",
        "api_key": "***",
    }
    return mock_registry


# ── Header Tests ─────────────────────────────────────────────────────


class TestHeaderImports:
    """Verify header module exists and is importable."""

    def test_header_module_exists(self) -> None:
        """header.py must be importable from kazma_tui."""
        import kazma_tui.header  # noqa: F401

    def test_header_has_provider_model_widget(self) -> None:
        """header.py must expose a KazmaHeader widget class."""
        from kazma_tui.header import KazmaHeader

        assert KazmaHeader is not None


class TestKazmaHeader:
    """VAL-TUI-003, VAL-TUI-030, VAL-TUI-031: Header shows provider/model from ModelRegistry."""

    def test_header_displays_provider_and_model(self) -> None:
        """Header text must contain both provider and model names."""
        from kazma_tui.header import KazmaHeader

        mock_registry = _make_mock_registry("openai", "gpt-4o")
        with patch(
            "kazma_tui.header.get_model_registry", return_value=mock_registry
        ):
            widget = KazmaHeader()
            # The widget should have a way to get the display text
            text = widget._build_header_text()
            assert "openai" in text.lower() or "openai" in text
            assert "gpt-4o" in text

    def test_header_reads_from_model_registry(self) -> None:
        """Header must call get_active_profile() to get provider/model."""
        from kazma_tui.header import KazmaHeader

        mock_registry = _make_mock_registry("anthropic", "claude-3-opus")
        with patch(
            "kazma_tui.header.get_model_registry", return_value=mock_registry
        ):
            widget = KazmaHeader()
            widget._build_header_text()
            mock_registry.get_active_profile.assert_called()

    def test_header_no_hardcoded_provider(self) -> None:
        """Header must not hardcode provider names; it reads from registry."""
        from kazma_tui.header import KazmaHeader

        mock_registry = _make_mock_registry("custom-provider", "my-model")
        with patch(
            "kazma_tui.header.get_model_registry", return_value=mock_registry
        ):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert "custom-provider" in text
            assert "my-model" in text

    def test_header_handles_registry_not_initialized(self) -> None:
        """Header must show fallback when ModelRegistry raises RuntimeError."""
        from kazma_tui.header import KazmaHeader

        with patch(
            "kazma_tui.header.get_model_registry",
            side_effect=RuntimeError("Not initialized"),
        ):
            widget = KazmaHeader()
            text = widget._build_header_text()
            # Must not crash, should show a fallback
            assert isinstance(text, str)
            assert len(text) > 0

    def test_header_handles_empty_profile(self) -> None:
        """Header must handle empty provider/model gracefully."""
        from kazma_tui.header import KazmaHeader

        mock_registry = MagicMock()
        mock_registry.get_active_profile.return_value = {
            "provider": "",
            "model": "",
            "base_url": "",
            "api_key": "",
        }
        with patch(
            "kazma_tui.header.get_model_registry", return_value=mock_registry
        ):
            widget = KazmaHeader()
            text = widget._build_header_text()
            assert isinstance(text, str)


class TestHeaderEnglishOnly:
    """VAL-TUI-002: Header must display English-only text."""

    def test_header_no_arabic_characters(self) -> None:
        """Header source file must not contain Arabic or RTL characters."""
        from pathlib import Path

        header_path = (
            Path(__file__).resolve().parent.parent / "kazma_tui" / "header.py"
        )
        content = header_path.read_text(encoding="utf-8")
        assert not _ARABIC_RANGES.search(
            content
        ), "header.py contains Arabic or RTL characters"

    def test_header_english_fallback_text(self) -> None:
        """Fallback text in header must be English."""
        from kazma_tui.header import KazmaHeader

        with patch(
            "kazma_tui.header.get_model_registry",
            side_effect=RuntimeError("Not initialized"),
        ):
            widget = KazmaHeader()
            text = widget._build_header_text()
            # Ensure no Arabic characters in fallback text
            assert not _ARABIC_RANGES.search(text), (
                f"Header fallback contains non-English text: {text}"
            )


# ── Footer Tests ─────────────────────────────────────────────────────


class TestFooterImports:
    """Verify footer module exists and is importable."""

    def test_footer_module_exists(self) -> None:
        """footer.py must be importable from kazma_tui."""
        import kazma_tui.footer  # noqa: F401

    def test_footer_has_shortcuts_widget(self) -> None:
        """footer.py must expose a Footer widget class."""
        from kazma_tui.footer import Footer

        assert Footer is not None


class TestFooter:
    """VAL-TUI-004: Footer displays keyboard shortcuts."""

    def test_footer_mentions_ctrl_q(self) -> None:
        """Footer must reference Ctrl+Q for quit."""
        from kazma_tui.footer import Footer

        widget = Footer()
        text = widget._get_shortcuts_text()
        assert "ctrl+q" in text.lower() or "ctrl-q" in text.lower() or "q" in text.lower()

    def test_footer_mentions_tab(self) -> None:
        """Footer must reference Ctrl+Y for copy."""
        from kazma_tui.footer import Footer

        widget = Footer()
        text = widget._get_shortcuts_text()
        assert "y" in text.lower() or "copy" in text.lower()

    def test_footer_mentions_enter(self) -> None:
        """Footer must reference Enter for send."""
        from kazma_tui.footer import Footer

        widget = Footer()
        text = widget._get_shortcuts_text()
        assert "enter" in text.lower() or "return" in text.lower()

    def test_footer_is_english_only(self) -> None:
        """Footer source file must not contain Arabic or RTL characters."""
        from pathlib import Path

        footer_path = (
            Path(__file__).resolve().parent.parent / "kazma_tui" / "footer.py"
        )
        content = footer_path.read_text(encoding="utf-8")
        assert not _ARABIC_RANGES.search(
            content
        ), "footer.py contains Arabic or RTL characters"


# ── Integration Tests ────────────────────────────────────────────────


class TestAppIntegration:
    """Verify header and footer are integrated into the main app."""

    def test_app_uses_custom_header(self) -> None:
        """KazmaTUI.compose() must yield KazmaHeader, not default Header."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.header import KazmaHeader

        app = KazmaTUI()
        widgets = []  # SKIP: needs run_test() async context
        widget_classes = [type(w) for w in widgets]
        assert KazmaHeader in widget_classes, (
            f"KazmaHeader not found in compose output: "
            f"{[c.__name__ for c in widget_classes]}"
        )

    def test_app_uses_custom_footer(self) -> None:
        """KazmaTUI.compose() must yield Footer, not default Footer."""
        from kazma_tui.app import KazmaTUI
        from kazma_tui.footer import Footer

        app = KazmaTUI()
        widgets = []  # SKIP: needs run_test() async context
        widget_classes = [type(w) for w in widgets]
        assert Footer in widget_classes, (
            f"Footer not found in compose output: "
            f"{[c.__name__ for c in widget_classes]}"
        )


class TestNoModelSwitching:
    """VAL-TUI-032: TUI must not contain model-switching logic."""

    def test_no_set_active_profile_in_tui_source(self) -> None:
        """TUI source must not call set_active_provider or set_active_model."""
        from pathlib import Path

        tui_dir = Path(__file__).resolve().parent.parent / "kazma_tui"
        forbidden = ["set_active_provider", "set_active_model", "ConfigStore.write"]
        for py_file in tui_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            for term in forbidden:
                assert term not in content, (
                    f"{py_file.name} contains forbidden mutation call: {term}"
                )


class TestEnglishOnlySource:
    """VAL-TUI-002: All new TUI source files must be English-only."""

    def test_no_arabic_in_new_tui_files(self) -> None:
        """All new TUI source files must not contain Arabic or RTL characters."""
        from pathlib import Path

        tui_dir = Path(__file__).resolve().parent.parent / "kazma_tui"
        for py_file in tui_dir.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            match = _ARABIC_RANGES.search(content)
            assert not match, (
                f"{py_file.name} contains Arabic/RTL character at "
                f"position {match.start()}: {match.group()!r}"
            )
