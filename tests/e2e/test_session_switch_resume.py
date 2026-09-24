"""Switching chat sessions mid-stream and back keeps the live turn whole.

Seen once on 2026-09-24 while probing another report: after a sidebar
session switch and back, the live reply briefly had no Thoughts/Activity
fold until the turn finished. It did not recur in four further runs, and
the resume path was traced end to end (journal re-attach from the cursor,
tokens reaching the document from the bus and the re-attached stream), so
this locks the behaviour rather than a guessed fix: if the fold drops or
the stream stops after a switch again, this fails with the evidence.

Runs the real app with a scripted, deliberately slow model: narration, one
tool, then a long answer streamed over ~20 seconds.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, Step, unified_turn_server  # noqa: E402

LONG = " ".join(f"sentence{n} of the final answer." for n in range(1, 60))

BUBBLE_JS = """() => {
  var bs = Array.from(document.querySelectorAll('.message-assistant'))
    .filter(n => !n.parentElement.closest('.message-assistant'));
  var b = bs[bs.length - 1];
  if (!b) return null;
  var t = ((b.querySelector('.message-text') || {}).textContent || '').trim();
  return {fold: !!b.querySelector('.agent-progress'),
          rows: b.querySelectorAll('.agent-progress-step').length,
          chars: t.length, done: t.indexOf('sentence59') >= 0};
}"""


@pytest.fixture
def slow_task_server():
    from kazma_core.llm_provider import LLMProvider
    from kazma_core.llm_stream import StreamDelta

    script = Script(
        steps=[Step(tool="file_list", args={"path": "."},
                    narration="Let me look at the workspace first, then I will answer.")],
        final=LONG,
    )
    with unified_turn_server(script) as h:
        orig = LLMProvider.chat_stream

        async def _slow(self, messages, *a, **kw):
            resp = script.respond(list(messages or []))
            text = str(getattr(resp, "content", "") or "")
            for i in range(0, len(text), 30):
                await asyncio.sleep(0.3)
                yield StreamDelta(content=text[i : i + 30])
            yield StreamDelta(response=resp)

        LLMProvider.chat_stream = _slow
        try:
            yield h
        finally:
            LLMProvider.chat_stream = orig


def _click_session(pg, sid: str) -> None:
    pg.evaluate(
        "(sid) => document.querySelector('.session-item[data-session-id=\"' + sid + '\"]').click()",
        sid,
    )


def test_switching_sessions_mid_stream_keeps_the_fold_and_the_stream(slow_task_server):
    from playwright.sync_api import sync_playwright

    base = slow_task_server.base
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            pg = browser.new_page()
            pg.goto(f"{base}/chat", wait_until="domcontentloaded")
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)

            # A finished conversation to switch to.
            pg.evaluate("() => window.KazmaChat.newSession()")
            pg.wait_for_timeout(800)
            pg.fill("#chat-input", "other conversation")
            pg.evaluate("() => window.KazmaChat.sendMessage()")
            pg.wait_for_function(
                "() => (document.body.textContent || '').indexOf('sentence59') >= 0",
                timeout=120000,
            )

            # The live task.
            pg.evaluate("() => window.KazmaChat.newSession()")
            pg.wait_for_timeout(800)
            pg.fill("#chat-input", "the task")
            pg.evaluate("() => window.KazmaChat.sendMessage()")
            pg.wait_for_timeout(3500)
            mine = pg.evaluate(
                "() => document.querySelector('.session-item.active').dataset.sessionId")
            other = [
                s for s in pg.evaluate(
                    "() => Array.from(document.querySelectorAll('.session-item'))"
                    ".map(x => x.dataset.sessionId)")
                if s != mine
            ][0]

            _click_session(pg, other)
            pg.wait_for_timeout(2500)
            _click_session(pg, mine)

            pg.wait_for_timeout(1500)
            first = pg.evaluate(BUBBLE_JS)
            pg.wait_for_timeout(1800)
            second = pg.evaluate(BUBBLE_JS)
            assert first and first["fold"] and first["rows"] >= 1, (
                f"the live turn came back without its fold: {first}")
            assert second["chars"] > first["chars"] or second["done"], (
                f"the reply stopped streaming after the switch: {first} -> {second}")

            pg.wait_for_function(
                "() => { var bs = document.querySelectorAll('.message-assistant');"
                " var t = bs.length ? bs[bs.length - 1].textContent : '';"
                " return t.indexOf('sentence59') >= 0; }",
                timeout=60000,
            )
            final = pg.evaluate(BUBBLE_JS)
            assert final["fold"] and final["done"], final
        finally:
            browser.close()
