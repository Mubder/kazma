"""A new, empty chat offers the memory choice before its first message
(resilience plan I), in English and Arabic, reached by the sidebar.

A new chat is in no list yet, so its menu -- where "Don't remember" lived --
was out of reach until a first turn had already been remembered. The empty
chat now shows the choice; choosing it keeps the chat out of memory at once
(the chat's memory state reads "not remembered"), and it goes away with the
first message.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import unified_turn_server  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.mark.parametrize("language", ["en", "ar"])
def test_an_empty_chat_offers_dont_remember(language: str) -> None:
    from kazma_ui.i18n import TRANSLATIONS
    from playwright.sync_api import sync_playwright

    with unified_turn_server() as h:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.add_cookies([dict(name="kazma-lang", value=language, url=h.base)])
                page = context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(h.base + "/dashboard", wait_until="domcontentloaded")
                page.wait_for_function("() => !!window.Alpine")
                page.click('nav a[href="/chat"], aside a[href="/chat"]', timeout=15000)
                page.wait_for_function("() => location.pathname === '/chat'", timeout=20000)
                page.wait_for_selector("#chat-memory-choice:not([hidden])", timeout=20000)
                choice = page.locator("#chat-memory-choice")
                assert choice.inner_text().strip() == TRANSLATIONS["chat.memory_choice"][language]
                choice.click()
                page.wait_for_function(
                    "() => document.getElementById('chat-memory-choice').getAttribute('aria-pressed') === 'true'",
                    timeout=15000)
                session = page.evaluate("() => localStorage.getItem('kazma.chatSessionId') || ''")
                assert session, "the new chat has its id"
                state = page.evaluate("async (sid) => (await fetch('/api/memory/v2/chats/' + "
                                      "encodeURIComponent(sid) + '/memory')).json()", session)
                assert state.get("remembered") is False, state
                assert errors == [], errors
            finally:
                browser.close()
