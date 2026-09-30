"""The TUI names its pages in the web UI's words, and in the chosen language.

The TUI called the Dashboard "لوحة القيادة" where the web UI says "لوحة
التحكم" -- the docs had recorded it as "worth aligning eventually" -- and its
navigation rail stayed in English on an Arabic screen. TAB_LABELS
(kazma_tui/nav_rail.py) is now the one table the tabs and the rail read.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from kazma_tui.nav_rail import NAV_ITEMS, TAB_LABELS, NavRail
from kazma_ui.i18n import TRANSLATIONS
from textual.widgets import TabbedContent

#: Tabs that keep a word of their own, and why.
OWN_WORD = {
    "swarm": "the web UI's nav label is its one outlier; its pages say السرب (swarm.title)",
    "files": "the web UI has no page of that name",
    "traces": "the web UI has no page of that name",
}

TAB_IDS = [tab_id for tab_id, _label, _key in NAV_ITEMS]


def test_every_tab_has_a_label_in_every_language():
    for lang in ("en", "ar"):
        assert list(TAB_LABELS[lang]) == TAB_IDS, lang
    assert TAB_LABELS["en"] == {tab_id: label for tab_id, label, _key in NAV_ITEMS}


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_a_page_the_web_ui_has_takes_its_word(lang):
    for tab_id in TAB_IDS:
        if tab_id not in OWN_WORD:
            assert TAB_LABELS[lang][tab_id] == TRANSLATIONS[f"nav.{tab_id}"][lang], tab_id


def test_the_swarm_tab_says_what_the_web_says_for_the_swarm():
    assert TAB_LABELS["ar"]["swarm"] in TRANSLATIONS["swarm.title"]["ar"]
    assert set(OWN_WORD) <= set(TAB_IDS)


async def _labels_on_screen(tmp_path: Path, monkeypatch, prefs: dict) -> tuple[str, str]:
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps(prefs), encoding="utf-8")
    monkeypatch.setattr("kazma_core.paths.preferences_path", lambda: path)
    from kazma_tui.app import KazmaTUI

    app = KazmaTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        app.update_localization()
        await pilot.pause()
        tab = app.query_one("#main-tabs", TabbedContent).get_tab("dashboard")
        rail_button = app.query_one(NavRail).query_one("#nav-dashboard")
        return str(tab.label), str(rail_button.label)


@pytest.mark.asyncio
async def test_an_arabic_screen_shows_arabic_tabs_and_rail(tmp_path, monkeypatch):
    tab, rail = await _labels_on_screen(tmp_path, monkeypatch, {"language": "ar"})
    assert tab == TAB_LABELS["ar"]["dashboard"]
    assert TAB_LABELS["ar"]["dashboard"] in rail  # the rail was English here


@pytest.mark.asyncio
async def test_an_english_screen_stays_english(tmp_path, monkeypatch):
    """The negative control: the same path, English."""
    tab, rail = await _labels_on_screen(tmp_path, monkeypatch, {})
    assert tab == "Dashboard"
    assert "Dashboard" in rail


@pytest.mark.asyncio
async def test_ctrl_l_switches_the_language_and_keeps_it(tmp_path, monkeypatch):
    """Nothing called ``set_language`` before 2026-10-01: the TUI's Arabic was
    out of reach but for a hand edit of preferences.json."""
    path = tmp_path / "preferences.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr("kazma_core.paths.preferences_path", lambda: path)
    from kazma_tui.app import KazmaTUI

    app = KazmaTUI()
    async with app.run_test(size=(120, 40)) as pilot:
        main = app.screen
        tab = main.query_one("#main-tabs", TabbedContent).get_tab("dashboard")
        assert str(tab.label) == "Dashboard" and not main.has_class("rtl-mode")

        await pilot.press("ctrl+l")
        await pilot.pause()
        assert str(tab.label) == TAB_LABELS["ar"]["dashboard"]
        assert main.has_class("rtl-mode")
        assert json.loads(path.read_text(encoding="utf-8"))["language"] == "ar"

        # Again with another screen on top -- the toast the switch showed, as
        # the command palette would be: the main screen changes all the same.
        assert app.screen is not main
        app.action_toggle_language()
        await pilot.pause()
        assert str(tab.label) == "Dashboard"
        assert not main.has_class("rtl-mode")
        assert json.loads(path.read_text(encoding="utf-8"))["language"] == "en"


def test_the_command_palette_offers_the_switch():
    from kazma_tui.app import KazmaTUI
    from kazma_tui.widgets.command_palette import CommandPalette

    ids = {cmd_id for cmds in CommandPalette.COMMANDS.values() for cmd_id, _text, _key in cmds}
    assert "toggle-language" in ids
    assert callable(getattr(KazmaTUI, "action_toggle_language", None))


@pytest.mark.asyncio
async def test_a_collapsed_rail_names_the_page_in_its_tooltip():
    from textual.app import App

    class _Host(App):
        def compose(self):
            yield NavRail()

    app = _Host()
    async with app.run_test(size=(60, 30)) as pilot:
        rail = app.query_one(NavRail)
        rail.set_labels(TAB_LABELS["ar"])
        rail.collapsed = True
        await pilot.pause()
        button = rail.query_one("#nav-dashboard")
        assert button.tooltip == TAB_LABELS["ar"]["dashboard"]
        assert str(button.label) == "1"
