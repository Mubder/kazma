"""One decision settles one gate — ``docs/plans/UNIFIED_TURN_BLOCK.md`` §7.

Incident, 2026-09-20, found by
``tests/e2e/test_unified_turn_concurrency.py::test_a_lost_response_does_not_execute_twice``:

    POST /api/approve/<thread> {interrupt_id: G1}  -> 200, running
    ... the graph resumes, executes G1's tool, and pauses again at G2
    POST /api/approve/<thread> {interrupt_id: G1}  -> 200, running
                                                     view.tool = shell_exec

The retry is what a double-click, a lost 200 or a stale tab produces.
The route's only liveness test was "is this THREAD paused" — it read the
pending interrupt off the checkpoint and resumed it, using the body's
gate id for nothing but stamping the response. So the second POST
authorized the question the human had never seen.

Plan §7: "A lost acknowledgement triggers status recovery, not an
unconditional new approval command." Invariant U11: transport loss is
not authorization.

The e2e suite owns the end-to-end proof; it needs a real graph, a real
pause and about a minute. This file is the fast lock on the rule itself,
so a refactor that drops the check fails in seconds rather than in the
slow job.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest


@pytest.fixture
def guard():
    from kazma_ui.routes_direct.misc import _gate_not_pending

    return _gate_not_pending


@dataclass
class _Row:
    gate_id: str
    thread_id: str
    state: str


def _call(guard, thread_id: str, gate_id: str) -> str:
    return asyncio.run(guard(thread_id, gate_id))


@pytest.fixture
def registry(monkeypatch):
    """Stand in for hitl_gates with an in-memory table.

    Patched at ``kazma_core.safety.hitl_gates`` because the route imports
    it inside the function — the same reason the real module is import-
    cheap there.
    """
    rows: dict[str, _Row] = {}
    enabled = {"on": True}

    import kazma_core.safety.hitl_gates as hg

    async def _gate_for_async(gate_id: str):
        return rows.get(gate_id)

    monkeypatch.setattr(hg, "gate_for_async", _gate_for_async)
    monkeypatch.setattr(hg, "gate_registry_enabled", lambda: enabled["on"])
    return rows, enabled


# ══════════════════════════════════════════════════════════════════════
# The rule
# ══════════════════════════════════════════════════════════════════════


def test_a_pending_gate_is_not_objected_to(guard, registry) -> None:
    """The normal Approve click must be untouched."""
    rows, _ = registry
    rows["G1"] = _Row("G1", "t1", "pending")
    assert _call(guard, "t1", "G1") == ""


@pytest.mark.parametrize("state", ["claimed", "resuming", "settled", "denied",
                                   "expired", "failed", "superseded"])
def test_an_answered_gate_refuses_a_second_decision(
    guard, registry, state: str
) -> None:
    """``LIVE_STATES`` is (pending, claimed, resuming) — but claimed and
    resuming both mean *somebody already answered this*. Only ``pending``
    is an open question, and only an open question may be resumed."""
    rows, _ = registry
    rows["G1"] = _Row("G1", "t1", state)
    assert _call(guard, "t1", "G1") == state, (
        f"a gate in state {state!r} accepted a second decision"
    )


def test_a_gate_belonging_to_another_thread_is_refused(guard, registry) -> None:
    """Refusing by state would be luck. Ownership is the actual rule."""
    rows, _ = registry
    rows["G1"] = _Row("G1", "other-thread", "pending")
    assert _call(guard, "t1", "G1") == "foreign"


# ══════════════════════════════════════════════════════════════════════
# What it must NOT do
# ══════════════════════════════════════════════════════════════════════


def test_an_unknown_gate_id_is_not_an_objection(guard, registry) -> None:
    """The dashboard, the TUI and the gateway all reach this route with
    ids the registry may not carry. A lookup miss must fall through to
    the route's existing behavior, not start refusing them."""
    _rows, _ = registry
    assert _call(guard, "t1", "never-registered") == ""


def test_no_gate_id_is_not_an_objection(guard, registry) -> None:
    """A body with no id at all is the oldest client shape there is."""
    _rows, _ = registry
    assert _call(guard, "t1", "") == ""


def test_the_kill_switch_disables_the_check(guard, registry) -> None:
    """``KAZMA_GATE_REGISTRY=0`` turns the registry off. A check that
    keeps refusing from a registry nobody is writing would strand every
    approval on the system it was disabled to bypass."""
    rows, enabled = registry
    rows["G1"] = _Row("G1", "t1", "settled")
    enabled["on"] = False
    assert _call(guard, "t1", "G1") == ""


def test_a_broken_registry_fails_open(guard, monkeypatch) -> None:
    """Fail-open on plumbing, never on a recorded decision.

    A human is waiting behind this call. A registry that cannot be read
    is a reason to fall back to the route's existing liveness check, not
    a reason to refuse the only person who can unblock the turn.
    """
    import kazma_core.safety.hitl_gates as hg

    async def _boom(_gate_id: str):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(hg, "gate_for_async", _boom)
    monkeypatch.setattr(hg, "gate_registry_enabled", lambda: True)
    assert _call(guard, "t1", "G1") == ""


