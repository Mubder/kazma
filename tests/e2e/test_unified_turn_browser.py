"""What the operator sees: the unified turn block in a real browser.

This is the Phase 3 browser evidence for
``docs/plans/UNIFIED_TURN_BLOCK.md``, and it closes the two oldest open
items in ``docs/plans/HITL_VIEW_MODEL.md``:

    | **1** | Sequential allow-tool: file_write -> Approve -> file_delete ->
    |       | Approve -> reply in the SAME bubble, settled cards above the
    |       | reply | Same harness as 4, plus a second interrupt after resume
    | **4** | Approve then look BEFORE the next /status poll: the card stays
    |       | above the answer because the 200 body carries the view, not
    |       | because a client bit outranks the registry

Both were declared unharnessable on 2026-09-19: "``create_app()`` +
TestClient + ``ainvoke({tool_calls_pending: file_write})`` ran the
supervisor LLM path (HTTP 401, ``turn_failed``) and never ``interrupt()``'d."
That finding is correct for what it tested and is still locked by
``tests/test_f0_hitl_app_graph_spike.py``. The conclusion drawn from it was
too broad: the app graph pauses when the supervisor PRODUCES the tool call.
``tests/e2e/_unified_turn_harness.py`` makes it do so, four times.

Nothing here is mocked except the model, at the provider boundary. Plan
§10: "Do not mock the approval endpoint or manually change the UI to make
sequential approval tests pass." Every Approve below is a real click on a
real button that POSTs ``/api/approve/{thread_id}``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

#: Written into console.log; a module constant so the escape does not
#: have to survive another layer of quoting.
LINE_SEP = chr(10)

from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

PROMPT = "Set up the project scaffold."

#: The layout the plan sketches, as a CSS-class sequence.
EXPECTED_SHAPE = ["turn-header", "agent-progress", "turn-approvals", "message-text"]

_SHAPE_JS = """() => {
  const b = document.querySelectorAll('.message-assistant');
  if (!b.length) return null;
  const c = b[b.length - 1].querySelector('.message-content');
  if (!c) return null;
  const group = c.querySelector('.turn-approvals');
  const rowsHost = group && group.querySelector('.turn-approvals-rows');
  return {
    bubbles: b.length,
    shape: Array.from(c.children).map((x) => x.className.split(' ')[0]),
    groups: c.querySelectorAll('.turn-approvals').length,
    looseCards: Array.from(c.children)
      .filter((x) => x.classList.contains('hitl-approval-card')).length,
    rows: rowsHost ? Array.from(rowsHost.children).map((x) => ({
      gate: x.getAttribute('data-interrupt-id') || '',
      shown: x.getAttribute('data-hitl-shown') || '',
      live: x.querySelectorAll('button:not([disabled])').length,
    })) : [],
    header: (c.querySelector('.turn-header') || {}).textContent || '',
    answer: (c.querySelector('.message-text') || {}).textContent || '',
    foldCollapsed: !!(c.querySelector('.agent-progress')
      && c.querySelector('.agent-progress').classList.contains('is-collapsed')),
    bottomBar: !!document.getElementById('live-task-card'),
  };
}"""


@pytest.fixture
def harness() -> Iterator[Harness]:
    """One app per test, not per module — see ``tests/e2e/conftest.py``."""
    with unified_turn_server() as h:
        yield h


#: Where a failing run leaves its trace, screenshot and console log.
#: Under the repo so a CI step can upload one directory, and gitignored
#: so a local run does not offer them up as changes.
EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "test-artifacts" / "unified-turn"

#: Tests whose recording IS the release evidence rather than a failure
#: diagnostic, so they record whether or not they pass (plan §13).
ALWAYS_RECORD = (
    "test_sequential_allow_tool_in_one_bubble",
    "test_stream_stays_visible_during_action_updates",
)


@pytest.fixture
def evidence(request) -> Iterator[dict]:
    """Collect artifacts for THIS test, and keep them only if they matter.

    Playwright writes a video for the whole context or none of it, and
    names the file only once the context closes — so the decision to keep
    is made here, after the test result is known, not at launch.
    """
    name = request.node.name.split("[")[0]
    out = EVIDENCE_DIR / name
    state = {"dir": out, "record": name in ALWAYS_RECORD, "page": None}
    yield state

    failed = getattr(request.node, "_utb_failed", False)
    keep = failed or state["record"]
    pg = state.get("page")
    if pg is not None and failed:
        # Best-effort: a page that crashed cannot be screenshotted, and a
        # missing screenshot must not replace the real failure.
        try:
            out.mkdir(parents=True, exist_ok=True)
            pg.screenshot(path=str(out / "failure.png"), full_page=True)
        except Exception:  # noqa: BLE001
            pass
        try:
            (out / "console.log").write_text(
                LINE_SEP.join(state.get("console") or []), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass
    if not keep:
        shutil.rmtree(out, ignore_errors=True)
    elif not failed:
        # A kept-but-passing test is release evidence, and the evidence is
        # the recording. The trace is the failure diagnostic — 14 MB of it
        # per green run, for a file nobody opens when nothing broke.
        # Tracing still ran, because it cannot be started retroactively.
        try:
            (out / "trace.zip").unlink()
        except OSError:
            pass


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Tell the fixture whether the test body failed.

    The fixture's own teardown cannot see the outcome, and "did this
    test pass" is the only input the keep-or-delete decision has.
    """
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        item._utb_failed = True


