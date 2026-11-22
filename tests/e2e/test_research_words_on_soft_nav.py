"""The Research page reached by the sidebar keeps its own words.

research.html merges its words into ``window.KAZMA_I18N`` in an inline
script. Soft navigation (``modules/nav.js``) refreshes ``KAZMA_I18N`` from
the shell's inert JSON and then runs the page's inline scripts again -- but
it skipped every inline script whose text named ``window.KAZMA_I18N`` (meant
for the shell's own bootstrap), so research.html's merge never ran again,
the refresh wiped what it had merged, and the Research page reached by the
sidebar fell back to English (found in the Spaces second review,
2026-11-22). The shell's bootstrap is now marked
``data-kazma-i18n-bootstrap`` and skipped by that mark alone.

Control: the same page with nav.js's text rule put back loses the merged
words.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_feature_grants_browser import _soft_nav, _t  # noqa: E402

_NEW_RULE = "if (s.getAttribute('data-kazma-i18n-bootstrap') !== null) return false;"
_OLD_RULE = "if (s.textContent && s.textContent.includes('window.KAZMA_I18N')) return false;"


class _OldNavRule:
    """ASGI: serve nav.js with the text rule of before (the control)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not (scope.get("path") or "").endswith("/modules/nav.js"):
            await self.app(scope, receive, send)
            return
        start: dict[str, Any] = {}
        chunks: list[bytes] = []

        async def keep(message: dict) -> None:
            if message["type"] == "http.response.start":
                start.update(message)
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            text = b"".join(chunks).decode("utf-8")
            assert _NEW_RULE in text, "nav.js's bootstrap rule was not found"
            body = text.replace(_NEW_RULE, _OLD_RULE).encode("utf-8")
            headers = [(k, v) for k, v in start.get("headers", []) if k.lower() not in (b"content-length", b"etag")]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, keep)


def _research_words_after_the_sidebar(*, old_rule: bool) -> tuple[Any, str]:
    """A tab opened on Memory in Arabic goes to Research by the sidebar:
    the merged word for "View report", and what the catalog says."""
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok"), asgi_wrapper=_OldNavRule if old_rule else None) as h, \
            sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": "ar", "url": h.base}])
            pg = context.new_page()
            pg.goto(f"{h.base}/memory", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.Alpine", timeout=30000)
            pg.locator(".sidebar .nav-links a[href='/research']").wait_for(state="attached", timeout=30000)
            pg.evaluate("() => { window.__noFullLoad = true; }")
            _soft_nav(pg, "/research")
            pg.locator("#research-list").wait_for(state="attached", timeout=30000)
            pg.wait_for_function("() => !!document.querySelector('#research-start-btn')", timeout=30000)
            assert pg.evaluate("() => window.__noFullLoad === true"), "arrived by a page load, not the sidebar"
            # The page's inline scripts ran: its words are on the window.
            pg.wait_for_timeout(500)
            word = pg.evaluate("() => (window.KAZMA_I18N || {}).research_view_report")
            return word, _t("research.view_report", "ar")
        finally:
            browser.close()


def test_the_research_page_keeps_its_words_by_the_sidebar() -> None:
    word, arabic = _research_words_after_the_sidebar(old_rule=False)
    assert word == arabic, (word, arabic)


def test_control_the_text_rule_lost_the_research_pages_words() -> None:
    word, _arabic = _research_words_after_the_sidebar(old_rule=True)
    assert word is None, word
