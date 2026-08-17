"""Regression coverage for Settings panel pre-hydration visibility."""

from __future__ import annotations

from pathlib import Path


def test_settings_tab_panels_are_cloaked_before_alpine_initializes() -> None:
    """Hidden tabs must not flash before Alpine evaluates their x-show state."""
    template = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "templates"
        / "settings.html"
    )
    html = template.read_text(encoding="utf-8")
    missing_cloak = [
        line.strip()
        for line in html.splitlines()
        if 'x-show="tab ===' in line and "x-cloak" not in line
    ]

    assert missing_cloak == []
    assert ':class="{ hidden: tab !== \'providers_connectors\' }" x-cloak' in html
    assert ':class="{ hidden: hubSubtab !== \'connectors\' }" x-cloak' in html
    assert ':class="{ hidden: !loading }" x-cloak' in html
