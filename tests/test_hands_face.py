"""Hands 0.11 WP0/WP1 — chat is home; More actually hides."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MISC = _ROOT / "kazma-ui" / "kazma_ui" / "routes_direct" / "misc.py"
_SIDEBAR = _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "components" / "sidebar.html"
_V5 = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.v5.css"


def test_root_handler_redirects_to_chat() -> None:
    src = _MISC.read_text(encoding="utf-8")
    assert "RedirectResponse" in src
    assert 'url="/chat"' in src
    assert "dashboard.html" not in src.split("async def root")[1].split("async def ")[0]


def test_nav_more_css_does_not_flex_the_details_node() -> None:
    v5 = _V5.read_text(encoding="utf-8")
    assert ".nav-more-body {" in v5
    assert "display: none;" in v5
    assert ".nav-more[open] > .nav-more-body" in v5
    # Negative: display must not be set on details.nav-more itself.
    chunk = v5.split(".nav-more {")[1].split(".nav-more-summary")[0]
    assert "display:" not in chunk


def test_ide_review_panel_markers() -> None:
    html = (
        _ROOT / "kazma-ui" / "kazma_ui" / "templates" / "ide.html"
    ).read_text(encoding="utf-8")
    js = (
        _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "ide.js"
    ).read_text(encoding="utf-8")
    assert 'class="ide-review' in html
    assert "ide-toolbar" in html
    assert "t('ide.review_accept')" in html
    assert "t('ide.review_reject')" in html
    assert 'x-if="reviewOpen"' in html
    assert "(file.hunks || [])" not in html
    assert "automaticLayout: false" in js
    assert "lineNumbers: 'on'" in js
    assert "bracketPairColorization" in js
    assert "_langFromName(filePath)" in js
    assert "var _ideMonaco = null" in js
    assert "cm: null" not in js
    assert 'id="ide-monaco-host"' in html
    assert "x-ignore" in html
    assert "openLatestReview" in js
    assert "rejectReview" in js


def test_sidebar_work_links_outside_more() -> None:
    html = _SIDEBAR.read_text(encoding="utf-8")
    work, _, more = html.partition('class="nav-more-body"')
    assert 'href="/chat"' in work
    assert 'href="/settings"' in work
    assert 'href="/dashboard"' in more
    nav_chunk = html.split("<nav")[1].split("</nav>")[0]
    assert 'style="display:' not in nav_chunk
