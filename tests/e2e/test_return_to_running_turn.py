"""Leaving Chat while a turn runs and coming back shows it running.

Live 2026-10-16 11:16-11:22 UTC: the owner sent a task, opened Memory, came
back to Chat. The server ran the turn on and paused it for a python_exec
approval; the page showed it dead ("Reconnecting...", nothing arriving), a
refresh did not help, no approval reached the server and the watchdog
auto-denied it after 300 s.

The turn journal restarts at seq 1 on every server restart (and after an hour
idle), so the delivery cursor the browser saved in localStorage is ahead of
it. The page re-attached with it; the server answered GAP and named its head;
the page kept the saved cursor, so every re-attach failed the same way until
the reopen budget ran out -- and while paused (no budget) it looped. Here the
saved cursor is planted at 5000, the state every open chat is in after a
restart. Before the fix these scenarios ended on "Attach declined -- reopen
budget exhausted" with every attach from seq=5000. Unit gate and negative
control: ``tests/test_delivery_cursor_gap.py``.
"""

from __future__ import annotations

import asyncio
import os
from unittest import mock

import pytest

pytest.importorskip("playwright.sync_api")

from tests.e2e._unified_turn_harness import Script, Step, unified_turn_server  # noqa: E402

#: Every model call waits this long: the turn is still RUNNING when the page
#: comes back, as the live one was.
DELAY_S = 6.0
STALE = 5000

LIVE_APPROVE = "() => document.querySelectorAll('.hitl-approval-card .hitl-approve:not([disabled])').length > 0"

PLANT_STALE_CURSOR = """(seq) => {
  const st = window.Alpine && Alpine.store && Alpine.store('agent');
  const keys = Object.keys(localStorage).filter(k => k.startsWith('kazma.deliveryCursor.'));
  if (st && st.sessionId) keys.push('kazma.deliveryCursor.' + st.sessionId);
  const uniq = [...new Set(keys)];
  uniq.forEach(k => localStorage.setItem(k, String(seq)));
  return uniq.length;
}"""


def _soft_nav(pg, href: str) -> None:
    """Follow a sidebar link the way a person does: a click nav.js handles."""
    pg.evaluate(
        "(href) => document.querySelector('.sidebar .nav-links a[href=\"' + href + '\"]').click()", href)
    pg.wait_for_function("(p) => location.pathname === p", arg=href, timeout=20000)


def _slow_model():
    from kazma_core.llm_provider import LLMProvider

    inner = LLMProvider.chat_stream

    async def slow(self, messages, *a, **kw):
        await asyncio.sleep(DELAY_S)
        async for delta in inner(self, messages, *a, **kw):
            yield delta

    return mock.patch.object(LLMProvider, "chat_stream", slow)


@pytest.mark.parametrize(
    "how,running,stale",
    [
        ("sidebar", True, True),     # the live incident
        ("reload", True, True),      # "refreshing did not help"
        ("reload", False, True),     # paused at the approval: it looped
        ("reload", True, False),     # control: a cursor the journal holds
    ],
)
def test_a_turn_left_and_returned_to_stays_live(how, running, stale):
    from playwright.sync_api import sync_playwright

    script = Script(
        steps=[Step(tool="file_write", args={"path": "", "content": "x"},
                    narration="Writing the file.")],
        final="All done here.",
    )
    console: list[str] = []
    approve_status: list[int] = []
    with unified_turn_server(script) as h, sync_playwright() as p:
        script.steps[0].args["path"] = os.path.join(h.data_dir, "out.txt")
        with (_slow_model() if running else mock.patch.dict({}, {})):
            browser = p.chromium.launch()
            try:
                pg = browser.new_page()
                pg.on("console", lambda m: console.append(m.text))
                pg.on("response", lambda r: approve_status.append(r.status)
                      if "/api/approve/" in r.url else None)

                pg.goto(f"{h.base}/chat", wait_until="domcontentloaded")
                pg.locator("#chat-input").wait_for(state="visible", timeout=30000)
                pg.wait_for_function("() => !!window.KazmaChat", timeout=20000)
                pg.fill("#chat-input", "please write the file")
                pg.evaluate("() => window.KazmaChat.sendMessage()")
                if running:
                    pg.wait_for_timeout(1500)   # mid model call
                else:
                    pg.wait_for_function(LIVE_APPROVE, timeout=60000)

                if how == "sidebar":
                    _soft_nav(pg, "/memory")
                    pg.wait_for_timeout(1500)
                    if stale:
                        assert pg.evaluate(PLANT_STALE_CURSOR, STALE) >= 1
                    _soft_nav(pg, "/chat")
                else:
                    if stale:
                        assert pg.evaluate(PLANT_STALE_CURSOR, STALE) >= 1
                    pg.reload(wait_until="domcontentloaded")
                pg.locator("#chat-input").wait_for(state="visible", timeout=30000)

                # The turn reaches its approval on the returned page, live.
                pg.wait_for_function(LIVE_APPROVE, timeout=int((DELAY_S + 30) * 1000))
                pg.locator(".hitl-approval-card .hitl-approve:not([disabled])").last.click()
                pg.wait_for_function("() => /All done here/.test(document.body.textContent)",
                                     timeout=int((DELAY_S + 30) * 1000))
                pg.wait_for_function(
                    "() => { const h = [...document.querySelectorAll('.message-assistant .turn-header')].pop();"
                    " return !!h && /Completed/.test(h.textContent); }",
                    timeout=30000)
            finally:
                browser.close()

    assert approve_status and approve_status[-1] == 200, approve_status
    attaches = [c for c in console if "Attaching journal" in c]
    exhausted = [c for c in console if "reopen budget exhausted" in c]
    stale_attaches = [c for c in attaches if f"from seq={STALE}" in c]
    assert not exhausted, exhausted
    # The server may answer the stale cursor once; after that the page reads
    # from the head it named. Before the fix every attach used it.
    assert len(stale_attaches) <= 1, stale_attaches
    # Bounded: the paused case looped dozens of times.
    assert len(attaches) <= 6, attaches