# ══════════════════════════════════════════════════════════════════════
# The call site
# ══════════════════════════════════════════════════════════════════════


def test_the_route_consults_the_guard_before_resuming() -> None:
    """The rule is worthless if the resume path stops asking.

    Source-level because the ordering is the point: the guard has to run
    before ``read_pending_interrupt``, which is the call that picks up
    whichever question the graph happens to be parked on.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "routes_direct" / "misc.py"
    ).read_text(encoding="utf-8")

    guard_at = src.index("_already = await _gate_not_pending(")
    read_at = src.index("_intr_payload = await read_pending_interrupt(")
    assert guard_at < read_at, (
        "the gate-identity check now runs AFTER the pending interrupt is "
        "read; a retry can reach the resume again"
    )
    between = src[guard_at:read_at]
    assert "status_code=409" in between, (
        "the guard no longer refuses — it must answer 409 with the "
        "server's actual view (plan §7), not fall through"
    )
    assert '"reason": "not_pending"' in between, (
        "the 409 carries no machine-readable reason, so the client cannot "
        "tell a stale decision from a real failure"
    )

def test_the_websocket_resume_consults_the_same_guard() -> None:
    """HTTP is not the only mouth, and the rule must reach both.

    ``POST /api/approve`` closed this on 2026-09-20. The WebSocket
    ``approve_tool`` handler did not, because the check lived inside the HTTP
    ROUTE MODULE rather than in the bridge both callers share — so the
    identical defect survived one transport over, dormant only because
    ``KAZMA_WS_GRAPH`` is off by default. "Dormant behind an escape hatch" is
    not "closed": the hatch exists to be used, and the diagnosis map tells
    operators how to turn it on.

    Same ordering requirement as the HTTP twin above: the guard must run
    BEFORE ``read_pending_interrupt``, which picks up whichever question the
    graph happens to be parked on right now.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "routes" / "ws_chat.py"
    ).read_text(encoding="utf-8")

    guard_at = src.index("_already = await gate_not_pending(")
    read_at = src.index("_intr_payload = await read_pending_interrupt(")
    assert guard_at < read_at, (
        "the WS gate-identity check now runs AFTER the pending interrupt is "
        "read; a retry or a sequential second pause can reach the resume again"
    )
    between = src[guard_at:read_at]
    assert '"code": "GATE_NOT_PENDING"' in between, (
        "the WS refusal carries no machine-readable code, so the client "
        "cannot tell a stale decision from a real failure"
    )
    assert '"hitl_state": _client_state' in between, (
        "the WS refusal must report the server's view so the client can "
        "reconcile, the same way the HTTP 409 does"
    )


def test_both_mouths_share_one_gate_identity_implementation() -> None:
    """One function, not two copies that can drift.

    A gate-identity check that only one caller performs is not a check. This
    is the object-identity assertion, so a future "small tidy-up" that
    re-inlines a private copy into either route fails here rather than in an
    incident.
    """
    from kazma_ui.hitl_gate_bridge import gate_not_pending
    from kazma_ui.routes_direct.misc import _gate_not_pending

    assert gate_not_pending is _gate_not_pending, (
        "routes_direct/misc.py no longer shares the bridge's gate-identity "
        "check — the WS and HTTP resume paths can now disagree"
    )


# ══════════════════════════════════════════════════════════════════════
# The refusal has to be readable
# ══════════════════════════════════════════════════════════════════════


def test_an_inflight_refusal_converges_instead_of_erroring() -> None:
    """``claimed``/``resuming`` must reach the client as ``inflight``.

    ``chat.js`` (~5853) splits an approve 409 on::

        running || hitl_state === 'inflight' || hitl_state === 'approved'

    True  -> paint the row inflight, re-attach, converge.
    False -> paint the row ERRORED, then throw the view away and resync.

    A double-clicked Approve lands on a gate the registry calls
    "claimed". Passed through raw it matches neither arm, so the
    commonest case this guard exists for would flash an error on a row
    that was approved perfectly well.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "routes_direct" / "misc.py"
    ).read_text(encoding="utf-8")
    guard = src[src.index("_already = await _gate_not_pending("):]
    guard = guard[: guard.index("_intr_payload = await read_pending_interrupt(")]

    assert '"inflight" if _already in ("claimed", "resuming")' in guard, (
        "the 409 no longer translates the registry's live states into the "
        "client's 'inflight'; a retried approval will render as an error"
    )
    assert '"hitl_state": _client_state' in guard, (
        "hitl_state is being sent from something other than the mapped "
        "client state"
    )
    assert '"registry_state": _already' in guard, (
        "the raw registry state is no longer reported alongside; an "
        "operator reading a 409 cannot tell claimed from resuming"
    )


def test_the_client_still_reads_hitl_state_the_way_the_route_writes_it() -> None:
    """The other end of the same contract.

    Two files, one agreement, no shared symbol. If chat.js stops testing
    ``hitl_state``, the route is translating into a vocabulary nobody
    reads and the translation should be revisited rather than left as
    decoration.
    """
    from pathlib import Path

    js = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")
    assert "res.body.hitl_state === 'inflight'" in js, (
        "chat.js no longer converges on an inflight 409; the route's "
        "translation in routes_direct/misc.py is now pointless"
    )
