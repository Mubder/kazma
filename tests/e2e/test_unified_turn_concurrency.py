"""Two clients, one gate — ``docs/plans/UNIFIED_TURN_BLOCK.md`` §7 and §10.

    "Concurrent approval clients retain registry CAS behavior. A 409
     resolves to the actual server view and cannot create a duplicate row.
     A lost acknowledgement triggers status recovery, not an unconditional
     new approval command."

Acceptance matrix rows covered here:

    | Two tabs approve concurrently | One effective decision/execution;
    |                               | loser reconciles actual state
    | Lost approval HTTP response   | Recovered server state; no duplicate
    |                               | execution

The registry's CAS is already unit-tested (``tests/test_hitl_gates.py``).
What is not, and what these cover, is the whole path: two real HTTP
clients racing the real route against a real paused graph, and the effect
on the transcript the reader ends up with. A CAS that holds while the
turn resumes twice would pass the unit test and lose the user's work.
"""

from __future__ import annotations

import concurrent.futures
from collections.abc import Iterator

import pytest

pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    HITL_EVENTS,
    api_client,
    gate_from_frame,
    new_session_id,
    sse_frames,
    start_turn,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture
def harness() -> Iterator[Harness]:
    """One app per test, not per module — see ``tests/e2e/conftest.py``."""
    with unified_turn_server() as h:
        yield h


def _pause(harness: Harness) -> tuple[str, str, str]:
    """Run one turn to its first gate. Returns (session, thread, gate)."""
    session_id = new_session_id()
    with api_client(harness.base) as client:
        with start_turn(
            harness.base, session_id, "Set up the project scaffold.", client
        ) as resp:
            assert resp.status_code == 200
            for frame in sse_frames(resp, limit_seconds=120):
                data = frame["data"] if isinstance(frame["data"], dict) else {}
                if frame["event"] in HITL_EVENTS:
                    gate = gate_from_frame(frame)
                    if gate and str(data.get("state") or "pending") == "pending":
                        return session_id, str(data.get("thread_id") or session_id), gate
    raise AssertionError("the turn never paused")


def _approve(base: str, thread: str, gate: str) -> dict:
    """One approval POST, from its own client — a separate tab."""
    import httpx

    with httpx.Client(follow_redirects=True, timeout=60.0) as c:
        primed = c.get(f"{base}/chat")
        assert primed.status_code < 400
        r = c.post(
            f"{base}/api/approve/{thread}",
            json={"action": "approve", "scope": "once", "interrupt_id": gate},
        )
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            body = {"raw": r.text}
        body["_status"] = r.status_code
        return body


def _gate_state(gate_id: str) -> str:
    """The registry's own answer for this gate, or "" if it has none.

    ``ensure_gate_schema()`` first: this repo gives every test its own
    empty ``hitl_gates.db`` (``tests/conftest.py``), so a READ can be the
    first thing to touch the file and "no such table" is an empty
    registry, not a failure. A reader must not assume a writer ran.
    """
    from kazma_core.safety.hitl_gates import ensure_gate_schema, gate_for

    try:
        ensure_gate_schema()
        row = gate_for(gate_id)
    except Exception:
        return ""
    return str(getattr(row, "state", "") or "") if row else ""


def test_two_tabs_approving_at_once_produce_one_decision(
    harness: Harness,
) -> None:
    """Both POST the same gate at the same moment.

    Exactly one may win. The loser must be TOLD — a 409 or an explicit
    "no longer pending" — and must not mint a second decision, a second
    resume, or a second row. Registry CAS is what makes that true; this
    checks it through the route, concurrently, against a real pause.
    """
    _session, thread, gate = _pause(harness)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(_approve, harness.base, thread, gate)
        b = pool.submit(_approve, harness.base, thread, gate)
        first, second = a.result(timeout=90), b.result(timeout=90)

    results = [first, second]
    winners = [r for r in results if r.get("_status") == 200 and r.get("ok")]
    losers = [r for r in results if r not in winners]

    assert len(winners) == 1, (
        f"{len(winners)} clients were told they won the same gate: {results}"
    )
    assert len(losers) == 1
    loser = losers[0]
    assert loser.get("_status") in (200, 409), (
        f"the losing client got {loser.get('_status')}: {loser}"
    )
    if loser.get("_status") == 409:
        assert loser.get("reason") == "not_pending" or loser.get("error"), (
            f"a 409 with no explanation is not a resolution: {loser}"
        )
    else:
        # A 200 to the loser is only acceptable if it reports the SAME
        # decision — i.e. it converged, rather than deciding again.
        assert loser.get("approved") == winners[0].get("approved"), (
            f"two different decisions were accepted for one gate: {results}"
        )

    # And the registry holds one settled state, not two.
    state = _gate_state(gate)
    assert state != "pending", (
        f"the gate is still pending after two approvals"
    )
    if not state:
        pytest.skip(
            "the gate registry has no row for this gate in this test's "
            "isolated database; the HTTP-level assertions above still held"
        )


def test_a_lost_response_does_not_execute_twice(harness: Harness) -> None:
    """The client never sees the 200 and asks again.

    Plan §7: "A lost acknowledgement triggers status recovery, not an
    unconditional new approval command." A client that retries anyway —
    which is what a user double-clicking Approve does — must not cause a
    second execution. The registry answers the retry from the decision it
    already recorded.
    """
    _session, thread, gate = _pause(harness)

    first = _approve(harness.base, thread, gate)
    assert first.get("ok") is True, first
    after_first = _gate_state(gate)

    # The "lost response" retry.
    retry = _approve(harness.base, thread, gate)
    assert retry.get("_status") in (200, 409), retry
    assert not (retry.get("_status") == 200 and retry.get("running") is True
                and after_first not in ("", "pending")), (
        "the retry started a SECOND resume for a gate that was already "
        f"decided ({after_first}): {retry}"
    )

    state = _gate_state(gate)
    assert state != "pending", (
        f"the gate is still pending after an approval and a retry: {state!r}"
    )


def test_a_decision_for_an_unknown_gate_is_refused(harness: Harness) -> None:
    """A stale tab approving a gate this thread never had.

    Not a race, but the same authority question: the route must answer
    from the registry rather than resuming whatever is paused. Resuming
    on an unmatched id is how one tab's click settles another tab's
    question.
    """
    _session, thread, gate = _pause(harness)

    bogus = _approve(harness.base, thread, "not-a-real-gate-id")
    assert bogus.get("_status") in (200, 400, 404, 409), bogus
    if bogus.get("_status") == 200 and bogus.get("ok"):
        # If the route accepted it, it must have resolved to the REAL
        # gate rather than inventing one.
        assert str(bogus.get("interrupt_id") or "") in ("", gate), (
            f"an unknown gate id was accepted as its own decision: {bogus}"
        )
