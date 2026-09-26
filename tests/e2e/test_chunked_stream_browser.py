"""A chat turn read through a stream cut the way Cloudflare Tunnel cuts it.

Live 2026-09-26: over the tunnel, a turn showed no thoughts, the Stop button
went back to Send within seconds, and the approval card appeared only after a
refresh; another run threw "Cannot read properties of null (reading
'tool_name')" mid-turn. Two defects, and a direct connection hid both:

* the page's stream reader kept a frame's half-built state for ONE network
  read, so a frame cut across two reads was lost and its orphaned data line
  corrupted the next frame (``streaming.js:createSseParser`` now keeps it for
  the stream);
* the server labelled every frame on the attach stream ``replay: true`` --
  including the live approval of the turn the tab had just started -- and
  the page, correctly, refuses to paint a pending card from history; the
  card waited for the reconciler's poll (``_frame_from_journaled(replay=)``).

Uvicorn on loopback delivers frames whole, which is why every browser test
passed. Here the app is wrapped in :class:`RechunkedStreams`, which cuts each
stream body into 1-40 byte pieces with a pause between them, and two turns
are sent, each with an ungated tool and a gated one. Nothing is mocked but
the model; every Approve is a real click on a real button.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    RechunkedStreams,
    Script,
    Step,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_LIVE_CARD = ".turn-approvals-rows .hitl-approval-card button:not([disabled])"


class _EveryTurnScript(Script):
    """Replay the steps for EVERY user turn and name the question in the answer.

    The harness Script counts tool results over the whole conversation, so a
    second turn would skip straight to its answer. Counting from the latest
    user message gives each turn the same shape, and the answer carries the
    turn's marker so a reply painted into the wrong block is caught.
    """

    def respond(self, messages: list[dict[str, Any]]) -> Any:
        from kazma_core.llm_provider import LLMResponse, ToolCall

        msgs = [m for m in messages or [] if isinstance(m, dict)]
        roles = [str(m.get("role") or m.get("type") or "").lower() for m in msgs]
        last_user = max((i for i, r in enumerate(roles) if r in ("user", "human")), default=0)
        marker = re.search(r"turn-\d+", str(msgs[last_user].get("content") if msgs else ""))
        done = sum(1 for r in roles[last_user:] if r == "tool")
        if done >= len(self.steps):
            self.calls.append("final")
            name = marker.group(0) if marker else "turn-?"
            return LLMResponse(
                content=f"Answer for {name}: the scaffold note is written. " * 3,
                finish_reason="stop",
                model="harness",
            )
        step = self.steps[done]
        self.calls.append(step.tool)
        return LLMResponse(
            content=step.narration,
            tool_calls=[
                ToolCall(id=f"call_{len(self.calls)}_{step.tool}", name=step.tool, arguments=dict(step.args))
            ],
            finish_reason="tool_calls",
            model="harness",
        )


@pytest.fixture
def harness(tmp_path) -> Iterator[Harness]:
    script = _EveryTurnScript(
        steps=[
            Step(tool="current_datetime", args={}, narration="Checking the time first."),
            Step(
                tool="file_write",
                args={"path": str(tmp_path / "notes" / "scaffold.md"), "content": "# notes"},
                narration="Writing the scaffold note now.",
            ),
        ]
    )
    with unified_turn_server(script, asgi_wrapper=RechunkedStreams) as h:
        yield h


@pytest.fixture
def page(harness: Harness):
    from playwright.sync_api import sync_playwright

    console: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            pg = context.new_page()
            pg.on("console", lambda m: console.append(m.type + ": " + m.text))
            pg.on("pageerror", lambda e: console.append("pageerror: " + str(e)))
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
            pg.wait_for_function("() => !!window.KazmaChat && !!window.KazmaStream")
            pg.console_lines = console  # type: ignore[attr-defined]
            yield pg
        finally:
            context.close()
            browser.close()


#: Records, per approval frame the stream delivers: was it labelled history,
#: and was a live card on screen when its handler returned. Also counts the
#: network reads of every chat stream, to prove the cutting reached the page.
_RECORDER_JS = r"""() => {
  if (window.__cut) return;
  const P = window.__cut = { approvals: [], reads: 0, bytes: 0 };
  const sse = window.KazmaStream.sse;
  window.KazmaStream.sse = function (url, body, cbs) {
    const fn = cbs && cbs.onApprovalRequired;
    if (typeof fn === 'function') {
      cbs.onApprovalRequired = function (data) {
        const out = fn.apply(this, arguments);
        P.approvals.push({
          replay: !!(data && data.replay),
          painted: !!document.querySelector('__LIVE_CARD__'),
        });
        return out;
      };
    }
    return sse.apply(this, arguments);
  };
  const fetch0 = window.fetch;
  window.fetch = function (input) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    const p = fetch0.apply(this, arguments);
    if (url.indexOf('/api/chat/stream') >= 0) {
      p.then((resp) => {
        if (!resp.body) return;
        const reader = resp.clone().body.getReader();
        const pump = () => reader.read().then((r) => {
          if (r.done) return;
          P.reads += 1;
          P.bytes += r.value.length;
          pump();
        }).catch(() => {});
        pump();
      }).catch(() => {});
    }
    return p;
  };
}""".replace("__LIVE_CARD__", _LIVE_CARD)

_BUBBLES_JS = """() => Array.from(document.querySelectorAll('.message-assistant')).map((b) => ({
  answer: ((b.querySelector('.message-text') || {}).textContent || '').trim(),
  completed: !!b.querySelector('.turn-header.is-completed'),
  rows: b.querySelectorAll('.turn-approvals-rows .hitl-approval-card').length,
}))"""


def _send(pg, text: str) -> None:
    pg.fill("#chat-input", text)
    pg.evaluate("() => window.KazmaChat.sendMessage()")


def test_turns_read_through_a_cut_stream_paint_whole(page, harness: Harness) -> None:
    page.evaluate(_RECORDER_JS)

    for n in (1, 2):
        before = len(page.evaluate("() => window.__cut.approvals"))
        _send(page, f"turn-{n}: set up the scaffold note")
        try:
            page.wait_for_selector(_LIVE_CARD, timeout=90000)
        except Exception:
            raise AssertionError(
                f"turn {n}: no actionable approval card. Bubbles: "
                + repr(page.evaluate(_BUBBLES_JS))
                + " console: "
                + repr(page.console_lines[-12:])  # type: ignore[attr-defined]
            ) from None

        # The approval frame of the turn this tab started is LIVE, and it
        # paints the card itself -- not the reconciler's poll after it.
        delivered = page.evaluate("() => window.__cut.approvals")[before:]
        assert delivered, f"turn {n}: the stream never delivered its approval frame"
        assert not delivered[0]["replay"], (
            f"turn {n}: a live approval arrived labelled replay: {delivered}"
        )
        assert delivered[0]["painted"], (
            f"turn {n}: the approval frame did not paint its own card: {delivered}"
        )

        page.click(_LIVE_CARD)
        page.wait_for_function(
            "(n) => { const b = document.querySelectorAll('.message-assistant');"
            " const last = b[b.length - 1];"
            " return !!last && !!last.querySelector('.turn-header.is-completed')"
            " && ((last.querySelector('.message-text') || {}).textContent || '')"
            ".indexOf('Answer for turn-' + n) >= 0; }",
            arg=n,
            timeout=90000,
        )

    bubbles = page.evaluate(_BUBBLES_JS)
    assert len(bubbles) == 2, f"two turns, {len(bubbles)} blocks: {bubbles}"
    for n, b in enumerate(bubbles, start=1):
        assert b["answer"].startswith(f"Answer for turn-{n}"), (
            f"block {n} holds another turn's answer: {b['answer'][:80]!r}"
        )
        assert b["answer"].count("Answer for") == 3, (
            f"block {n}: the answer was painted more or less than once: {b['answer']!r}"
        )
        assert b["completed"], f"block {n} never reached Completed: {b}"
        assert b["rows"] == 1, f"block {n}: {b['rows']} approval rows"

    diag = page.evaluate("() => window.KazmaChat.diagnostics()") or []
    broken = [d for d in diag if d.get("e") in ("sse-error", "sse-frame-error")]
    assert not broken, f"the stream failed while reading cut frames: {broken}"
    lost = [c for c in page.console_lines if "SSE stream lost" in c or "stream continues" in c]  # type: ignore[attr-defined]
    assert not lost, f"frames were lost or failed: {lost}"

    # The instrument: the server really cut the bodies, and the page really
    # read them in pieces. Without this a green run could mean "nothing was
    # cut" (AGENTS.md §28, measure the instrument first).
    wrapper = harness.app
    assert isinstance(wrapper, RechunkedStreams)
    assert wrapper.pieces > 5 * wrapper.bodies, (wrapper.pieces, wrapper.bodies)
    probe = page.evaluate("() => window.__cut")
    assert probe["reads"] >= 60 and probe["bytes"] / probe["reads"] < 200, probe


@pytest.fixture
def two_tabs(harness: Harness):
    """Two tabs of ONE browser (shared localStorage, so the same chat)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        try:
            sender = context.new_page()
            sender.goto(f"{harness.base}/chat", wait_until="domcontentloaded", timeout=30000)
            sender.locator("#chat-input").wait_for(state="visible", timeout=20000)
            sender.wait_for_function("() => !!window.KazmaChat && !!window.KazmaStream")
            yield context, sender
        finally:
            context.close()
            browser.close()


