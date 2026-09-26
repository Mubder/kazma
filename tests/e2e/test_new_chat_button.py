"""The New Chat button starts a new chat, from any page (2026-09-26).

The header's New Chat went to ``/chat``, and ``/chat`` resumes the last
session: clicking it reloaded the chat you were already in, and from any
other page it opened that chat again. Found testing the live install through
its UI. ``kazmaNewChat()`` (nav.js) now starts a session in place on /chat
and opens ``/chat?new=1`` elsewhere, which the chat page honours. Ctrl+N runs
the same function.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

SESSION = "() => localStorage.getItem('kazma.chatSessionId')"
NEW_CHAT = "button.header-btn[title*='Ctrl+N']"


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def _chat_ready(pg) -> None:
    pg.wait_for_function("() => !!window.KazmaChat && !!localStorage.getItem('kazma.chatSessionId')",
                         timeout=30000)


def test_new_chat_starts_a_session_on_chat_and_from_another_page(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            _chat_ready(pg)
            first = pg.evaluate(SESSION)

            # On /chat: a fresh session in place.
            pg.click(NEW_CHAT)
            pg.wait_for_function(f"(prev) => ({SESSION})() !== prev", arg=first, timeout=10000)
            second = pg.evaluate(SESSION)
            assert second and second != first

            # Negative control: /chat on its own resumes the last session --
            # the only thing the old button did.
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            _chat_ready(pg)
            assert pg.evaluate(SESSION) == second

            # From another page: New Chat opens a NEW chat, not the last one.
            pg.goto(f"{harness.base}/dashboard", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function("() => typeof window.kazmaNewChat === 'function'", timeout=30000)
            pg.click(NEW_CHAT)
            pg.wait_for_url("**/chat*", timeout=30000)
            _chat_ready(pg)
            pg.wait_for_function(f"(prev) => ({SESSION})() !== prev", arg=second, timeout=10000)
            third = pg.evaluate(SESSION)
            assert third and third not in (first, second)
            assert "new=1" not in pg.url  # the request is not left in the address bar
            assert pg.locator(".chat-welcome").count() == 1
        finally:
            context.close()
            browser.close()