@pytest.fixture
def page(harness: Harness, evidence: dict):
    from playwright.sync_api import sync_playwright

    out = evidence["dir"]
    out.mkdir(parents=True, exist_ok=True)
    console: list[str] = []
    evidence["console"] = console

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            record_video_dir=str(out / "video"),
            record_video_size={"width": 1280, "height": 900},
        )
        # A trace is only worth its size when something went wrong, but it
        # has to be started before the thing goes wrong.
        context.tracing.start(screenshots=True, snapshots=True, sources=False)
        try:
            pg = context.new_page()
            evidence["page"] = pg
            pg.on("console", lambda m: console.append(m.type + ": " + m.text))
            pg.on("pageerror", lambda e: console.append("pageerror: " + str(e)))
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded",
                    timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
            yield pg
        finally:
            try:
                context.tracing.stop(path=str(out / "trace.zip"))
            except Exception:  # noqa: BLE001 - never mask the real failure
                pass
            context.close()  # flushes the video
            browser.close()


#: A step drawn as running (a spinner).
_SPINNING = ".agent-progress-step.state-running, .agent-progress-step .step-icon.is-animated"
_SPINNING_JS = """(sel) => Array.from(document.querySelectorAll(sel)).map(
  (e) => ((e.closest('.agent-progress-step') || e).textContent || '').trim().slice(0, 60))"""


def _wait_until_nothing_spins(pg, why: str, timeout: int = 20000) -> None:
    """A completed header and no running step, polled to a deadline."""
    try:
        pg.wait_for_function(
            "(sel) => { const h = document.querySelector('.turn-header');"
            " return !!h && h.className.indexOf('is-completed') >= 0"
            " && document.querySelectorAll(sel).length === 0; }",
            arg=_SPINNING,
            timeout=timeout,
        )
    except Exception:
        raise AssertionError(f"{why}: {pg.evaluate(_SPINNING_JS, _SPINNING)}") from None


#: What a finished turn's activity panel must not say about itself.
_STILL_WORKING = ("Working…", "Working...", "Kazma is thinking…")


def _assert_finished_panels_say_so(pg, why: str) -> None:
    """A finished turn's panel is not titled as work in progress. It kept
    "Working..." under its own "Completed" header when the turn had no
    thoughts (live, 2026-09-26)."""
    titles = pg.evaluate(
        "() => Array.from(document.querySelectorAll("
        "'.agent-progress.is-done .agent-progress-title, .kazma-cot-restored .agent-progress-title'"
        ")).map((e) => (e.textContent || '').trim())"
    )
    stale = [t for t in titles if t in _STILL_WORKING]
    assert not stale, f"{why}: {stale} (all titles: {titles})"


def _shape(pg) -> dict:
    return pg.evaluate(_SHAPE_JS) or {}


def _send(pg, text: str) -> None:
    pg.fill("#chat-input", text)
    pg.evaluate("() => window.KazmaChat.sendMessage()")


def _wait_for_pending_row(pg, timeout: int = 120000) -> None:
    """Wait until the approval region shows a row with a live control.

    Generous: the first turn of a session pays for the embedder warm-up
    and the graph's first compile. A tight timeout here fails as "no
    approval row" when the real answer is "not yet", which is the least
    useful way for a browser test to be red.
    """
    try:
        pg.wait_for_function(
            "() => !!document.querySelector("
            "'.turn-approvals-rows .hitl-approval-card button:not([disabled])')",
            timeout=timeout,
        )
    except Exception:
        raise AssertionError(
            "no actionable approval row appeared. Page state: "
            + repr(_shape(pg))
        ) from None


