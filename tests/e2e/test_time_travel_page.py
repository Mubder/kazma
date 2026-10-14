"""The Time Travel page and a restored chat, in a browser (Time Travel TT-Q1).

The 2026-10-13 audit exercised Time Travel through its routes and engine only.
No test opened the page, and every browser test that existed loaded its page
directly, while a person arrives by the sidebar (a soft navigation that swaps
the page and re-runs its scripts). This drives the real app on the harness
(never the live install) with a scripted model:

* two turns of one chat; the page, reached from another page by the sidebar,
  lists them as turns, each step once, and restores the chat to the first
  turn; the chat, reached by the sidebar, then shows the first turn only, and
  the next question reaches the model without the rewound turn (TT-03);
* the page in Arabic, every tab, with no English interface text (the
  instrument of ``test_pages_read_in_arabic``);
* a step list the server answers with 500 shows an error, never "No steps"
  (TT-10). The negative control is the same check with the route answering
  an empty list: the page then says there are no steps, which the error
  check must not mistake for an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

pytest.importorskip("playwright.sync_api")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import Script, drive_turn, new_session_id, unified_turn_server  # noqa: E402
from tests.e2e.test_pages_read_in_arabic import english_on  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@dataclass
class _EchoScript(Script):
    """Answers each question with "Answer to <question>" and keeps what the
    model was sent, so the test can read the next turn's input."""

    seen: list[list[dict[str, Any]]] = field(default_factory=list)

    def respond(self, messages: list[dict[str, Any]]) -> Any:
        from kazma_core.llm_provider import LLMResponse

        self.seen.append([dict(m) for m in messages or [] if isinstance(m, dict)])
        last = ""
        for m in reversed(messages or []):
            if isinstance(m, dict) and str(m.get("role") or "") == "user":
                last = str(m.get("content") or "")
                break
        self.calls.append("final")
        return LLMResponse(content=f"Answer to {last}", finish_reason="stop", model="harness")


