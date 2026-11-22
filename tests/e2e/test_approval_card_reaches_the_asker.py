"""An approval card goes to the person it asks, in a real browser (Spaces
S2-05).

The owner and a member, Omar, share a space. The owner's turn there pauses
at a card. Both reach Chat by the sidebar (soft navigation runs the page
scripts again), in English and in Arabic:

* the owner's page shows the card with its buttons;
* Omar's page shows the same card waiting for the owner, with no button
  he can press, in the page's language;
* Omar's answer, sent anyway, is refused (403 ``not_yours``) and the card
  stays pending;
* the owner answers it and the turn finishes.

The control is in ``tests/test_approvals_reach_the_asker.py`` (the
thread-owner rule lets Omar decide) and ``tests/js/test_hitl_waiting_for.js``
(a card that names nobody keeps its buttons).
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, Step, unified_turn_server  # noqa: E402

TEAM = "team-s205"
MEMBER = "omar-s205"
ANSWER = "The note is written."


def _soft_nav(pg, href: str) -> None:
    """Follow a sidebar link the way a person does: a click nav.js handles."""
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _team_with_omar() -> str:
    """Sign-in on, the shared space, Omar in it, Spaces enforced; Omar's raw
    session id. The harness puts KAZMA_SECRET back when it stops."""
    import os

    from kazma_core.security import platform_rbac, web_sessions
    from kazma_core.spaces import identity, scope
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID
    from kazma_ui import space_binding

    os.environ["KAZMA_SECRET"] = "approval-asker-test-secret-" + "x" * 24
    user = platform_rbac.create_local_user(MEMBER, "a-long-enough-password-1")
    account = space_binding.account_of({"source": "session", "actor": "login", "user_id": user.user_id,
                                        "username": MEMBER, "role": "operator"})
    assert account, "Omar has no account"
    identity.create_space("Team", created_by=OWNER_ACCOUNT_ID, space_id=TEAM)
    identity.add_member(TEAM, account)
    scope.set_enforced(True)
    return web_sessions.create_session(role="operator", actor="login", username=MEMBER, user_id=user.user_id)


def _open_chat_by_sidebar(pg, base: str) -> None:
    pg.goto(f"{base}/memory?space={TEAM}", wait_until="domcontentloaded")
    pg.wait_for_function("() => !!window.kazmaSpace", timeout=20000)
    _soft_nav(pg, "/chat")
    pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
    pg.wait_for_function("() => !!window.KazmaChat", timeout=20000)


def _label(key: str, lang: str, **values: str) -> str:
    from kazma_ui.i18n import t

    text = t(key, lang)
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_the_card_is_answered_only_by_the_person_it_asks(lang):
    from kazma_core.spaces import scope
    from playwright.sync_api import sync_playwright

    script = Script(steps=[Step(tool="file_write", args={"path": f"s205-{lang}.txt", "content": "hi"},
                                narration="Writing the note.")], final=ANSWER)
    with unified_turn_server(script) as h, sync_playwright() as p:
        omar_session = _team_with_omar()
        browser = p.chromium.launch()
        try:
            owner_ctx = browser.new_context()
            owner_ctx.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            owner = owner_ctx.new_page()
            # The loopback browser's first page mints the owner's sign-in.
            owner.goto(f"{h.base}/chat?space={TEAM}", wait_until="domcontentloaded")
            _open_chat_by_sidebar(owner, h.base)
            owner.evaluate("() => window.KazmaChat.newSession()")
            owner.wait_for_timeout(600)
            owner.fill("#chat-input", f"write the note ({lang})")
            owner.evaluate("() => window.KazmaChat.sendMessage()")
            owner.wait_for_function(
                "() => !!document.querySelector('.hitl-approval-card button:not([disabled]):not([hidden])')",
                timeout=120000)
            assert owner.locator(".hitl-approval-card[data-hitl-waiting]").count() == 0
            session_id = owner.evaluate("() => localStorage.getItem('kazma.chatSessionId')")
            assert session_id

            omar_ctx = browser.new_context()
            omar_ctx.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base},
                                  {"name": "kazma-session", "value": omar_session, "url": h.base}])
            omar = omar_ctx.new_page()
            omar.goto(f"{h.base}/memory?space={TEAM}", wait_until="domcontentloaded")
            omar.evaluate("(sid) => localStorage.setItem('kazma.chatSessionId', sid)", session_id)
            _open_chat_by_sidebar(omar, h.base)
            omar.wait_for_function(
                "() => !!document.querySelector('.hitl-approval-card[data-hitl-waiting]')", timeout=60000)
            card = omar.locator(".hitl-approval-card[data-hitl-waiting]").first
            assert card.locator("button:visible").count() == 0
            waiting = card.locator(".hitl-waiting-for").inner_text().strip()
            prefix = _label("chat.hitl_waiting_for", lang, names="").strip()
            assert prefix.split()[0] in waiting, (waiting, prefix)

            # His answer, sent anyway, is refused; the card stays pending.
            # The owner's list holds the card; Omar's does not.
            listed = owner.evaluate(
                "(space) => fetch('/api/pending-approvals', {headers: {'X-Kazma-Space': space}})"
                ".then(r => r.json()).then(b => b.pending || [])", TEAM)
            assert len(listed) == 1, listed
            thread_id = listed[0]["thread_id"]
            omars = omar.evaluate(
                "(space) => fetch('/api/pending-approvals', {headers: {'X-Kazma-Space': space}})"
                ".then(r => r.json()).then(b => b.pending || [])", TEAM)
            assert omars == [], omars
            gate = card.get_attribute("data-interrupt-id")
            status = omar.evaluate(
                "(a) => fetch('/api/approve/' + encodeURIComponent(a.thread), {method: 'POST',"
                " headers: {'Content-Type': 'application/json', 'X-Kazma-Space': a.space},"
                " body: JSON.stringify({action: 'approve', interrupt_id: a.gate})})"
                ".then(r => r.json().then(b => [r.status, b.code || '']))",
                {"thread": thread_id, "space": TEAM, "gate": gate})
            assert status == [403, "not_yours"], status
            assert owner.locator(".hitl-approval-card button:not([disabled]):not([hidden])").count() > 0

            # The owner answers it and the turn finishes.
            owner.locator(".hitl-approval-card .hitl-approve").first.click()
            owner.wait_for_function(
                "(a) => [...document.querySelectorAll('.message-assistant')].some("
                "el => el.textContent.includes(a))", arg=ANSWER, timeout=120000)
        finally:
            browser.close()
            scope.set_enforced(False)
