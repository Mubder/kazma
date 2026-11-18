"""Two tabs of one person, each in another space, in a real browser (Spaces
S1-06 and S1-08).

The space is the tab's: one browser, one sign-in, two tabs, and each tab's
chats, turns and live frames stay in its own space. The page reaches Chat by
the sidebar (soft navigation runs the page scripts again, and that is where a
page loses what it was opened with), in English and in Arabic.

Each tab sends a message and gets its answer through the real app, graph and
checkpoint store with Spaces enforced. Then:

* each tab's chat list holds its own chat and not the other's;
* the tab that only watches is never shown the other space's turn;
* a reload keeps the tab in its space;
* the server recorded each turn's thread in the space of the tab it came from.

Negative control: a page whose calls name no space cannot be told from the
person's other tab, and is refused instead of being given a guessed space.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402

TEAM = "team-two-tabs"
ANSWER = "The scripted answer is here."

SENT = """(text) => {
  const box = document.querySelector('#chat-input');
  box.value = text;
  box.dispatchEvent(new Event('input', { bubbles: true }));
  return window.KazmaChat.sendMessage();
}"""

CHAT_LIST = "() => fetch('/api/chat/sessions').then(r => r.text())"


def _soft_nav(pg, href: str) -> None:
    """Follow a sidebar link the way a person does: a click nav.js handles."""
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _open_chat_by_sidebar(pg, base: str, space: str) -> None:
    # The first page a loopback browser opens mints its sign-in cookie; an
    # admin page asked for with none goes to the sign-in page instead.
    pg.goto(f"{base}/chat?space={space}", wait_until="domcontentloaded")
    pg.goto(f"{base}/memory?space={space}", wait_until="domcontentloaded")
    pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
    _soft_nav(pg, "/chat")
    pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
    pg.wait_for_function("() => !!window.KazmaChat", timeout=20000)


def _ask(pg, text: str) -> None:
    pg.evaluate(SENT, text)
    pg.wait_for_function(
        "(answer) => [...document.querySelectorAll('.message-assistant')].some("
        "el => el.textContent.includes(answer))", arg=ANSWER, timeout=90000)


def _two_spaces():
    """The owner in two spaces, and Spaces enforced for this process.

    Sign-in on: the harness runs with it off, and with no sign-in there is
    no account to check a space against (such a request naming another
    space is refused). With a secret set, the browser's first page gets the
    loopback auto-login, which is the owner's account. The harness puts the
    variable back when it stops."""
    import os

    from kazma_core.spaces import identity, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID

    os.environ["KAZMA_SECRET"] = "two-tabs-test-secret-" + "x" * 24
    identity.create_space("Two tabs team", created_by=OWNER_ACCOUNT_ID, space_id=TEAM)
    scope.set_enforced(True)


def _threads_of_chats(space: str) -> dict[str, str]:
    """title -> thread id of every chat *space* holds, read from the store."""
    from kazma_core.spaces import scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from kazma_ui.session_manager import get_session_manager

    with scope.bind_scope(space, OWNER_ACCOUNT_ID):
        sessions = get_session_manager().list_all(include_archived=True, include_empty=True,
                                                  prune_empty=False)
        return {" ".join(str(m.get("content") or "") for m in s.messages): s.thread_id for s in sessions}


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_two_tabs_in_two_spaces_keep_their_chats_apart(lang):
    from kazma_core.spaces import scope, threads
    from kazma_core.spaces.scope import OWNER_SPACE_ID
    from playwright.sync_api import sync_playwright

    mine = f"a question from my own space ({lang})"
    team = f"a question from the team space ({lang})"
    script = Script(steps=[], final=ANSWER)
    refused: list[str] = []
    with unified_turn_server(script) as h, sync_playwright() as p:
        _two_spaces()
        browser = p.chromium.launch()
        try:
            # One context: one sign-in, as one person's two tabs are.
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            tab_mine, tab_team = context.new_page(), context.new_page()
            for tab in (tab_mine, tab_team):
                tab.on("response", lambda r: refused.append(f"{r.status} {r.url}")
                       if r.status == 403 and "/api/" in r.url else None)
            _open_chat_by_sidebar(tab_mine, h.base, OWNER_SPACE_ID)
            _open_chat_by_sidebar(tab_team, h.base, TEAM)
            assert tab_mine.evaluate("() => window.kazmaSpace.id") == OWNER_SPACE_ID
            assert tab_team.evaluate("() => window.kazmaSpace.id") == TEAM
            assert tab_team.evaluate("() => document.documentElement.lang") == lang

            _ask(tab_mine, mine)
            _ask(tab_team, team)

            # Each tab's list is its own space's.
            listed_mine, listed_team = tab_mine.evaluate(CHAT_LIST), tab_team.evaluate(CHAT_LIST)
            assert mine in listed_mine and team not in listed_mine
            assert team in listed_team and mine not in listed_team
            # The tab that only watched was never shown the other's turn.
            assert team not in tab_mine.evaluate("() => document.body.textContent")
            assert mine not in tab_team.evaluate("() => document.body.textContent")

            # A reload keeps the tab in its space.
            tab_team.reload(wait_until="domcontentloaded")
            tab_team.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
            assert tab_team.evaluate("() => window.kazmaSpace.id") == TEAM
            reloaded = tab_team.evaluate(CHAT_LIST)
            assert team in reloaded and mine not in reloaded
        finally:
            browser.close()
            scope.set_enforced(False)

        # What the server kept: each chat in its tab's space, each thread
        # recorded there.
        in_mine, in_team = _threads_of_chats(OWNER_SPACE_ID), _threads_of_chats(TEAM)
        assert any(mine in text for text in in_mine) and not any(team in text for text in in_mine)
        assert any(team in text for text in in_team) and not any(mine in text for text in in_team)
        team_thread = next(thread for text, thread in in_team.items() if team in text)
        mine_thread = next(thread for text, thread in in_mine.items() if mine in text)
        assert threads.record_of(team_thread).space_id == TEAM
        assert threads.record_of(mine_thread).space_id == OWNER_SPACE_ID
    assert refused == [], refused


def test_control_a_page_that_names_no_space_is_refused_not_guessed():
    """Negative control. The page's calls carry no space (the script that
    names it is not loaded): the server cannot tell the tabs apart, and a
    request that must say its space is refused instead of guessed."""
    from kazma_core.spaces import scope
    from kazma_core.spaces.scope import OWNER_SPACE_ID
    from playwright.sync_api import sync_playwright

    script = Script(steps=[], final=ANSWER)
    with unified_turn_server(script) as h, sync_playwright() as p:
        _two_spaces()
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            # The page loads, but its space script is kept from running.
            context.route("**/static/js/space_scope.js*",
                          lambda route: route.fulfill(status=200, content_type="application/javascript", body=""))
            tab = context.new_page()
            tab.goto(f"{h.base}/chat?space={TEAM}", wait_until="domcontentloaded")
            tab.locator("#chat-input").wait_for(state="visible", timeout=30000)
            status = tab.evaluate("() => fetch('/api/chat/sessions').then(r => r.status)")
            # No space named, a person in two spaces: refused, never the
            # first space as a guess.
            assert status == 400, status
            named = tab.evaluate(
                "(space) => fetch('/api/chat/sessions', {headers: {'X-Kazma-Space': space}}).then(r => r.status)",
                OWNER_SPACE_ID)
            assert named == 200
        finally:
            browser.close()
            scope.set_enforced(False)