def _soft_nav(pg: Any, href: str) -> None:
    """Follow a sidebar link the way a person does: a click nav.js handles."""
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _arrive_at_time_travel(pg: Any, base: str) -> None:
    pg.goto(f"{base}/memory", wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_function("() => !!window.Alpine", timeout=30000)
    pg.wait_for_timeout(1000)
    _soft_nav(pg, "/replay")
    pg.wait_for_function("() => !!window.KazmaReplay && !!document.getElementById('replay-thread-select')",
                         timeout=20000)
    # A soft navigation, not a page load.
    assert pg.evaluate("() => performance.getEntriesByType('navigation')[0].name").endswith("/memory")


def _open_thread(pg: Any, thread_id: str) -> None:
    pg.wait_for_function(
        "(t) => !!document.querySelector('#replay-thread-select option[value=\"' + t + '\"]')",
        arg=thread_id, timeout=30000)
    pg.select_option("#replay-thread-select", thread_id)


def _user_and_answer_texts(messages: list[dict[str, Any]]) -> list[str]:
    return [str(m.get("content") or "") for m in messages if str(m.get("role") or "") in ("user", "assistant")]


def test_the_page_restores_a_chat_reached_by_the_sidebar_in_english_and_arabic():
    from playwright.sync_api import sync_playwright

    script = _EchoScript(steps=[])
    with unified_turn_server(script) as h, sync_playwright() as p:
        sid = new_session_id()
        first = drive_turn(h, "first question about tea", [], session_id=sid)
        second = drive_turn(h, "second question about coffee", [], session_id=sid)
        assert first.finished and second.finished
        thread_id = first.thread_id or second.thread_id
        assert thread_id

        host = h.base.split("//", 1)[1].split(":", 1)[0]
        browser = p.chromium.launch()
        try:
            # ── English: list, restore, then the chat ──
            context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
            pg = context.new_page()
            errors: list[str] = []
            pg.on("pageerror", lambda e: errors.append(str(e)))
            _arrive_at_time_travel(pg, h.base)
            _open_thread(pg, thread_id)
            pg.wait_for_function("() => document.querySelectorAll('.replay-turn-head').length === 2",
                                 timeout=30000)
            ids = pg.evaluate(
                "() => [...document.querySelectorAll('.replay-snap-card')].map((c) => c.dataset.snapshot)")
            assert ids and len(ids) == len(set(ids)), f"a step listed twice: {ids}"
            heads = pg.evaluate("() => [...document.querySelectorAll('.replay-turn-head')].map((h) => h.textContent)")
            assert heads == ["Turn 1", "Turn 2"], heads

            # The last step of turn 1: the first answer is in it.
            pg.evaluate(
                "() => { const t = document.querySelectorAll('.replay-turn')[0];"
                " const cards = t.querySelectorAll('.replay-snap-card'); cards[cards.length - 1].click(); }")
            pg.wait_for_function(
                "() => document.getElementById('replay-detail-messages').textContent.includes('Answer to')",
                timeout=20000)
            pg.locator("#replay-snapshot-detail button.btn-primary").click()
            confirm = pg.locator(".modal-footer button.btn-danger")
            confirm.wait_for(state="visible", timeout=10000)
            assert "Files written" in pg.locator(".modal-body").first.inner_text()
            confirm.click()
            # The steps after the restored one are gone with the turn.
            pg.wait_for_function("() => document.querySelectorAll('.replay-turn-head').length === 1",
                                 timeout=30000)

            # The chat, reached by the sidebar, shows the first turn only.
            _soft_nav(pg, "/chat")
            pg.wait_for_function("() => !!window.KazmaChat", timeout=20000)
            item = pg.locator(f'.session-item[data-session-id="{sid}"]')
            item.wait_for(state="visible", timeout=20000)
            item.click()
            pg.wait_for_function(
                "() => [...document.querySelectorAll('.message-user')].some((m) => m.textContent.includes('tea'))",
                timeout=20000)
            shown = pg.evaluate("() => document.getElementById('chat-messages')"
                                " ? document.getElementById('chat-messages').textContent"
                                " : document.body.textContent")
            assert "first question about tea" in shown
            assert "coffee" not in shown, "the rewound turn is still in the chat"
            assert errors == [], errors
            context.close()

            # The next question reaches the model without the rewound turn.
            third = drive_turn(h, "third question about water", [], session_id=sid)
            assert third.finished
            sent = _user_and_answer_texts(script.seen[-1])
            assert any("tea" in s for s in sent), sent
            assert not any("coffee" in s for s in sent), f"the rewound turn reached the model: {sent}"

            # ── Arabic: the page, every tab, no English interface text ──
            context = browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
            context.add_cookies([{"name": "kazma-lang", "value": "ar", "domain": host, "path": "/"}])
            pg = context.new_page()
            _arrive_at_time_travel(pg, h.base)
            assert pg.evaluate("() => document.documentElement.dir") == "rtl"
            _open_thread(pg, thread_id)
            pg.wait_for_function("() => document.querySelectorAll('.replay-snap-card').length > 0",
                                 timeout=30000)
            pg.evaluate("() => document.querySelector('.replay-snap-card').click()")
            pg.wait_for_function(
                "() => document.getElementById('replay-detail-messages').children.length > 0", timeout=20000)
            problems = [f"timeline: {s}" for s in english_on(pg)]
            for tab in ("diff", "about"):
                pg.evaluate("(t) => window.KazmaReplay.switchTab(t)", tab)
                problems += [f"{tab}: {s}" for s in english_on(pg)]
            assert not problems, "English on the Arabic Time Travel page:\n  " + "\n  ".join(problems)
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("answer", ["error", "empty"])
def test_a_failed_step_list_is_shown_as_an_error(answer: str):
    """TT-10: a 500 reads as an error. ``empty`` is the negative control: an
    honest empty list reads as "no steps", and the error check sees none."""
    from playwright.sync_api import sync_playwright

    script = _EchoScript(steps=[])
    with unified_turn_server(script) as h, sync_playwright() as p:
        run = drive_turn(h, "a question for the list", [], session_id=new_session_id())
        assert run.finished and run.thread_id
        browser = p.chromium.launch()
        try:
            pg = browser.new_page()
            if answer == "error":
                pg.route("**/api/replay/snapshots/**", lambda route: route.fulfill(
                    status=500, content_type="application/json", body='{"error": "internal_error"}'))
            else:
                pg.route("**/api/replay/snapshots/**", lambda route: route.fulfill(
                    status=200, content_type="application/json",
                    body='{"snapshots": [], "turns": [], "count": 0}'))
            _arrive_at_time_travel(pg, h.base)
            _open_thread(pg, run.thread_id)
            pg.wait_for_function(
                "() => !document.getElementById('replay-timeline-list').textContent.includes('Loading')",
                timeout=20000)
            listed = pg.locator("#replay-timeline-list").inner_text()
            error = pg.locator("#replay-timeline-list .replay-load-error[role=alert]")
            if answer == "error":
                assert error.count() == 1, listed
                assert "HTTP 500" in listed, listed
                assert "No steps" not in listed, listed
            else:
                assert error.count() == 0, listed
                assert "No steps" in listed, listed
        finally:
            browser.close()
