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


def test_no_disclosure_css_survives_the_disclosure() -> None:
    """The `.nav-more` rules are gone because the markup is.

    This used to assert how the disclosure hid its body — `display: none` on
    `.nav-more-body`, never on the `<details>` node itself (the 2026-08-26
    revert). The nav no longer hides anything: all sixteen destinations are
    visible under headings, so those selectors matched nothing.

    A selector matching nothing is not harmless — it is what silently dropped
    the provider model list's styling on 2026-09-14, because CSS has no
    undefined-name error. Dead rules go.
    """
    v5 = _V5.read_text(encoding="utf-8")
    assert ".nav-more-body" not in v5
    assert ".nav-more[open]" not in v5


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
    assert "fromTextArea" in js
    assert "CodeMirror" in html
    assert "_langFromName(filePath)" in js
    assert "var _ideCM = null" in js
    assert 'id="ide-fallback"' in html
    assert "x-ignore" in html
    assert "monaco-editor" not in html
    assert "openLatestReview" in js
    assert "rejectReview" in js


def test_sidebar_leads_with_the_work_surfaces() -> None:
    """Chat leads; Dashboard is an inspector and comes later.

    The ordering used to be enforced by hiding Dashboard behind "More". It is
    visible now, so the grouping carries the distinction: Work first, the rest
    under their own headings.

    The inline-display check stays. That was the 2026-08-26 lesson — a
    `display:` in this nav beat the UA's own hiding — and it is about how
    fragile display toggling here is, not about the disclosure it was written
    for.
    """
    html = _SIDEBAR.read_text(encoding="utf-8")
    nav_chunk = html.split("<nav")[1].split("</nav>")[0]
    assert 'style="display:' not in nav_chunk
    assert "<details" not in nav_chunk
    for href in ("/chat", "/settings", "/dashboard"):
        assert f'href="{href}"' in nav_chunk
    assert nav_chunk.index('href="/chat"') < nav_chunk.index('href="/dashboard"')
