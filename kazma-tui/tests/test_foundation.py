"""Tests for TUI foundation: package structure, app class, and entry point."""

from __future__ import annotations

from pathlib import Path


class TestPackageStructure:
    """Verify kazma_tui package structure exists and is importable."""

    def test_init_exists(self) -> None:
        """__init__.py must exist in kazma_tui package."""
        init_path = Path(__file__).resolve().parent.parent / "kazma_tui" / "__init__.py"
        assert init_path.is_file(), f"Missing {init_path}"

    def test_init_has_version(self) -> None:
        """__init__.py must export __version__."""
        import kazma_tui

        assert hasattr(kazma_tui, "__version__"), "kazma_tui must define __version__"
        assert isinstance(kazma_tui.__version__, str)
        assert len(kazma_tui.__version__) > 0

    def test_main_exists(self) -> None:
        """__main__.py must exist in kazma_tui package."""
        main_path = Path(__file__).resolve().parent.parent / "kazma_tui" / "__main__.py"
        assert main_path.is_file(), f"Missing {main_path}"

    def test_app_exists(self) -> None:
        """app.py must exist in kazma_tui package."""
        app_path = Path(__file__).resolve().parent.parent / "kazma_tui" / "app.py"
        assert app_path.is_file(), f"Missing {app_path}"


class TestKazmaTUIApp:
    """Verify KazmaTUI Textual App class."""

    def test_app_class_exists(self) -> None:
        """KazmaTUI class must be importable from kazma_tui.app."""
        from kazma_tui.app import KazmaTUI

        assert KazmaTUI is not None

    def test_app_is_textual_app(self) -> None:
        """KazmaTUI must be a subclass of textual.app.App."""
        from kazma_tui.app import KazmaTUI
        from textual.app import App

        assert issubclass(KazmaTUI, App), "KazmaTUI must inherit from textual.app.App"

    def test_app_has_main_function(self) -> None:
        """kazma_tui.app must expose a main() function."""
        from kazma_tui.app import main

        assert callable(main), "main must be callable"

    def test_app_compose_returns_widgets(self) -> None:
        """KazmaTUI.compose() must yield HeaderProviderModel, FooterShortcuts, and at least one placeholder widget."""
        from kazma_tui.app import KazmaTUI

        app = KazmaTUI()
        # Call compose to get the widgets
        widgets = list(app.compose())
        widget_types = [type(w).__name__ for w in widgets]

        assert "HeaderProviderModel" in widget_types, f"HeaderProviderModel not found in compose output: {widget_types}"
        assert "FooterShortcuts" in widget_types, f"FooterShortcuts not found in compose output: {widget_types}"
        # Must have at least one placeholder beyond Header/Footer
        assert len(widgets) >= 3, f"Expected at least 3 widgets (Header + placeholder + Footer), got {len(widgets)}"

    def test_app_title(self) -> None:
        """KazmaTUI must have a non-empty TITLE."""
        from kazma_tui.app import KazmaTUI

        assert hasattr(KazmaTUI, "TITLE"), "KazmaTUI must define TITLE"
        assert KazmaTUI.TITLE, "TITLE must not be empty"


class TestMainEntryPoint:
    """Verify __main__.py entry point behavior."""

    def test_main_module_calls_main(self) -> None:
        """__main__.py must call main() from kazma_tui.app."""
        main_path = Path(__file__).resolve().parent.parent / "kazma_tui" / "__main__.py"
        content = main_path.read_text(encoding="utf-8")
        assert "main()" in content, "__main__.py must call main()"


class TestPyprojectEntryPoint:
    """Verify pyproject.toml entry point is updated."""

    def test_entry_point_updated(self) -> None:
        """kazma-tui entry point must point to kazma_tui.app:main."""
        pyproject_path = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        assert "kazma_tui.app:main" in content, (
            f"pyproject.toml must have kazma-tui = 'kazma_tui.app:main', found: {content}"
        )
        # Must NOT reference old entry point
        assert "kazma_tui.tui:main" not in content, "Old entry point kazma_tui.tui:main must be removed"