def test_stream_stays_visible_during_action_updates(page, evidence: dict) -> None:
    """Replay interleaved delivery through the real projector and DOM painter.

    This is a renderer race test, not a substitute for the real graph/Approve
    lifecycle below. Observe every animation frame and every DOM removal:
    checking only the final answer missed the disappearing interval.
    """
    page.wait_for_function("() => !!window.KazmaChat")
    result = page.evaluate("""async () => {
      const chat = window.KazmaChat;
      let seq = 1;
      const emit = ev => chat.applyTurnEvent(Object.assign({
        turn_id: 'stream-continuity-test', seq: seq++
      }, ev));
      const tick = () => new Promise(resolve => setTimeout(resolve, 180));
      emit({type: 'hitl', interrupt_id: 'g-continuity', tool: 'file_write',
            state: 'approved'});
      emit({type: 'tool_start', tool_call_id: 'c-continuity', tool_name: 'file_write'});
      emit({type: 'token', content: 'Visible response'});
      await tick();
      const answer = document.querySelector(
        '[data-turn-id="stream-continuity-test"] .message-text');
      if (!answer) return {error: 'initial answer missing'};
      let running = true, frames = 0, blankFrames = 0, removals = 0;
      const observer = new MutationObserver(records => {
        for (const record of records) for (const node of record.removedNodes) {
          if (node === answer || (node.contains && node.contains(answer))) removals++;
        }
      });
      observer.observe(answer.parentNode, {childList: true, subtree: true});
      const sample = () => {
        if (!running) return;
        frames++;
        if (!answer.isConnected || !answer.getClientRects().length ||
            !answer.innerText.trim() || getComputedStyle(answer).visibility === 'hidden') {
          blankFrames++;
        }
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
      for (const state of ['inflight', 'approved', 'settled', 'pending']) {
        emit({type: 'hitl', interrupt_id: 'g-continuity', tool: 'file_write', state});
        await tick();
        emit({type: 'token', content: ' more text'});
        await tick();
      }
      emit({type: 'tool_call', tool_call_id: 'c-continuity', tool_name: 'file_write'});
      await tick();
      emit({type: 'token', content: ' complete.'});
      await tick();
      const text = answer.innerText.trim();
      const sameNode = document.querySelector(
        '[data-turn-id="stream-continuity-test"] .message-text') === answer;
      running = false;
      observer.disconnect();
      return {frames, blankFrames, removals, sameNode, text};
    }""")
    (evidence["dir"] / "continuity.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    assert not result.get("error"), result
    assert result["frames"] > 5, result
    assert result["blankFrames"] == 0, result
    assert result["removals"] == 0, result
    assert result["sameNode"], result
    assert result["text"] == "Visible response" + " more text" * 4 + " complete.", result


def test_sequential_allow_tool_in_one_bubble(page, harness: Harness) -> None:
    """HITL_VIEW_MODEL.md Playwright **1**, claimed.

    Four gates, four real Approve clicks, one bubble. The original wording
    says "settled cards **above** the reply"; they are, and now they are
    rows in ONE region rather than four cards, which is the
    UNIFIED_TURN_BLOCK.md §3 change. The requirement it was protecting —
    a sequential approval must not scatter the turn or lose the reply —
    is stronger for it.
    """
    pg = page
    pg.evaluate("() => { try { sessionStorage.clear(); } catch (e) {} }")
    pg.evaluate("() => window.KazmaChat.newSession()")
    pg.wait_for_timeout(800)

    _send(pg, PROMPT)
    _wait_for_pending_row(pg)

    seen_gates: list[str] = []
    for leg in range(4):
        before = _shape(pg)
        assert before["bubbles"] == 1, (
            f"leg {leg}: {before['bubbles']} assistant bubbles — a "
            f"sequential approval split the turn"
        )
        assert before["groups"] == 1, f"leg {leg}: {before['groups']} groups"
        assert before["looseCards"] == 0, (
            f"leg {leg}: {before['looseCards']} cards outside the group"
        )
        pending = [r for r in before["rows"] if r["live"]]
        assert len(pending) == 1, (
            f"leg {leg}: expected one actionable row, got {len(pending)}"
        )
        seen_gates.append(pending[0]["gate"])

        pg.click(
            ".turn-approvals-rows .hitl-approval-card button:not([disabled])"
        )
        if leg < 3:
            pg.wait_for_function(
                "(prev) => {"
                " const rows = document.querySelectorAll("
                "  '.turn-approvals-rows .hitl-approval-card');"
                " if (rows.length <= prev) return false;"
                " return !!document.querySelector("
                "  '.turn-approvals-rows .hitl-approval-card button:not([disabled])');"
                "}",
                arg=len(before["rows"]),
                timeout=90000,
            )

    # The turn finishes on its own after the fourth approval.
    pg.wait_for_function(
        "() => {"
        " const h = document.querySelector('.turn-header');"
        " return !!h && h.className.indexOf('is-completed') >= 0;"
        "}",
        timeout=90000,
    )
    end = _shape(pg)

    assert end["bubbles"] == 1, "the reply is not in the same bubble"
    assert end["groups"] == 1
    assert end["looseCards"] == 0
    assert len(end["rows"]) == 4, (
        f"four requests produced {len(end['rows'])} rows"
    )
    assert len({r["gate"] for r in end["rows"]}) == 4, (
        "two gates share a row identity"
    )
    assert [r["gate"] for r in end["rows"]] == seen_gates, (
        "rows are not in ask order after the sequence"
    )
    assert all(r["live"] == 0 for r in end["rows"]), (
        "a finished turn still offers a decision"
    )
    assert "Scaffold ready" in end["answer"], end["answer"][:200]

    # The plan's layout sketch, in order, with the answer a SIBLING of the
    # group rather than inside it.
    assert end["shape"][: len(EXPECTED_SHAPE)] == EXPECTED_SHAPE, end["shape"]
    assert not end["bottomBar"], "the separate status bar is back"

    # Nothing spins on a finished turn. The gate rows ("Approved", "Approval
    # resolved") carry state `info`, which the step renderer drew as running:
    # four spinners on a finished turn, live and after every reload
    # (2026-09-26).
    _wait_until_nothing_spins(pg, "a finished turn shows a running step")
    _assert_finished_panels_say_so(pg, "a finished turn's panel says it is working")
    pg.reload(wait_until="domcontentloaded")
    _wait_until_nothing_spins(pg, "a reloaded finished turn shows a running step", 60000)
    _assert_finished_panels_say_so(pg, "a reloaded finished turn's panel says it is working")


def test_a_finished_plain_turn_does_not_say_it_is_working(page, harness: Harness) -> None:
    """A turn with no tools and no thoughts -- a plain answer -- kept
    "Working..." as its activity title under its own "Completed" header,
    until a reload (live, 2026-09-26). Its only rows are the page's own
    phase notes, which is the case the title had a branch for."""
    pg = page
    harness.script.steps, harness.script.final = [], "Ready."
    pg.evaluate("() => { try { sessionStorage.clear(); } catch (e) {} }")
    pg.evaluate("() => window.KazmaChat.newSession()")
    pg.wait_for_timeout(800)
    _send(pg, "Reply with the word ready.")
    pg.wait_for_function(
        "() => { const h = document.querySelector('.turn-header');"
        " return !!h && h.className.indexOf('is-completed') >= 0; }",
        timeout=90000,
    )
    assert "Ready." in (_shape(pg).get("answer") or "")
    _assert_finished_panels_say_so(pg, "a finished plain turn's panel says it is working")


def test_the_row_settles_before_any_poll(page) -> None:
    """HITL_VIEW_MODEL.md Playwright **4**, claimed.

    "Approve then look BEFORE the next /status poll: the card stays above
    the answer because the 200 body carries the view, not because a
    client bit outranks the registry."

    Two things are checked in the window right after the click, with all
    polling suspended:

    * the row stops offering a decision — from the response view, since
      nothing else has spoken yet;
    * the ANSWER has not moved. Under the old layout a settling gate
      crossed the answer, so "the card stays above the reply" was a claim
      about where the reply ended up. It is now structural: the region is
      above the answer for the whole turn.
    """
    pg = page
    pg.evaluate("() => { try { sessionStorage.clear(); } catch (e) {} }")
    pg.evaluate("() => window.KazmaChat.newSession()")
    pg.wait_for_timeout(800)

    _send(pg, PROMPT)
    _wait_for_pending_row(pg)

    before = _shape(pg)
    # A PAUSED turn has no answer region: narration folds into the
    # thoughts region when the model asks to act, so there is nothing to
    # index yet. What must hold is that settling a gate does not move
    # the reply RELATIVE to the group — captured as the shape before and
    # compared after, rather than as an absolute index into a list one
    # of whose entries may not exist.
    shape_before = [k for k in before["shape"] if k != "message-text"]
    gate = [r for r in before["rows"] if r["live"]][0]["gate"]

    # Silence every poller: whatever the row says next came from the
    # approval response, not from a resync that happened to land.
    pg.evaluate(
        "() => { window.fetch = new Proxy(window.fetch, { apply(t, s, a) {"
        " const u = String(a[0] || '');"
        " if (u.indexOf('/status') >= 0 || u.indexOf('/messages') >= 0) {"
        "   return Promise.reject(new Error('polling suspended by test'));"
        " }"
        " return Reflect.apply(t, s, a);"
        "} }); }"
    )

    pg.click(".turn-approvals-rows .hitl-approval-card button:not([disabled])")
    pg.wait_for_function(
        "(gate) => {"
        " const row = document.querySelector("
        "  '.turn-approvals-rows [data-interrupt-id=\"' + gate + '\"]');"
        " return !!row && row.querySelectorAll('button:not([disabled])').length === 0;"
        "}",
        arg=gate,
        timeout=30000,
    )

    after = _shape(pg)
    assert [k for k in after["shape"] if k != "message-text"] == shape_before, (
        "the regions were reordered when the gate settled — deciding a "
        f"gate must not relocate anything: {shape_before} -> "
        f"{[k for k in after['shape'] if k != 'message-text']}"
    )
    if "message-text" in after["shape"]:
        assert (after["shape"].index("turn-approvals")
                < after["shape"].index("message-text")), (
            "the approval group fell below the reply when the gate settled"
        )
    assert after["groups"] == 1
    assert after["looseCards"] == 0


def test_refresh_mid_pause_rebuilds_the_group(harness: Harness, page) -> None:
    """Acceptance matrix: "Refresh mid-pause — same group and valid
    controls after authoritative hydration."

    HITL incident 2 already covered this for the flat layout
    (``test_hitl_view_model.py``). What is new is that hydration has to
    rebuild the REGION, not a loose card: a reload that painted the cards
    as siblings of the answer would pass the old assertion and still be
    the layout this plan removes.
    """
    pg = page
    pg.evaluate("() => { try { sessionStorage.clear(); } catch (e) {} }")
    pg.evaluate("() => window.KazmaChat.newSession()")
    pg.wait_for_timeout(800)

    _send(pg, PROMPT)
    _wait_for_pending_row(pg)

    pg.reload(wait_until="domcontentloaded")
    pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
    _wait_for_pending_row(pg)

    after = _shape(pg)
    assert after["groups"] == 1, "refresh did not rebuild one approval group"
    assert after["looseCards"] == 0, (
        "refresh painted approval cards beside the answer instead of in "
        "the group"
    )
    assert any(r["live"] for r in after["rows"]), (
        "refresh mid-pause left the row with no live control"
    )
    assert not after["bottomBar"]
    # Only once there IS a reply: a turn still paused for approval has no
    # answer region, which is the point of the fold.
    if "message-text" in after["shape"]:
        assert (after["shape"].index("turn-approvals")
                < after["shape"].index("message-text")), (
            "the approval group is below the reply after a refresh"
        )


def test_the_fold_starts_collapsed_and_stays_where_the_reader_puts_it(
    page,
) -> None:
    """Invariant U08, in the browser.

    The node suite proves the rule against a fake DOM and missed the real
    defect: a turn opens under the ``'live'`` placeholder and is renamed,
    and the preference was written under one key and read under the other,
    so the fold shut again about a second into every turn. A browser test
    is the only place that rename happens.
    """
    pg = page
    pg.evaluate("() => { try { sessionStorage.clear(); } catch (e) {} }")
    pg.evaluate("() => window.KazmaChat.newSession()")
    pg.wait_for_timeout(800)

    _send(pg, PROMPT)
    pg.wait_for_selector(".message-assistant .agent-progress", timeout=60000)

    assert _shape(pg)["foldCollapsed"] is True, (
        "a live turn opened with its thoughts already unfolded"
    )

    pg.click(".message-assistant .agent-progress .agent-progress-header")
    assert _shape(pg)["foldCollapsed"] is False

    # Repaint the way a token stream does, across the turn-id promotion.
    pg.evaluate(
        "() => { for (let i = 0; i < 25; i++) {"
        " window.KazmaChat.applyTurnEvent("
        "  { type: 'token', content: 'x', seq: 90000 + i, source: 'test' });"
        "} }"
    )
    pg.wait_for_timeout(600)
    assert _shape(pg)["foldCollapsed"] is False, (
        "repaints closed a fold the reader opened (U08)"
    )

    pg.click(".message-assistant .agent-progress .agent-progress-header")
    pg.evaluate(
        "() => { for (let i = 0; i < 25; i++) {"
        " window.KazmaChat.applyTurnEvent("
        "  { type: 'token', content: 'y', seq: 91000 + i, source: 'test' });"
        "} }"
    )
    pg.wait_for_timeout(600)
    assert _shape(pg)["foldCollapsed"] is True, (
        "repaints reopened a fold the reader closed (U08)"
    )

# ══════════════════════════════════════════════════════════════════════
# Session switch during updates
# ══════════════════════════════════════════════════════════════════════


def test_a_new_session_mid_pause_does_not_inherit_the_open_question(
    harness: Harness, page
) -> None:
    """Acceptance matrix: "No content appears in the wrong session;
    return restores state."

    The dangerous direction is the approval group: an Approve button left
    on screen after the switch is a control that answers a question the
    reader is no longer looking at. The answer text leaking is bad; a
    live gate leaking is a decision made by accident.
    """
    _send(page, PROMPT)
    _wait_for_pending_row(page)
    before = _shape(page)
    assert before["rows"], before
    first_gate = before["rows"][0]["gate"]
    assert first_gate
    # The id to come BACK to. The sidebar's first row is the NEWEST
    # session, which after the switch is the empty one we left for.
    origin = page.evaluate(
        "() => { try { return localStorage.getItem('kazma.chatSessionId'); }"
        " catch (e) { return null; } }"
    )

    # Switch away. newSession() is the product's own path, not a reload.
    page.evaluate("() => window.KazmaChat.newSession()")
    page.wait_for_function(
        "() => !document.querySelector('.turn-approvals .hitl-approval-card')",
        timeout=20000,
    )
    # _shape() is {} when there is no assistant bubble at all, which is
    # the desired state here — so every read is a .get().
    after = _shape(page)
    assert not after.get("rows"), (
        f"the new session inherited the old one's approval rows: {after}"
    )
    assert not after.get("looseCards"), (
        f"an approval card survived the session switch: {after}"
    )
    assert first_gate not in page.content(), (
        "the previous session's gate id is still in the document; a stale "
        "control can still be clicked"
    )

    # ...and coming back restores it, from the server rather than memory.
    if not origin:
        pytest.skip("the page exposes no session id to return to")
    item = page.locator('.session-item[data-session-id="' + origin + '"]')
    if item.count() == 0:
        pytest.skip("the paused session has no sidebar entry to return through")
    item.first.click()
    page.wait_for_function(
        "() => !!document.querySelector('.turn-approvals .hitl-approval-card')",
        timeout=60000,
    )
    back = _shape(page)
    assert back["groups"] <= 1, f"returning built a second group: {back}"
    assert back["rows"], f"returning restored no approval rows: {back}"


# ══════════════════════════════════════════════════════════════════════
# Long activity / RTL / mobile
# ══════════════════════════════════════════════════════════════════════


def test_the_answer_survives_a_phone_in_rtl(harness: Harness, page) -> None:
    """Acceptance matrix: "Readable, accessible, responsive; no answer
    trapped in folds."

    375x812 with ``dir="rtl"`` is the combination that breaks layouts
    built with left-anchored padding, and RTL is the case a source-level
    CSS check cannot evaluate at all. Three things must hold: the answer
    is on screen, the page does not scroll sideways, and the answer is
    not inside the collapsible region.
    """
    page.set_viewport_size({"width": 375, "height": 812})
    page.evaluate("() => { document.documentElement.setAttribute('dir', 'rtl'); }")
    _send(page, PROMPT)
    _wait_for_pending_row(page)

    facts = page.evaluate("""() => {
  var bs = document.querySelectorAll('.message-assistant');
  var c = bs.length ? bs[bs.length - 1].querySelector('.message-content') : null;
  if (!c) return { missing: true };
  var answer = c.querySelector('.message-text');
  var fold = c.querySelector('.agent-progress');
  var r = answer ? answer.getBoundingClientRect() : null;
  return {
    missing: false,
    hasAnswer: !!answer,
    answerInFold: !!(answer && fold && fold.contains(answer)),
    answerWidth: r ? r.width : 0,
    answerRight: r ? r.right : 0,
    overflow: document.documentElement.scrollWidth
            - document.documentElement.clientWidth,
    dir: getComputedStyle(document.documentElement).direction,
    headerVisible: !!c.querySelector('.turn-header'),
  };
}""")
    assert not facts.get("missing"), "no assistant block rendered at all"
    assert facts["dir"] == "rtl", f"the RTL switch did not take: {facts}"
    assert facts["headerVisible"], f"the turn header vanished on mobile: {facts}"
    assert not facts["answerInFold"], (
        "the answer is inside the collapsible activity region, so "
        f"collapsing thoughts hides it: {facts}"
    )
    # 2px of tolerance for subpixel rounding; a real overflow is tens of px.
    assert facts["overflow"] <= 2, (
        f"the turn block scrolls the page sideways on a phone: {facts}"
    )
    if facts["hasAnswer"]:
        assert facts["answerWidth"] <= 375, (
            f"the answer is wider than the viewport: {facts}"
        )


def test_a_completed_four_gate_turn_keeps_its_answer_out_of_the_fold(
    harness: Harness, page
) -> None:
    """The long-activity half of the same row.

    Four gates means four tool rows plus their results — the longest
    activity list this plan produces. The fold must still default closed
    and the answer must still be outside it, because "readable" for a
    long turn means the answer is not below a wall of tool output.
    """
    _send(page, PROMPT)
    for _ in range(4):
        try:
            _wait_for_pending_row(page, timeout=90000)
        except AssertionError:
            break
        page.evaluate(
            "() => { var b = document.querySelector("
            "'.turn-approvals-rows .hitl-approval-card "
            "button:not([disabled])'); if (b) b.click(); }"
        )
        page.wait_for_timeout(1500)

    facts = page.evaluate("""() => {
  var bs = document.querySelectorAll('.message-assistant');
  var c = bs.length ? bs[bs.length - 1].querySelector('.message-content') : null;
  if (!c) return { missing: true };
  var fold = c.querySelector('.agent-progress');
  var answer = c.querySelector('.message-text');
  var toggle = c.querySelector('[aria-expanded]');
  return {
    missing: false,
    rows: fold ? fold.querySelectorAll('.agent-progress-row, .tool-row, li').length : 0,
    collapsed: !!(fold && fold.classList.contains('is-collapsed')),
    expanded: toggle ? toggle.getAttribute('aria-expanded') : null,
    answerInFold: !!(answer && fold && fold.contains(answer)),
    answerVisible: !!(answer && answer.getBoundingClientRect().height > 0),
    bubbles: bs.length,
  };
}""")
    assert not facts.get("missing"), "no assistant block rendered at all"
    assert not facts["answerInFold"], (
        f"a long turn put its answer inside the fold: {facts}"
    )
    if facts["expanded"] is not None:
        assert facts["expanded"] == "false", (
            f"the activity fold defaulted open after four gates: {facts}"
        )

# ══════════════════════════════════════════════════════════════════════
# Narration belongs in the fold
# ══════════════════════════════════════════════════════════════════════


def test_the_answer_region_is_empty_while_the_turn_is_asking(
    harness: Harness, page
) -> None:
    """Reported from the installed build, 2026-09-20.

    Text appeared under the CoT block while the model worked, and was
    replaced the moment Approve was clicked. It was the model's
    narration, classified as the ANSWER because mid-turn there is
    nothing to compare the stream against — the separation only happened
    retroactively, when the resume leg's backfill arrived.

    At a pause the turn has not answered. The answer region must be
    empty, and what the model said must be in the thoughts fold —
    which is collapsed, which is where it was wanted.
    """
    _send(page, PROMPT)
    _wait_for_pending_row(page)

    facts = page.evaluate("""() => {
  var bs = document.querySelectorAll('.message-assistant');
  var c = bs.length ? bs[bs.length - 1].querySelector('.message-content') : null;
  if (!c) return { missing: true };
  var answer = c.querySelector('.message-text');
  var fold = c.querySelector('.agent-progress');
  var thoughts = fold
    ? Array.from(fold.querySelectorAll('.step-thought')).map(
        (r) => (r.textContent || '').trim())
    : [];
  return {
    missing: false,
    answer: ((answer && answer.textContent) || '').trim(),
    thoughts: thoughts,
    foldCollapsed: !!(fold && fold.classList.contains('is-collapsed')),
  };
}""")
    assert not facts.get("missing"), "no assistant block rendered"
    assert facts["answer"] == "", (
        "the turn is paused for approval and the answer region already "
        f"has text in it: {facts['answer'][:120]!r}"
    )
    assert facts["foldCollapsed"], (
        "the thoughts fold is open at a pause; it is collapsed by default"
    )


def test_approving_does_not_swap_the_text_under_the_reader(
    harness: Harness, page
) -> None:
    """The symptom itself: "the text replaced after I click approve".

    Whatever the answer region holds before Approve must not be
    *replaced* by different text after it. Empty→answer is growth;
    narration→other-narration is the swap that was reported.
    """
    _send(page, PROMPT)
    _wait_for_pending_row(page)

    def answer_text() -> str:
        return page.evaluate("""() => {
  var bs = document.querySelectorAll('.message-assistant');
  var c = bs.length ? bs[bs.length - 1].querySelector('.message-content') : null;
  var a = c && c.querySelector('.message-text');
  return ((a && a.textContent) || '').trim();
}""")

    before = answer_text()
    page.evaluate(
        "() => { var b = document.querySelector("
        "'.turn-approvals-rows .hitl-approval-card "
        "button:not([disabled])'); if (b) b.click(); }"
    )
    page.wait_for_timeout(4000)
    after = answer_text()

    if before:
        assert after.startswith(before), (
            "the answer region was REPLACED across an approval rather than "
            f"grown: {before[:60]!r} -> {after[:60]!r}"
        )


_CARD_STATE_JS = """() => {
  var c = document.querySelector('.turn-approvals-rows .hitl-approval-card');
  if (!c) return null;
  var h = c.querySelector('.hitl-approval-header');
  return {
    collapsed: c.classList.contains('hitl-collapsed'),
    live: c.querySelectorAll('button:not([disabled])').length,
    bodyShown: !!(c.querySelector('.hitl-approval-body')
      && c.querySelector('.hitl-approval-body').offsetParent),
    headerCursor: h ? getComputedStyle(h).cursor : '',
  };
}"""


def test_a_settled_card_opens_and_closes_from_its_header(page) -> None:
    """Reported 2026-09-24: a settled approval card could be expanded from
    its one-line bar, and then never collapsed again -- clicking the
    header did nothing. The header is the toggle in BOTH directions."""
    pg = page
    pg.evaluate("() => { try { sessionStorage.clear(); } catch (e) {} }")
    pg.evaluate("() => window.KazmaChat.newSession()")
    pg.wait_for_timeout(800)
    _send(pg, PROMPT)
    _wait_for_pending_row(pg)
    pg.click(".turn-approvals-rows .hitl-approval-card button:not([disabled])")
    pg.wait_for_function(
        "() => { const c = document.querySelector("
        "'.turn-approvals-rows .hitl-approval-card');"
        " return !!c && c.classList.contains('hitl-collapsed'); }",
        timeout=60000,
    )
    header = ".turn-approvals-rows .hitl-approval-card .hitl-approval-header"
    pg.click(header)
    pg.wait_for_timeout(300)
    opened = pg.evaluate(_CARD_STATE_JS)
    assert opened and not opened["collapsed"] and opened["bodyShown"], opened
    assert opened["headerCursor"] == "pointer", (
        f"an expanded card's header must still read as clickable: {opened}"
    )
    pg.click(header)
    pg.wait_for_timeout(300)
    closed = pg.evaluate(_CARD_STATE_JS)
    assert closed and closed["collapsed"] and not closed["bodyShown"], (
        f"the expanded card did not collapse from its header: {closed}"
    )
    # And it stays where the reader put it across the renders that follow.
    pg.wait_for_timeout(3000)
    assert pg.evaluate(_CARD_STATE_JS)["collapsed"] is True
    pg.click(header)
    pg.wait_for_timeout(3000)
    assert pg.evaluate(_CARD_STATE_JS)["collapsed"] is False, (
        "a re-render snapped the card shut under the reader"
    )
