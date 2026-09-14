"""Delivery must converge: the reconciler, and a stream that knows it is dead.

THE INCIDENT (operator's install, 2026-09-14 15:44 UTC)

    :26.783  SSE HITL interrupt -- awaiting approval; server closes the body
    :28.866  POST /api/approve/<thread> 200
    :43.307  Reply persisted chars=3725 interrupted=False

with ZERO /api/chat/stream requests in between. The approval was honoured and
the answer was written. It was never delivered, because every recovery path in
chat.js asked a client-side question first -- is the stream alive, is the card
terminal, are we awaiting an approval -- and `activeStream` still held a handle
to a stream the server had already closed. Each guard declined. The bubble kept
saying the agent was waiting for permission.

TWO CHANGES, AND THEY ARE DIFFERENT IN KIND

1. `isClosed()` removes the guess. Liveness is reported by the stream, where it
   is a fact, instead of inferred from whether a variable is non-null.
   Exercised for real below.

2. The reconciler removes the DEPENDENCE on any single path being right. While
   a turn might still be undelivered it asks the server on a timer, with no
   guard on whether to ask, and stops only when the server says the turn is
   over twice running. Whichever path breaks next, the next tick heals it.

The second is the one that matters. The first is a better guess; the second is
what makes guessing non-fatal.

ON THE SHAPE OF THESE TESTS: `streaming.js` is a real module and is exercised
as one. `chat.js` is ~7,700 lines in an IIFE around a live DOM and cannot be
imported, so its half is structural -- it pins the no-latch property against
copy-paste and does NOT prove that a browser recovers. Worth keeping in view
when reading a green run.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_JS = _ROOT / "kazma-ui" / "kazma_ui" / "static" / "js"
_CHAT = _JS / "chat.js"
_STREAM = _JS / "streaming.js"

# A VM with a fetch that yields __FRAMES__ and then ends the body, so the
# stream reaches its terminal state the same way a real HITL pause does.
_HARNESS = """
const fs = require('fs'), vm = require('vm');
const frames = __FRAMES__;
const failWith = __FAIL__;
const enc = new TextEncoder();
let i = 0;
const ctx = {
  TextDecoder, TextEncoder, AbortController, setTimeout, clearTimeout,
  console, JSON, Promise,
  fetch: function () {
    if (failWith) return Promise.reject(new Error(failWith));
    return Promise.resolve({
      ok: true,
      body: { getReader: function () { return { read: function () {
        if (i < frames.length) return Promise.resolve(
          { done: false, value: enc.encode(frames[i++]) });
        return Promise.resolve({ done: true });
      } }; } },
    });
  },
};
ctx.globalThis = ctx; ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(__SRC__, 'utf8'), ctx);
const out = [];
const h = ctx.KazmaStream.sse('/x', {}, {
  onToken: function () { out.push('token'); },
  onDone: function () { out.push('done'); },
  onError: function (m) { out.push('error:' + m); },
});
const report = function (label) {
  process.stdout.write(JSON.stringify(
    { label: label, closed: h.isClosed(), events: out }) + '\\n');
};
__EARLY__
report('immediately');
setTimeout(function () { report('after'); }, 80);
"""


def _node(script: str) -> str:
    proc = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return proc.stdout


def _run(frames: list[str], *, fail: str = "", early: str = "") -> list[dict]:
    script = (
        _HARNESS.replace("__FRAMES__", json.dumps(frames))
        .replace("__FAIL__", json.dumps(fail))
        .replace("__SRC__", json.dumps(str(_STREAM)))
        .replace("__EARLY__", early)
    )
    return [json.loads(line) for line in _node(script).splitlines() if line.strip()]


class TestTheStreamKnowsWhenItIsDead:
    def test_a_fresh_stream_is_open(self) -> None:
        assert _run(["data: {}\n\n"])[0]["closed"] is False

    def test_it_is_closed_once_the_body_ends(self) -> None:
        """The exact case that stranded the operator's reply."""
        last = _run(['event: token\ndata: {"content":"hi"}\n\n'])[-1]
        assert last["closed"] is True, last
        assert "done" in last["events"], last

    def test_a_transport_failure_reaches_onError(self) -> None:
        """The catch handler used to throw before it could call onError.

        `finishStream` and `isBenignStreamClose` were declared inside the
        fetch `.then` while the `.catch` -- chained on the OUTER promise --
        called both. Not lexically visible there, so the handler raised
        `ReferenceError` every time it ran and onError was never reached.
        chat.js nulls `activeStream` and resyncs from onError, so any
        transport failure left a dead handle that looked alive, with no
        recovery. This test is what found it.
        """
        last = _run([], fail="boom")[-1]
        assert last["closed"] is True, last
        assert any(e.startswith("error:") for e in last["events"]), last

    def test_a_benign_close_completes_the_turn_instead_of_erroring(self) -> None:
        """The same handler's other branch, equally unreachable before."""
        last = _run([], fail="Failed to fetch")[-1]
        assert "done" in last["events"], last
        assert not any(e.startswith("error:") for e in last["events"]), last

    def test_abort_closes_it_immediately(self) -> None:
        assert _run(["data: {}\n\n"], early="h.abort();")[0]["closed"] is True


