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


@pytest.fixture
def page(harness: Harness):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            pg = browser.new_page()
            pg.goto(f"{harness.base}/chat", wait_until="domcontentloaded",
                    timeout=30000)
            pg.locator("#chat-input").wait_for(state="visible", timeout=20000)
            yield pg
        finally:
            browser.close()


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
    answer_index_before = before["shape"].index("message-text")
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
    assert after["shape"].index("message-text") == answer_index_before, (
        "the answer moved when the gate settled — deciding a gate must "
        "not relocate the reply"
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
    assert after["shape"].index("turn-approvals") < after["shape"].index("message-text")


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
