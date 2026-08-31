"""CSS parse + headless mount coverage for the TUI.

The launch crash (``hatch: right 12%`` → ``Expected a color``) is invisible
to tests that only grep the theme string. Parse every stylesheet with
Textual, then mount the real app and switch every tab.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from textual.css.stylesheet import Stylesheet, StylesheetParseError
from textual.widgets import TabbedContent

from kazma_tui.theme import KAZMA_THEME
from kazma_tui.themes.theme_manager import (
    HIGH_CONTRAST_THEME,
    LIGHT_THEME,
    MONOKAI_THEME,
    RTL_CSS_OVERRIDES,
)


_TAB_IDS = (
    "dashboard",
    "memory",
    "chat",
    "files",
    "traces",
    "swarm",
    "settings",
    "documents",
)


def _parse(css: str, *, label: str) -> None:
    ss = Stylesheet()
    ss.add_source(css, read_from=("", label))
    ss.parse()


def _widget_css_sources() -> list[tuple[str, str]]:
    """Every DEFAULT_CSS / HIGH_CONTRAST_CSS string defined in kazma_tui."""
    from kazma_tui import (
        chat,
        dashboard,
        documents,
        editor,
        files,
        footer,
        header,
        memory_panel,
        nav_rail,
        settings_panel,
        swarm,
        traces,
    )
    from kazma_tui.widgets import (
        accessibility,
        command_bar,
        command_palette,
        confirm_dialog,
        hitl_modal,
        log_stream,
        model_picker,
        sparkline,
        status_bar,
        toast,
        tutorial,
    )

    modules = [
        chat,
        dashboard,
        documents,
        editor,
        files,
        footer,
        header,
        memory_panel,
        nav_rail,
        settings_panel,
        swarm,
        traces,
        accessibility,
        command_bar,
        command_palette,
        confirm_dialog,
        hitl_modal,
        log_stream,
        model_picker,
        sparkline,
        status_bar,
        toast,
        tutorial,
    ]
    found: list[tuple[str, str]] = []
    for mod in modules:
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if getattr(obj, "__module__", "") != mod.__name__:
                continue
            # Skip inherited Textual DEFAULT_CSS ($background / $foreground).
            if "DEFAULT_CSS" not in getattr(obj, "__dict__", {}):
                css = None
            else:
                css = getattr(obj, "DEFAULT_CSS", None)
            if isinstance(css, str) and css.strip():
                found.append((f"{mod.__name__}.{name}.DEFAULT_CSS", css))
            hc = getattr(obj, "HIGH_CONTRAST_CSS", None)
            if isinstance(hc, str) and hc.strip():
                found.append((f"{mod.__name__}.{name}.HIGH_CONTRAST_CSS", hc))
    return found


class TestThemeCssParses:
    def test_kazma_theme_parses(self) -> None:
        _parse(KAZMA_THEME, label="KAZMA_THEME")

    def test_alternate_themes_parse(self) -> None:
        _parse(LIGHT_THEME, label="LIGHT_THEME")
        _parse(HIGH_CONTRAST_THEME, label="HIGH_CONTRAST_THEME")
        _parse(MONOKAI_THEME, label="MONOKAI_THEME")
        _parse(KAZMA_THEME + "\n" + RTL_CSS_OVERRIDES, label="KAZMA_THEME+RTL")

    def test_every_widget_default_css_parses(self) -> None:
        sources = _widget_css_sources()
        assert len(sources) >= 20, f"expected widget CSS, got {len(sources)}"
        # Widget CSS uses $tokens from the app theme; prepend dark vars so
        # unknown-token parse is not a false pass/fail.
        from kazma_tui.theme import KAZMA_DARK_VARS

        for label, css in sources:
            try:
                _parse(KAZMA_DARK_VARS + "\n" + css, label=label)
            except Exception as exc:
                raise AssertionError(f"{label} failed CSS parse") from exc

    def test_bare_hatch_percentage_is_invalid(self) -> None:
        """Negative control: the launch-crash CSS must fail parse."""
        ss = Stylesheet()
        ss.add_source("Screen { hatch: right 12%; }")
        with pytest.raises(StylesheetParseError):
            ss.parse()

    def test_hatch_character_plus_color_is_valid(self) -> None:
        ss = Stylesheet()
        ss.add_source("$panel: #11171f;\nScreen { hatch: right $panel; }")
        ss.parse()


class TestAppMount:
    @pytest.mark.asyncio
    async def test_app_mounts_and_every_tab_activates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefs = tmp_path / "preferences.json"
        prefs.write_text("{}", encoding="utf-8")

        def _prefs() -> Path:
            return prefs

        monkeypatch.setattr("kazma_core.paths.preferences_path", _prefs)

        from kazma_tui.app import KazmaTUI

        async with KazmaTUI().run_test(size=(120, 40)) as pilot:
            tabs = pilot.app.query_one("#main-tabs", TabbedContent)
            for tab_id in _TAB_IDS:
                tabs.active = tab_id
                await pilot.pause()
                assert tabs.active == tab_id

            # EditorScreen applies Screen.-maximized-view (the original crash).
            from kazma_tui.editor import EditorScreen

            await pilot.app.push_screen(
                EditorScreen(rel_path="README.md", workspace_root=Path.cwd())
            )
            await pilot.pause()
            assert isinstance(pilot.app.screen, EditorScreen)
            await pilot.app.pop_screen()
            await pilot.pause()
            assert not isinstance(pilot.app.screen, EditorScreen)

    @pytest.mark.asyncio
    async def test_nav_rail_switches_tabs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefs = tmp_path / "preferences.json"
        prefs.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("kazma_core.paths.preferences_path", lambda: prefs)

        from kazma_tui.app import KazmaTUI

        async with KazmaTUI().run_test(size=(120, 40)) as pilot:
            tabs = pilot.app.query_one("#main-tabs", TabbedContent)
            for tab_id in ("chat", "files", "documents", "dashboard"):
                pilot.app.action_goto_tab(tab_id)
                await pilot.pause()
                assert tabs.active == tab_id, f"goto_tab({tab_id!r}) left active={tabs.active!r}"

    @pytest.mark.asyncio
    async def test_command_palette_and_help_do_not_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prefs = tmp_path / "preferences.json"
        prefs.write_text("{}", encoding="utf-8")
        monkeypatch.setattr("kazma_core.paths.preferences_path", lambda: prefs)

        from kazma_tui.app import KazmaTUI
        from kazma_tui.widgets.command_bar import CommandConsole
        from kazma_tui.widgets.command_palette import CommandPalette
        from kazma_tui.widgets.toast import Toast

        async with KazmaTUI().run_test(size=(120, 40)) as pilot:
            pilot.app.action_command_palette()
            await pilot.pause()
            assert isinstance(pilot.app.screen, CommandPalette)
            await pilot.app.pop_screen()
            await pilot.pause()
            # `:` overlay — its CSS used to swap align axes and fail parse.
            pilot.app.action_command_bar()
            await pilot.pause()
            assert isinstance(pilot.app.screen, CommandConsole)
            await pilot.app.pop_screen()
            await pilot.pause()
            pilot.app.action_help_screen()
            await pilot.pause()
            assert isinstance(pilot.app.screen, Toast)
