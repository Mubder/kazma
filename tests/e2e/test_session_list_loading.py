"""The chat list says it is loading until it has loaded (2026-09-28).

Opening the chat page re-rendered the session list before
``/api/chat/sessions`` answered, so the "Loading sessions…" placeholder was
replaced by the empty state -- "No sessions yet" and a Start button -- for a
second, over 128 chats on the live install. The Archived view had the same
empty state (and its Start button), English titles in every language, and
its title replaced the heading's text, deleting the session count for good.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

LIST = "#session-list"


def _seed_chat(sid: str, title: str) -> None:
    from kazma_ui.session_manager import get_session_manager

    sm = get_session_manager()
    sess = sm.get_or_create(sid)
    sess.thread_id = sid
    sess.title = title
    sess.messages = [{"role": "user", "content": "hello"},
                     {"role": "assistant", "content": "Hi."}]
    sm.put(sess)


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def test_the_list_never_claims_to_be_empty_while_it_loads(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    _seed_chat("sl-one", "Seeded chat")
    held: list = []
    holding = {"on": True}

    def hold(route) -> None:
        if holding["on"]:
            held.append(route)  # answered later, from the test
        else:
            route.continue_()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.route("**/api/chat/sessions", hold)
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            # init() asks for the list last, after the renders that used to
            # paint the empty state; then the open session's messages settle.
            deadline = time.monotonic() + 30
            while not held and time.monotonic() < deadline:
                pg.wait_for_timeout(100)  # lets Playwright hand us the route
            assert held, "the page never asked for its sessions"
            pg.wait_for_function(
                "() => { const w = document.querySelector('.chat-welcome');"
                " return !!window.KazmaChat && (!w || !/Loading messages/.test(w.innerText)); }",
                timeout=30000,
            )
            text = pg.inner_text(LIST)
            assert "No sessions yet" not in text, text
            assert "Loading sessions" in text, text

            holding["on"] = False
            for route in held:
                route.continue_()
            pg.wait_for_function(
                "() => document.querySelector('#session-list').innerText.includes('Seeded chat')",
                timeout=15000,
            )
            assert pg.inner_text("#session-count").strip() == "(1)"
        finally:
            context.close()
            browser.close()


def test_an_empty_list_is_still_shown_as_empty(harness: Harness) -> None:
    """Control: once the server answers "none", the empty state appears --
    the check above is not blind to it."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.route("**/api/chat/sessions",
                     lambda route: route.fulfill(status=200, content_type="application/json", body="[]"))
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function(
                "() => document.querySelector('#session-list').innerText.includes('No sessions yet')",
                timeout=30000,
            )
        finally:
            context.close()
            browser.close()


def test_the_archived_view_keeps_the_count_and_offers_no_new_chat(harness: Harness) -> None:
    from playwright.sync_api import sync_playwright

    _seed_chat("sl-two", "Kept chat")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.wait_for_function(
                "() => document.querySelector('#session-list').innerText.includes('Kept chat')",
                timeout=30000,
            )
            # A refresh started in the active view may finish after the
            # archive request. Hold it so this race is deterministic.
            held: list = []
            pg.route("**/api/chat/sessions", lambda route: held.append(route))
            pg.evaluate("() => { window.__lateSessionList = window.KazmaChat.refreshSessions(); }")
            deadline = time.monotonic() + 15
            while not held and time.monotonic() < deadline:
                pg.wait_for_timeout(50)
            assert held, "the refresh never requested its sessions"
            pg.evaluate("() => window.KazmaChat.toggleArchivedView()")
            pg.wait_for_function(
                "() => document.querySelector('#session-list').innerText.includes('No archived sessions')",
                timeout=15000,
            )
            assert pg.inner_text("#sessions-title") == "Archived"
            assert pg.locator("#session-empty-new").count() == 0

            with pg.expect_response(lambda response: response.url.endswith("/api/chat/sessions")) as late:
                held[0].continue_()
            late.value.finished()
            pg.evaluate("() => window.__lateSessionList")
            assert "No archived sessions" in pg.inner_text(LIST)
            assert "Kept chat" not in pg.inner_text(LIST)
            pg.unroute("**/api/chat/sessions")

            pg.evaluate("() => window.KazmaChat.toggleArchivedView()")
            pg.wait_for_function(
                "() => document.querySelector('#session-list').innerText.includes('Kept chat')",
                timeout=15000,
            )
            assert pg.inner_text("#sessions-title") == "Sessions"
            # The count element survived the round trip (it was deleted).
            assert pg.locator("#session-count").count() == 1
            assert pg.inner_text("#session-count").strip() == "(1)"

            # Cover the reverse race, including a stale HTTP error. Neither
            # an old archive response nor its error may replace active chats.
            for status in (200, 503):
                held.clear()
                pg.route("**/api/chat/sessions/archived", lambda route: held.append(route))
                pg.evaluate("() => { window.__lateSessionList = window.KazmaChat.toggleArchivedView(); }")
                deadline = time.monotonic() + 15
                while not held and time.monotonic() < deadline:
                    pg.wait_for_timeout(50)
                assert held, "the archive view never requested its sessions"
                pg.evaluate("() => window.KazmaChat.toggleArchivedView()")
                pg.wait_for_function(
                    "() => document.querySelector('#session-list').innerText.includes('Kept chat')",
                    timeout=15000,
                )
                with pg.expect_response(lambda response: response.url.endswith("/api/chat/sessions/archived")) as late:
                    held[0].fulfill(status=status, content_type="application/json", body="[]")
                late.value.finished()
                pg.evaluate("() => window.__lateSessionList")
                assert "Kept chat" in pg.inner_text(LIST)
                assert pg.inner_text("#session-count").strip() == "(1)"
                pg.unroute("**/api/chat/sessions/archived")
        finally:
            context.close()
            browser.close()