def _code_only(text: str) -> str:
    """Prose about the bug is not evidence that the bug is fixed."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


@pytest.fixture(scope="module")
def chat() -> str:
    return _CHAT.read_text(encoding="utf-8")


class TestTheReconcilerCannotGiveUp:
    def test_it_exists_and_is_driven_by_a_timer(self, chat: str) -> None:
        code = _code_only(chat)
        assert "function _reconcileTick()" in code
        assert "function _scheduleReconcile()" in code
        assert "setTimeout(_reconcileTick" in code

    def test_the_tick_asks_the_server_with_no_guard(self, chat: str) -> None:
        """The whole point: no client-side question gates the request."""
        body = _code_only(chat).split("function _reconcileTick()", 1)[1].split("\n  }", 1)[0]
        before = body.split("_resyncDelivery('reconcile')", 1)[0]
        for belief in (
            "activeStream",
            "_tcIsTerminal",
            "_awaitingApproval",
            "hasInlineApprovalCard",
        ):
            assert belief not in before, (
                "the reconcile request is gated on " + belief + ": " + before.strip()
            )

    def test_it_stops_only_on_repeated_server_idle(self, chat: str) -> None:
        body = _code_only(chat).split("function _reconcileTick()", 1)[1].split("\n  }", 1)[0]
        assert "_turnMayBeUndelivered()" in body
        assert "_RECONCILE_IDLE_TICKS" in body
        assert "_scheduleReconcile()" in body

    def test_idle_requires_more_than_one_observation(self, chat: str) -> None:
        m = re.search(r"_RECONCILE_IDLE_TICKS\s*=\s*(\d+)", chat)
        assert m and int(m.group(1)) >= 2, "one idle reading can race a resync in flight"

    def test_a_hidden_tab_backs_off_rather_than_stops(self, chat: str) -> None:
        code = _code_only(chat)
        assert "_RECONCILE_HIDDEN_MS" in code
        sched = code.split("function _scheduleReconcile()", 1)[1].split("\n  }", 1)[0]
        assert "document.hidden" in sched
        assert "setTimeout(_reconcileTick" in sched

    def test_every_turn_entry_point_starts_it(self, chat: str) -> None:
        code = _code_only(chat)
        for reason in ("'dispatch'", "'approval'", "'hitl'"):
            assert "_startReconciler(" + reason + ")" in code, reason

    def test_a_hitl_pause_starts_it_too(self, chat: str) -> None:
        """An approval can be granted from Telegram; this tab sees no response."""
        body = _code_only(chat).split("function pauseForApproval(data)", 1)[1][:600]
        assert "_startReconciler('hitl')" in body


class TestTheLatchesAreGone:
    def test_the_idle_watchdog_rearms_during_an_approval(self, chat: str) -> None:
        """It used to `return` without re-arming, killing itself for the turn."""
        body = chat.split("function _armTurnWatchdog()", 1)[1].split("\n  }", 1)[0]
        branch = body.split("if (_awaitingApproval) {", 1)[1].split("}", 1)[0]
        assert "_armTurnWatchdog()" in branch, "the approval branch must re-arm"

    def test_liveness_is_asked_not_assumed(self, chat: str) -> None:
        code = _code_only(chat)
        assert "function _streamIsLive()" in code
        assert "activeStream.isClosed()" in code
        resync = code.split("function _resyncDelivery(reason)", 1)[1]
        assert "if (_streamIsLive()) {" in resync

    def test_no_approval_path_is_gated_on_a_handle_again(self, chat: str) -> None:
        code = _code_only(chat)
        offenders = [
            line.strip()
            for line in code.splitlines()
            if "activeStream" in line and "_reopenSseRef" in line
        ]
        assert not offenders, offenders