def _run_turn(pg, n: int) -> None:
    _send(pg, f"turn-{n}: set up the scaffold note")
    pg.wait_for_selector(_LIVE_CARD, timeout=90000)
    pg.click(_LIVE_CARD)
    pg.wait_for_function(
        "(n) => { const b = document.querySelectorAll('.message-assistant');"
        " const last = b[b.length - 1];"
        " return !!last && !!last.querySelector('.turn-header.is-completed')"
        " && ((last.querySelector('.message-text') || {}).textContent || '')"
        ".indexOf('Answer for turn-' + n) >= 0; }",
        arg=n,
        timeout=90000,
    )


def test_a_watching_tab_shows_each_turn_in_its_own_block(two_tabs) -> None:
    """Live 2026-09-26: a second browser window on the same chat showed the
    approved cards and replies "aligned like they are one task" until a
    refresh. The watching tab never got a user row for a turn another tab
    sent, so each new turn's frames found the previous turn's block as the
    open one and painted into it; its catch-up attach replayed from seq 0
    and put earlier blocks back to "working". The server now fans the
    question out (user_message) and the page attaches from what it has read.
    """
    context, sender = two_tabs
    _run_turn(sender, 1)

    watcher = context.new_page()
    watcher.goto(sender.url, wait_until="domcontentloaded", timeout=30000)
    watcher.wait_for_function(
        "() => !!window.KazmaChat && document.querySelectorAll('.message-assistant').length === 1",
        timeout=30000,
    )
    # A watcher that never regresses a finished block: record every render
    # of a completed header going back to open.
    watcher.evaluate(
        """() => {
          window.__regressed = [];
          const seen = new Set();
          new MutationObserver(() => {
            document.querySelectorAll('.message-assistant').forEach((b) => {
              const id = b.getAttribute('data-turn-id') || '';
              const done = !!b.querySelector('.turn-header.is-completed');
              if (done) seen.add(id);
              else if (seen.has(id)) window.__regressed.push(id);
            });
          }).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
        }"""
    )

    for n in (2, 3):
        _run_turn(sender, n)
        watcher.wait_for_function(
            "(n) => Array.from(document.querySelectorAll('.message-assistant .message-text'))"
            ".some((t) => (t.textContent || '').indexOf('Answer for turn-' + n) >= 0)",
            arg=n,
            timeout=60000,
        )

    watcher.wait_for_function(
        "() => Array.from(document.querySelectorAll('.message-assistant'))"
        ".every((b) => !!b.querySelector('.turn-header.is-completed'))",
        timeout=30000,
    )
    users = watcher.evaluate(
        "() => Array.from(document.querySelectorAll('.message-user')).map((u) => (u.textContent || '').trim())"
    )
    blocks = watcher.evaluate(_BUBBLES_JS)
    assert len(users) == 3 and all(f"turn-{n}" in users[n - 1] for n in (1, 2, 3)), users
    assert len(blocks) == 3, f"three turns, {len(blocks)} blocks in the watching tab: {blocks}"
    for n, b in enumerate(blocks, start=1):
        assert b["answer"].startswith(f"Answer for turn-{n}"), (n, b["answer"][:80])
        assert b["rows"] == 1, f"block {n} holds {b['rows']} approval rows"
    assert watcher.evaluate("() => window.__regressed") == [], (
        "a finished block in the watching tab went back to working"
    )

    # And the sender never painted its own question twice.
    sent = sender.evaluate("() => document.querySelectorAll('.message-user').length")
    assert sent == 3, f"the sending tab shows {sent} user rows for three sends"
