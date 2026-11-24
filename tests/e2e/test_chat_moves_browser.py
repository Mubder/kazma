"""Moving one's own chat from the chat list (Spaces S2-12), EN/AR.

Reached by the sidebar. In the person's own space their chat's menu offers
"Share with…" each shared space they are in; the dialog says how much of
theirs the chat holds, what sharing means, and the rule -- the conversation
moves, what Kazma remembered stays where it was said -- before it moves
anything. Then the chat is gone from their own list and in the shared
space's, its thread recorded there. In the shared space the same chat
offers "Take out to my space", said first too, and afterwards the shared
space lists the line it keeps: the title, never a word of the chat.

The routes, the chat store, the thread records, the checkpoints and the forget
ledger are the real app's (memory: ``tests/test_chat_move_memory.py``).
"""

from __future__ import annotations

import os
import secrets

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, unified_turn_server  # noqa: E402
from tests.e2e.test_people_page_browser import _modal_action, _soft_nav, _t  # noqa: E402


def _plain(text: str) -> str:
    return " ".join(text.replace("\u2068", "").replace("\u2069", "").split())


def _chat(space: str, account: str) -> tuple[str, str]:
    from kazma_core.spaces import threads
    from kazma_core.spaces.scope import bind_scope
    from kazma_ui.session_manager import get_session_manager

    session_id, thread_id = f"s{secrets.token_hex(6)}", f"t{secrets.token_hex(6)}"
    with bind_scope(space, account):
        store = get_session_manager()
        session = store.get_or_create(session_id, thread_id=thread_id)
        session.add_message("user", "Plan our trip to Oman", author=account)
        session.add_message("assistant", "Here is a plan.")
        session.title = "Trip to Oman"
        store.put(session)
    threads.record_thread(thread_id, space, account)
    return session_id, thread_id


def _open_menu(pg, session_id: str):
    item = pg.locator(f'.session-item[data-session-id="{session_id}"]')
    item.wait_for(state="visible", timeout=30000)
    item.hover()
    item.locator(".session-more").click()
    return pg.locator(f'.session-menu[data-menu="{session_id}"]')


@pytest.mark.parametrize("lang", ["en", "ar"])
def test_share_and_take_out_a_chat_from_the_chat_list(lang):
    from kazma_core.spaces import chat_moves, people, scope, threads
    from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
    from playwright.sync_api import sync_playwright

    with unified_turn_server(Script(steps=[], final="ok")) as h, sync_playwright() as p:
        os.environ["KAZMA_SECRET"] = "chat-moves-test-secret-" + "x" * 24
        scope.set_enforced(True)
        assert chat_moves._SETTLER, "the app's start gives the maintenance sweep what settles a move"
        family = people.create_shared_space("Family", by=OWNER_ACCOUNT_ID)["id"]
        session_id, thread_id = _chat(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
        browser = p.chromium.launch()
        try:
            context = browser.new_context()
            context.add_cookies([{"name": "kazma-lang", "value": lang, "url": h.base}])
            pg = context.new_page()
            # The first page a loopback browser opens mints its sign-in
            # cookie; the next is drawn for the signed-in person.
            pg.goto(f"{h.base}/chat?space={OWNER_SPACE_ID}", wait_until="domcontentloaded")
            pg.goto(f"{h.base}/documents?space={OWNER_SPACE_ID}", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace && window.kazmaSpace.spaces.length > 1", timeout=20000)
            _soft_nav(pg, "/chat")

            # ── In the person's own space: Share with Family ─────────────
            menu = _open_menu(pg, session_id)
            share = menu.locator(f'[data-menu-action="move"][data-menu-to="{family}"]')
            share.wait_for(state="visible", timeout=20000)
            assert _plain(share.inner_text()) == _plain(_t("chat.move_share_with", lang, name="Family"))
            assert menu.locator('[data-menu-takeout="1"]').count() == 0, "nothing to take out of one's own space"
            share.click()
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            dialog = pg.locator(".modal", has=pg.locator("#modal-title"))
            said = _plain(dialog.inner_text())
            assert _plain(_t("chat.move_share_title", lang)) in said
            assert _plain(_t("chat.move_size.one", lang)) in said, said
            assert _plain(_t("chat.move_share_body", lang)) in said
            assert _plain(_t("chat.move_memory_note", lang)) in said
            assert _plain(_t("chat.move_also_note", lang)) in said
            with pg.expect_response(lambda r: r.url.endswith("/move") and r.request.method == "POST") as answer:
                _modal_action(pg, _t("chat.move_share_ok", lang))
            assert answer.value.status == 200, answer.value.text()
            pg.locator(f'.session-item[data-session-id="{session_id}"]').wait_for(state="detached", timeout=20000)
            assert threads.record_of(thread_id).space_id == family

            # ── In the shared space: Take out to my space ────────────────
            pg.goto(f"{h.base}/documents?space={family}", wait_until="domcontentloaded")
            pg.wait_for_function("() => !!window.kazmaSpace && window.kazmaSpace.spaces.length > 1", timeout=20000)
            _soft_nav(pg, "/chat")
            menu = _open_menu(pg, session_id)
            take = menu.locator('[data-menu-action="move"][data-menu-takeout="1"]')
            take.wait_for(state="visible", timeout=20000)
            assert _plain(take.inner_text()) == _plain(_t("chat.move_take_out", lang))
            take.click()
            pg.locator("#modal-title").wait_for(state="visible", timeout=10000)
            said = _plain(dialog.inner_text())
            assert _plain(_t("chat.move_take_out_title", lang)) in said
            assert _plain(_t("chat.move_take_out_body", lang)) in said
            assert _plain(_t("chat.move_memory_note", lang)) in said
            with pg.expect_response(lambda r: r.url.endswith("/move") and r.request.method == "POST") as answer:
                _modal_action(pg, _t("chat.move_take_out_ok", lang))
            assert answer.value.status == 200, answer.value.text()
            pg.locator(f'.session-item[data-session-id="{session_id}"]').wait_for(state="detached", timeout=20000)
            assert threads.record_of(thread_id).space_id == OWNER_SPACE_ID
            line = pg.locator("[data-taken-out]")
            line.first.wait_for(state="visible", timeout=20000)
            text = line.first.inner_text()
            assert "Trip to Oman" in text and "Plan our trip" not in text, text
            # The DOM's words (the label is drawn in capitals).
            assert _plain(_t("chat.taken_out_label", lang)) in _plain(pg.locator(".session-taken-out").text_content())
            assert _plain(_t("chat.taken_out_unnamed", lang)) in _plain(line.first.text_content()), (
                "a person with no name is not 'taken out by' nothing")
        finally:
            browser.close()
            scope.set_enforced(False)
