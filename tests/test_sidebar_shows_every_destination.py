"""Every destination is in the sidebar, grouped — none behind a disclosure.

The nav showed four links and hid the other twelve inside
`<details class="nav-more">`: two thirds of Kazma behind a click labelled
"More", which gives no hint of what it holds. The section vocabulary already
existed in the i18n catalog — Work / Activity / Capabilities / Settings — and
was never rendered, so someone had designed this and it got collapsed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SIDEBAR = (
    Path(__file__).resolve().parent.parent
    / "kazma-ui" / "kazma_ui" / "templates" / "components" / "sidebar.html"
)


@pytest.fixture(scope="module")
def markup() -> str:
    return _SIDEBAR.read_text(encoding="utf-8")


def test_nothing_is_hidden_behind_a_disclosure(markup):
    assert "nav-more" not in markup.split("{#")[0] + "".join(
        seg.split("#}", 1)[-1] for seg in markup.split("{#")[1:]
    ), "a <details> disclosure is back in the nav"


def test_every_destination_is_still_present(markup):
    hrefs = set(re.findall(r'<a href="(/[^"]*)" class="nav-link', markup))
    expected = {
        "/chat", "/workspace", "/ide",
        "/memory", "/knowledge", "/documents", "/research",
        "/agents", "/swarm", "/scheduled",
        "/dashboard", "/replay",
        "/skills", "/mcp", "/x",
        "/settings",
    }
    assert hrefs == expected, f"missing {expected - hrefs}, unexpected {hrefs - expected}"


def test_every_link_sits_under_a_heading(markup):
    """A flat list of sixteen is not better than four plus a shrug."""
    nav = markup[markup.index('<nav class="nav-links">') : markup.index("</nav>")]
    order = re.findall(r'nav-section-title|<a href="(/[^"]*)" class="nav-link', nav)
    assert order, "no nav content found"
    assert order[0] == "", "the nav does not open with a section heading"
    seen_heading = False
    for item in order:
        if item == "":
            seen_heading = True
        else:
            assert seen_heading, f"{item} appears before any heading"


def test_the_headings_are_translated_not_hardcoded(markup):
    nav = markup[markup.index('<nav class="nav-links">') : markup.index("</nav>")]
    headings = re.findall(r'nav-section-title">([^<]*)<', nav)
    assert len(headings) >= 5, f"only {len(headings)} sections"
    for h in headings:
        assert h.strip().startswith("{{ t("), f"hardcoded heading: {h!r}"


def test_the_section_keys_resolve_in_both_languages():
    from kazma_ui.i18n import t

    nav = _SIDEBAR.read_text(encoding="utf-8")
    keys = re.findall(r"nav-section-title\">\{\{ t\('([^']+)'\)", nav)
    assert keys
    for key in keys:
        for lang in ("en", "ar"):
            value = t(key, lang)
            assert value and value != key, f"{key} has no {lang} translation"


def test_a_collapsed_sidebar_hides_the_headings():
    """Collapsed, the sidebar is icons only — headings would render as
    orphaned text."""
    css = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"
    ).read_text(encoding="utf-8")
    assert ".sidebar.collapsed .nav-section-title" in css


def test_the_nav_can_scroll():
    """Sixteen links plus six headings will not fit every window."""
    css = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"
    ).read_text(encoding="utf-8")
    block = css[css.index(".nav-links {") : css.index("}", css.index(".nav-links {"))]
    assert "overflow-y: auto" in block
