"""A finished answer that opens with a plan fence stays finished on reload.

Live 2026-09-26: after a page reload, a completed turn whose answer began
with a ```plan fence grew a second header -- "Kazma is thinking… Stop" --
under its answer, its thoughts panel was stuck "active", and the render
invariant reported ``text-missing,duplicate-region:turn-header:2``. The
model had glued its answer to the fence's closer (```Deploy test 4 — …),
which it often does. The text painter, which paints every turn including
finished ones, fed the plan into the LIVE turn's progress ("Plan locked (n
steps)"), and a live progress event is what paints a working header. Plan
ingestion belongs to the running turn only.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

#: The shape the model produced live: the closer glued to the answer.
_GLUED = (
    "```plan\n- Run the snippet\n- Report the outcome\n- Stop there\n"
    "```Deploy test — **denied, nothing executed.** The tool was not approved."
)

_BLOCKS_JS = """() => Array.from(document.querySelectorAll('.message-assistant')).map((b) => ({
  headers: b.querySelectorAll('.turn-header').length,
  working: b.querySelectorAll('.turn-header.is-working').length,
  activePanels: b.querySelectorAll('.agent-progress.is-active').length,
  runningSteps: b.querySelectorAll('.agent-progress-step.state-running').length,
  answer: ((b.querySelector('.message-text') || {}).textContent || '').trim().slice(0, 60),
}))"""

#: The last bubble's meta line: whether it kept its time, and the text after
#: the time -- the turn's stats and model, which live and reload must agree on.
_META_JS = """() => {
  const b = Array.from(document.querySelectorAll('.message-assistant')).pop();
  const meta = b && b.querySelector('.message-meta');
  if (!meta) return {hasTime: false, stats: ''};
  const time = meta.querySelector('time');
  const text = meta.textContent || '';
  return {hasTime: !!time, stats: time ? text.slice(text.indexOf(time.textContent) + time.textContent.length) : text};
}"""


class _CountedScript(Script):
    """The final answer reports usage, like a real provider does."""

    def respond(self, messages):
        resp = super().respond(messages)
        resp.usage = {"prompt_tokens": 1000, "completion_tokens": 234}
        resp.cost_usd = 0.0042
        return resp


@pytest.fixture
def harness() -> Iterator[Harness]:
    with unified_turn_server(_CountedScript(steps=[], final=_GLUED)) as h:
        yield h


@pytest.fixture
def page(harness: Harness):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
            pg.wait_for_function("() => !!window.KazmaChat && !!window.KazmaStream")
            yield pg
        finally:
            context.close()
            browser.close()


def _finished_turn_is_whole(pg) -> list[dict]:
    pg.wait_for_function(
        "() => { const b = document.querySelectorAll('.message-assistant');"
        " const last = b[b.length - 1];"
        " return !!last && !!last.querySelector('.turn-header.is-completed')"
        " && ((last.querySelector('.message-text') || {}).textContent || '')"
        ".indexOf('Deploy test') >= 0; }",
        timeout=60000,
    )
    pg.wait_for_timeout(1500)  # the post-load resync and its render pass
    return pg.evaluate(_BLOCKS_JS)


def test_a_plan_fenced_answer_stays_finished_after_a_reload(page) -> None:
    page.fill("#chat-input", "run the deploy test")
    page.evaluate("() => window.KazmaChat.sendMessage()")
    live = _finished_turn_is_whole(page)
    assert live[-1]["headers"] == 1 and live[-1]["working"] == 0, live
    # A finished turn has nothing running (a status step used to keep its
    # spinner forever).
    assert live[-1]["runningSteps"] == 0, live
    # The running turn still shows its plan (as a thought, in this flow).
    plan = page.evaluate(
        "() => { const b = Array.from(document.querySelectorAll('.message-assistant')).pop();"
        " return ((b && b.querySelector('.agent-progress')) || {}).textContent || ''; }"
    )
    assert "Run the snippet" in plan, plan[:200]
    # The done frame writes the stats after the time; it used to overwrite it.
    live_meta = page.evaluate(_META_JS)
    assert live_meta["hasTime"], live_meta
    # The turn's totals (the harness makes several calls of 1,234 tokens).
    assert re.search(r"[\d,]+ tokens · \$0\.\d{4} · ", live_meta["stats"]), live_meta

    page.reload(wait_until="domcontentloaded")
    page.wait_for_function("() => !!window.KazmaChat")
    blocks = _finished_turn_is_whole(page)
    last = blocks[-1]
    assert last["headers"] == 1, f"a second header appeared on reload: {blocks}"
    assert last["working"] == 0 and last["activePanels"] == 0, blocks
    assert last["runningSteps"] == 0, blocks
    assert last["answer"].startswith("Deploy test"), blocks
    # The turn's stats come back with it, the same line the live frame wrote
    # (they used to vanish on reload).
    reloaded_meta = page.evaluate(_META_JS)
    assert reloaded_meta == live_meta, (live_meta, reloaded_meta)
    invariants = page.evaluate(
        "() => (window.KazmaChat.diagnostics() || []).filter((d) => d.e === 'render-invariant')"
    )
    assert invariants == [], invariants
