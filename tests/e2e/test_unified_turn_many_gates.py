"""Nine gates in one turn — reported ordering and thoughts defects.

Reported from the installed build, 2026-09-20, on a turn that fired nine
approval cards:

  * "after the 5th card the ordering become not in the right orders, the
    5th or the 6th shown below the 1st approved card"
  * the thoughts fold held only ONE fragment of the model's narration,
    although it narrated before most of its tool calls

Neither reproduces in the projector, and nine gates alone did not
reproduce them either. The ingredient is a leg that finishes WITHOUT
pausing — a read-tier tool call — because that closes the reply turn and
the next tool call opens a new one. The client then mints a new document
and orphans everything decided under the old turn id.

That one cause produces both reported symptoms:

  * the orphaned cards stay in the DOM and the painter appends them
    after the planned rows, so an early card appears below later ones;
  * the thoughts fold belongs to the current document, so narration from
    before the identity change is no longer shown — leaving a single
    fragment.

The four-gate suite next door never reached this: every one of its legs
pauses, so its turn never closes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytest.importorskip("playwright")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    Script,
    Step,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

PROMPT = "Fire nine approval cards so I can check the gate."

#: Narration before every step, so the thoughts question is exercised as
#: well as the ordering one. Distinct per step: if they merge wrongly the
#: text says which ones survived.
_NARRATION = [
    "Card {n} of nine — preparing the call and explaining myself first, "
    "because a gate with no reason attached is a gate the reader has to "
    "guess at.".format(n=i + 1)
    for i in range(12)
]


def _nine_gate_script(tmp_dir: str) -> Script:
    """The reported turn's shape, not just its gate count.

    Nine gates alone did not reproduce anything. Two ingredients of the
    real turn are reproduced here as well:

    * READ-TIER calls between the gates (``file_read``, ``file_list``).
      They emit activity but no gate, so nothing folds the narration in
      front of them — the shape that could leave a single fragment in
      the thoughts.
    * calls that FAIL AFTER APPROVAL. The report has three shell_exec
      calls approved and then rejected by the allowlist ("could not
      resolve 'ls' under restricted PATH"), all before the fifth card.
      An approved gate whose execution fails is a different sequence
      from one that succeeds.
    """
    plan = [
        ("file_write", {"path": "probe_1.txt", "content": "one"}),
        ("file_read", {"path": "probe_1.txt"}),                  # read tier
        ("shell_exec", {"command": "definitely-not-on-the-allowlist"}),
        ("file_list", {"path": "."}),                            # read tier
        ("shell_exec", {"command": "also-not-allowlisted"}),
        ("file_write", {"path": "probe_2.txt", "content": "two"}),
        ("file_read", {"path": "probe_2.txt"}),                  # read tier
        ("shell_exec", {"command": "still-not-allowlisted"}),
        ("file_write", {"path": "probe_3.txt", "content": "three"}),
        ("file_list", {"path": "."}),                            # read tier
        ("file_write", {"path": "probe_4.txt", "content": "four"}),
        ("file_delete", {"path": "probe_1.txt"}),
    ]
    steps = []
    for i, (tool, args) in enumerate(plan):
        a = dict(args)
        if "path" in a:
            a["path"] = os.path.join(tmp_dir, str(a["path"]))
        steps.append(Step(tool=tool, args=a, narration=_NARRATION[i % len(_NARRATION)]))
    return Script(steps=steps, final="All cards fired.")


@pytest.fixture
def harness() -> Iterator[Harness]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="utb-nine-") as tmp:
        with unified_turn_server(_nine_gate_script(tmp)) as h:
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


_ROWS_JS = """() => {
  var bs = document.querySelectorAll('.message-assistant');
  var c = bs.length ? bs[bs.length - 1].querySelector('.message-content') : null;
  if (!c) return null;
  var host = c.querySelector('.turn-approvals-rows');
  var fold = c.querySelector('.agent-progress');
  var thought = fold && fold.querySelector('.step-thought .step-detail');
  return {
    rows: host ? Array.from(host.children).map((x) => ({
      gate: x.getAttribute('data-interrupt-id') || '',
      tool: ((x.querySelector('.hitl-tool') || {}).textContent || '').trim(),
      live: x.querySelectorAll('button:not([disabled])').length,
    })) : [],
    groups: c.querySelectorAll('.turn-approvals').length,
    thought: ((thought && thought.textContent) || '').trim(),
  };
}"""


def _approve_first_live(pg) -> bool:
    """Click the first actionable Approve. False when none is left."""
    return pg.evaluate("""() => {
  var b = document.querySelector(
    '.turn-approvals-rows .hitl-approval-card button:not([disabled])');
  if (!b) return false;
  b.click();
  return true;
}""")


def _wait_for_next_card(pg, timeout: int = 90000) -> bool:
    """Wait for the next actionable row, or for the turn to finish.

    A fixed sleep is not a wait: the first run of this test slept 2500ms
    after each approval and gave up at the second card, while the graph
    log showed it had already reached the third. The resume leg has to
    run a tool and go back to the model before the next gate exists.
    """
    try:
        pg.wait_for_function(
            "() => !!document.querySelector("
            "'.turn-approvals-rows .hitl-approval-card "
            "button:not([disabled])')",
            timeout=timeout,
        )
        return True
    except Exception:
        return False


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The turn ID changes mid-turn, so the client mints a NEW document "
        "and every gate decided before that point is orphaned. Measured "
        "2026-09-20; see the module docstring. Fixing it means changing "
        "when close_turn fires, which plan §15 preserves rather than "
        "redesigns — remove this marker with the fix."
    ),
)
def test_nine_gates_keep_ask_order_as_they_settle(harness: Harness, page) -> None:
    """The reported symptom: a later card jumps up beside the first.

    Rows are ordered by the position of their ``hitl`` part, and settling
    a gate must change its label, not its place — sorting by state is
    what used to reshuffle the transcript on every decision.

    **Measured cause (2026-09-20).** Instrumenting ``applyEvent`` in the
    live page showed the document identity changing part-way through one
    turn::

        doc=9d8b8d08b7 … hitl:approved -> a757a62a,d4532c99
        doc=6e42866f12 … hitl:approved -> d4532c99

    and the server log showed why::

        turn=a68388eeaef3   file_write (gated), file_read (no gate)
        turn=ca4d10390acf   <- NEW TURN
        turn=3aa26d094e79   <- NEW TURN

    A leg that finishes WITHOUT pausing closes the reply turn; the next
    tool call opens a new one. The client treats that as a new turn
    block, so gates decided under the old id are orphaned: their cards
    stay in the DOM (the renderer cannot remove what the plan no longer
    mentions) and the painter appends them after the planned rows —
    which is the "5th card below the 1st approved card" that was
    reported.

    This is why nine gates alone reproduced nothing: with every leg
    gated the turn never closes. The read-tier calls interleaved in the
    script above are the ingredient.
    """
    page.fill("#chat-input", PROMPT)
    page.evaluate("() => window.KazmaChat.sendMessage()")
    page.wait_for_function(
        "() => !!document.querySelector("
        "'.turn-approvals-rows .hitl-approval-card button:not([disabled])')",
        timeout=120000,
    )

    seen_order: list[str] = []
    snapshots: list[list[str]] = []

    for _leg in range(12):
        state = page.evaluate(_ROWS_JS)
        if not state or not state["rows"]:
            break
        order = [r["gate"] for r in state["rows"] if r["gate"]]
        snapshots.append(order)
        for gate in order:
            if gate not in seen_order:
                seen_order.append(gate)

        # Every snapshot must be a PREFIX-CONSISTENT view of ask order:
        # the gates it shows, in the order it shows them, must match the
        # order in which they were first seen.
        expected = [g for g in seen_order if g in order]
        assert order == expected, (
            f"leg {_leg + 1}: the approval rows are out of ask order.\n"
            f"  shown        : {order}\n"
            f"  first seen as: {expected}\n"
            f"  rows         : {[(r['tool'], r['live']) for r in state['rows']]}\n"
            f"  history      : {snapshots}"
        )
        assert state["groups"] == 1, f"{state['groups']} approval groups"

        if not _approve_first_live(page):
            break
        if not _wait_for_next_card(page):
            # The turn finished, or no further card is coming. Take one
            # last look: the settled rows must still be in ask order.
            page.wait_for_timeout(1500)
            final = page.evaluate(_ROWS_JS)
            if final and final["rows"]:
                order = [r["gate"] for r in final["rows"] if r["gate"]]
                snapshots.append(order)
                expected = [g for g in seen_order if g in order]
                assert order == expected, (
                    "after the last decision the rows are out of ask "
                    f"order.\n  shown        : {order}\n"
                    f"  first seen as: {expected}\n"
                    f"  history      : {snapshots}"
                )
            break

    assert len(seen_order) >= 5, (
        f"only {len(seen_order)} gates ever appeared; this test needs to "
        f"get past the fifth to check what was reported: {snapshots}"
    )


def test_narration_from_every_leg_reaches_the_fold(
    harness: Harness, page
) -> None:
    """The other half of the report: only one fragment in the thoughts.

    The script narrates a distinct sentence before each of nine calls, so
    a fold holding one of them is visibly wrong and the text says which
    survived.
    """
    page.fill("#chat-input", PROMPT)
    page.evaluate("() => window.KazmaChat.sendMessage()")
    page.wait_for_function(
        "() => !!document.querySelector("
        "'.turn-approvals-rows .hitl-approval-card button:not([disabled])')",
        timeout=120000,
    )

    for _leg in range(4):
        if not _approve_first_live(page):
            break
        if not _wait_for_next_card(page):
            break

    state = page.evaluate(_ROWS_JS)
    assert state is not None
    thought = state["thought"]
    present = [i + 1 for i in range(9) if f"Card {i + 1} of nine" in thought]
    assert len(present) >= 3, (
        "the thoughts fold kept only "
        f"{len(present)} of the narration fragments ({present}). Every leg "
        f"narrates, and the fold is where narration goes.\n"
        f"  fold text: {thought[:400]!r}"
    )
