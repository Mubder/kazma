"""A page reached by the sidebar has its own words before its scripts paint.

Soft navigation (``modules/nav.js``) used to run every one of the incoming
page's script files first and its inline scripts after. Pages define their
words inline, before the script that reads them: chat.html's
``window.CHAT_I18N`` comes before ``chat.js`` (so do the Dashboard's
``__DASH_I18N``, Knowledge's ``__KB_STRINGS`` and Memory's
``__MEM_STRINGS``). On an Arabic page reached by the sidebar, whatever
``chat.js`` painted before the inline scripts ran read an empty table and
said it in English (found in the Spaces S2-05 review, 2026-11-22). nav.js
now runs a page's scripts in the order the page has them, as a page load
does (``runPageScripts``).

The test makes the window wide and certain: the script after ``chat.js``
(``shared_memory_notice.js``) is held back for a few seconds, so the chat's
history -- a finished turn, whose header reads "Completed" in the page's
language -- paints while the page's scripts are still being run.

Control: the same page with its words moved after its last script --
what running every script file first did to them -- paints the header in
English while the held script is on its way.
"""

from __future__ import annotations

import asyncio
import functools
import re
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_feature_grants_browser import _soft_nav, _t  # noqa: E402

#: The script chat.html loads right after chat.js.
HELD = "/static/js/shared_memory_notice.js"
HOLD_S = 3.0

_WORDS = re.compile(r"<script>\s*/\*[^*]*\*/\s*window\.CHAT_I18N = .*?</script>\s*", re.S)
_LAST_SCRIPT = re.compile(r'<script src="/static/js/shared_memory_notice\.js[^"]*"></script>')


#: Records every text a turn header shows, from now on, into window.__labels.
RECORD_LABELS = """() => {
    window.__labels = [];
    const seen = new WeakMap();
    const look = () => document.querySelectorAll('.turn-header-label').forEach((el) => {
        const text = el.textContent.trim();
        if (text && seen.get(el) !== text) { seen.set(el, text); window.__labels.push(text); }
    });
    new MutationObserver(look).observe(document.body, {childList: true, subtree: true, characterData: true});
}"""


class _HoldTheScriptAfterChat:
    """ASGI: the script after chat.js answers late. With
    *words_after_chat_js*, the chat page's CHAT_I18N block is moved to after
    the page's last script (the control: today's order, every script file
    first and the inline scripts after)."""

    def __init__(self, app: Any, *, words_after_chat_js: bool = False) -> None:
        self.app = app
        self.words_after = words_after_chat_js

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path.endswith(HELD):
            await asyncio.sleep(HOLD_S)
        if not (self.words_after and path == "/chat"):
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
            html = b"".join(chunks).decode("utf-8")
            words = _WORDS.search(html)
            last = _LAST_SCRIPT.search(html)
            assert words and last, "the chat page's words or its last script were not found"
            moved = html.replace(words.group(0), "", 1)
            anchor = _LAST_SCRIPT.search(moved)
            html = moved[:anchor.end()] + words.group(0) + moved[anchor.end():]
            body = html.encode("utf-8")
            headers = [(k, v) for k, v in start.get("headers", []) if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            await send({**start, "headers": headers})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, keep)


def _header_labels_after_arriving_by_the_sidebar(words_after_chat_js: bool) -> tuple[list[str], str]:
    """A finished turn on /chat in Arabic; then a tab opened on Memory goes to
    Chat by the sidebar: every text the turn headers showed while arriving,
    and the label the catalog says."""
    from playwright.sync_api import sync_playwright

    wrapper = functools.partial(_HoldTheScriptAfterChat, words_after_chat_js=words_after_chat_js)
    with unified_turn_server(Script(steps=[], final="Done."), asgi_wrapper=wrapper) as h, \
            sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": "ar", "url": h.base}])
            pg = context.new_page()
            pg.goto(f"{h.base}/chat", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.KazmaChat", timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
            pg.fill("#chat-input", "say done")
            pg.evaluate("() => window.KazmaChat.sendMessage()")
            completed = _t("chat.phase_completed", "ar")
            pg.wait_for_function(
                "(word) => Array.from(document.querySelectorAll('.turn-header-label'))"
                ".some((el) => el.textContent.trim() === word)", arg=completed, timeout=30000)

            # A tab whose first page is not Chat: nothing of Chat's own words
            # is on its window yet (the same browser, so the same chat).
            pg.close()
            pg = context.new_page()
            pg.goto(f"{h.base}/memory", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.Alpine", timeout=30000)
            pg.locator(".sidebar .nav-links a[href='/chat']").wait_for(state="attached", timeout=30000)
            assert pg.evaluate("() => window.CHAT_I18N === undefined")
            pg.evaluate("() => { window.__noFullLoad = true; }")
            # Every text a turn header shows from here on, in order: a header
            # painted once in English and again in Arabic counts as English.
            pg.evaluate(RECORD_LABELS)
            # The address changes once the page's last script ran: the history
            # painted while the held script was still on its way.
            _soft_nav(pg, "/chat")
            pg.wait_for_function("() => window.__labels.length > 0", timeout=30000)
            pg.wait_for_function(
                "() => Array.from(document.querySelectorAll('.turn-header-label'))"
                ".some((el) => el.textContent.trim().length > 0)", timeout=30000)
            labels = pg.evaluate("() => window.__labels.slice()")
            assert pg.evaluate("() => window.__noFullLoad === true"), "arrived by a page load, not the sidebar"
            return labels, completed
        finally:
            browser.close()


def test_a_page_reached_by_the_sidebar_paints_in_its_own_language() -> None:
    labels, completed = _header_labels_after_arriving_by_the_sidebar(words_after_chat_js=False)
    assert labels and all(label == completed for label in labels), (labels, completed)


def test_control_words_after_the_script_paint_in_english() -> None:
    labels, _completed = _header_labels_after_arriving_by_the_sidebar(words_after_chat_js=True)
    assert "Completed" in labels, labels
