"""Four sequential approval/resume cycles through the real application graph.

``docs/plans/UNIFIED_TURN_BLOCK.md`` Phase 0 exit:

    Deterministic application graph harness supporting four approval/resume
    cycles. […] No claim that sequential approval works until the app-graph
    harness proves it.

``tests/e2e/test_hitl_view_model.py`` left incidents 1 and 4 unclaimed
because "``create_app()`` does not pause from preloaded
``tool_calls_pending``" — it seeds registry rows and proves rendering only.
This module makes the real graph pause, four times, and answers each pause
through ``POST /api/approve/{thread_id}``.

Nothing here is mocked except the model, and the model is mocked at the
provider boundary ``AGENTS.md`` §3 names as the single OpenAI-compatible
path every transport shares. The supervisor, the tool worker's
``interrupt()``, the checkpointer, the gate registry, ``close_turn``, the
journal and the SSE transport all run as they do in production. Plan §10
forbids the alternatives: "Do not mock the approval endpoint or manually
change the UI to make sequential approval tests pass."

Not a browser test. What the operator SEES is Phase 3/4 and needs
Playwright; this proves the lifecycle underneath it, which is what was
missing.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

pytest.importorskip("httpx")
pytest.importorskip("uvicorn")

from tests.e2e._unified_turn_harness import (  # noqa: E402
    Harness,
    drive_turn,
    four_gate_script,
    unified_turn_server,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture(scope="module")
def harness() -> Iterator[Harness]:
    with unified_turn_server() as h:
        yield h


def _tools(run: object) -> list[str]:
    return [c.tool for c in run.cycles]  # type: ignore[attr-defined]


def test_four_sequential_gates_complete_the_turn(harness: Harness) -> None:
    """The headline: four pauses, four real approvals, one finished turn."""
    run = drive_turn(
        harness,
        "Set up the project scaffold.",
        [True, True, True, True],
        leg_timeout=120.0,
    )

    assert len(run.cycles) == 4, (
        f"expected four pauses, got {len(run.cycles)}: {_tools(run)}"
    )
    assert _tools(run) == ["file_write", "shell_exec", "file_write", "file_delete"], (
        "the graph did not ask for the scripted tools in order"
    )
    assert len(set(run.gate_ids)) == 4, (
        "gate ids repeated — two pauses shared one identity, which is the "
        "defect invariant U09 exists to prevent: " + str(run.gate_ids)
    )
    for cycle in run.cycles:
        assert cycle.ack.get("ok") is True, cycle.ack
        assert cycle.ack.get("running") is True, (
            f"the approval route did not resume the graph: {cycle.ack}"
        )
    assert run.finished, "the turn never reached a terminal frame"
    assert "Scaffold ready" in run.final_answer(), run.final_answer()


def test_two_gates_can_share_a_tool_and_stay_distinct(harness: Harness) -> None:
    """Steps 1 and 3 are both ``file_write``.

    Acceptance matrix, "Repeated identical tool/arguments": fresh gate ids
    produce separate rows. If the server collapsed them the turn would ask
    three times, not four — which is exactly the collapse
    ``turn_document.py:_part_key`` was changed to prevent, now checked
    against the graph instead of against a fixture.
    """
    run = drive_turn(
        harness,
        "Set up the project scaffold.",
        [True, True, True, True],
        leg_timeout=120.0,
    )
    writes = [c for c in run.cycles if c.tool == "file_write"]
    assert len(writes) == 2, _tools(run)
    assert writes[0].gate_id != writes[1].gate_id, (
        "two file_write pauses shared one gate id"
    )


def test_denial_does_not_end_the_turn(harness: Harness) -> None:
    """Plan §7: "Denial does not automatically mean the whole turn failed."

    The layout fixture denies step 2 on purpose. The turn must still pause
    for steps 3 and 4 and still finish.
    """
    run = drive_turn(
        harness,
        "Set up the project scaffold.",
        [True, False, True, True],
        leg_timeout=120.0,
    )
    assert len(run.cycles) == 4, (
        f"a denial cut the turn short: {_tools(run)}"
    )
    assert run.cycles[1].decision == "deny"
    assert run.cycles[1].ack.get("approved") is False, run.cycles[1].ack
    assert run.finished, "the turn did not finish after a denial"


def test_approved_tools_actually_execute(harness: Harness) -> None:
    """Approval is not a UI state change.

    ``file_write`` on the approved path really writes, inside the isolated
    data directory. Without this the harness could pass while the resume
    stopped short of execution, and "Approved" would mean nothing — which
    is the decision/execution conflation plan §3 separates.
    """
    script = four_gate_script(harness.data_dir)
    target = script.steps[0].args["path"]
    run = drive_turn(
        harness,
        "Set up the project scaffold.",
        [True, True, True, True],
        leg_timeout=120.0,
    )
    assert run.finished
    assert os.path.isfile(target), (
        f"the approved file_write never ran: {target} does not exist"
    )


def test_persisted_row_carries_the_protocol_contract(harness: Harness) -> None:
    """What ``/messages`` returns is what a refresh has to rebuild from.

    ``UNIFIED_TURN_BLOCK.md`` §6 lists what a turn record must carry. Unit
    tests assert each field in isolation; this asserts they all survive a
    real turn through the real routes, which is where a field gets dropped
    by a serializer nobody thought about.
    """
    from tests.e2e._unified_turn_harness import api_client

    run = drive_turn(
        harness,
        "Set up the project scaffold.",
        [True, True, True, True],
        leg_timeout=120.0,
    )
    assert run.finished

    with api_client(harness.base) as client:
        resp = client.get(
            f"{harness.base}/api/chat/sessions/{run.session_id}/messages",
            timeout=30.0,
        )
        assert resp.status_code == 200, resp.status_code
        messages = resp.json()

    assistant = [m for m in messages if m.get("role") == "assistant"]
    assert assistant, "the finished turn is not in the transcript"
    row = assistant[-1]

    assert str(row.get("turn_id") or "").strip(), "no stable turn id"
    assert int(row.get("rev") or 0) > 0, (
        "the durable row carries no revision, so a late snapshot cannot be "
        "told from a current one (invariant U05)"
    )
    assert int(row.get("schema") or 0) >= 2, (
        f"row written at schema {row.get('schema')!r}"
    )

    gates = [p for p in (row.get("parts") or []) if p.get("type") == "hitl"]
    assert len(gates) == 4, (
        f"four pauses persisted as {len(gates)} gate parts"
    )
    assert len({p.get("interrupt_id") for p in gates}) == 4, (
        "gate identities collapsed in storage"
    )

    rows = row.get("activity") or []
    assert rows, "the turn persisted no activity at all"
    assert all(str(r.get("id") or "").strip() for r in rows), (
        "an activity row has no stable id, so the renderer cannot keep it "
        "expanded across an update (plan §3)"
    )
    assert len({r["id"] for r in rows}) == len(rows), (
        "two activity rows share an id"
    )


def test_gates_are_registered_and_settled(harness: Harness) -> None:
    """Every pause left a registry row, and none is still pending.

    The gate registry is the decision authority (AGENTS.md §30). A finished
    turn with a pending row would keep the turn open forever
    (``turn_runtime.close_turn``), and a pause with no row is the
    unregistered-gate class the bridge backfills.
    """
    from kazma_core.safety.hitl_gates import gate_for, live_gates

    run = drive_turn(
        harness,
        "Set up the project scaffold.",
        [True, True, True, True],
        leg_timeout=120.0,
    )
    assert run.finished
    for gate_id in run.gate_ids:
        row = gate_for(gate_id)
        assert row is not None, (
            f"gate {gate_id} paused the graph but left no registry row"
        )
        assert getattr(row, "state", "") != "pending", (
            f"gate {gate_id} is still pending after the turn finished"
        )
    # ``live_gates`` is the non-terminal query close_turn consults. A row
    # left in it after a finished turn is the "turn stays open forever"
    # condition, so emptiness here is the assertion, not an accident of the
    # query's scope.
    assert live_gates(run.thread_id) == [], (
        "the finished turn left a non-terminal gate row behind"
    )
