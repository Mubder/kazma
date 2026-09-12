"""The approval path for a process that has no bus.

`kazma mcp` is spawned by an MCP client, so it cannot see the in-process
approval bus that lives in the Kazma server. Before this, `check()` saw
`NullBusAdapter`, failed closed, and every danger tool was withheld — 55 of
them, with a banner explaining why (`docs/MCP_SERVER.md`).

The bridge queues into the gate registry instead, which is a shared SQLite
table both processes can see. Most of what follows is about the ways that must
*not* work: the only route to an approval is a human claiming a row this
process wrote, and every other outcome — no watcher, a stale one, a timeout, a
vanished row, an unreadable database — denies.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from kazma_core.safety import bus_bridge as bb
from kazma_core.safety import hitl_gates as hg


@pytest.fixture
def gate_db(tmp_path, monkeypatch):
    """A private gate database, and a bridge that polls fast enough to test."""
    path = str(tmp_path / "gates.db")
    hg.set_db_path_for_tests(path)
    hg.ensure_gate_schema()
    monkeypatch.setattr(bb, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.delenv("KAZMA_BUS_BRIDGE", raising=False)
    monkeypatch.delenv("KAZMA_GATE_REGISTRY", raising=False)
    yield path
    hg.set_db_path_for_tests(None)
    bb._schema_ready.discard(path)


@pytest.fixture
def watching(gate_db):
    """A live Kazma instance, heartbeating into the same database."""
    bb.record_watcher(kind="ui", detail="test")
    return gate_db


async def _decide_when_pending(decision: str, *, actor: str = "operator") -> str:
    """Act as the operator: wait for the card, then click."""
    for _ in range(500):
        rows = await asyncio.to_thread(hg.pending_gates)
        bridge = [r for r in rows if r.mechanism == "bus_bridge"]
        if bridge:
            await asyncio.to_thread(hg.claim_gate, bridge[0].gate_id, decision, actor)
            return bridge[0].gate_id
        await asyncio.sleep(0.01)
    raise AssertionError("no bridge gate was ever registered")


# ── the path that works ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_human_approval_reaches_the_waiting_process(watching):
    """The whole point: a card in the dashboard, an answer back out."""
    decider = asyncio.create_task(_decide_when_pending("approve"))
    approved = await bb.request_approval_via_registry(
        tool_name="shell_exec", tool_args="ls -la", timeout=10
    )
    await decider
    assert approved is True


@pytest.mark.asyncio
async def test_a_human_denial_reaches_it_too(watching):
    decider = asyncio.create_task(_decide_when_pending("deny"))
    approved = await bb.request_approval_via_registry(
        tool_name="shell_exec", tool_args="rm -rf /", timeout=10
    )
    await decider
    assert approved is False


@pytest.mark.asyncio
async def test_the_card_carries_what_the_operator_needs_to_decide(watching):
    """A card saying only "a tool wants to run" is not a decision aid."""
    seen: dict = {}

    async def _capture() -> None:
        for _ in range(500):
            rows = await asyncio.to_thread(hg.pending_gates)
            if rows:
                seen.update(rows[0].to_dict())
                await asyncio.to_thread(hg.claim_gate, rows[0].gate_id, "deny", "op")
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(_capture())
    await bb.request_approval_via_registry(
        tool_name="file_write", tool_args="/etc/hosts", timeout=10
    )
    await task

    assert seen["tool"] == "file_write"
    assert "/etc/hosts" in seen["message"]
    assert seen["mechanism"] == "bus_bridge", "must be distinguishable from a graph gate"
    assert seen["tenant_id"], "an unstamped row would be claimable by any tenant"


# ── the ways it must refuse ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nobody_watching_means_refuse_now_not_wait(gate_db):
    """Queueing into a database nobody reads is worse than saying no.

    The caller would block for the full timeout and be denied anyway, having
    learned nothing — and the MCP client's model would have spent the whole
    time believing an approval was coming.
    """
    started = time.monotonic()
    approved = await bb.request_approval_via_registry(
        tool_name="shell_exec", timeout=30
    )
    assert approved is False
    assert time.monotonic() - started < 2.0, "it waited instead of refusing"
    assert hg.pending_gates() == [], "nothing should have been queued"


@pytest.mark.asyncio
async def test_a_stale_heartbeat_is_not_a_watcher(gate_db, monkeypatch):
    """The operator went home. The instance is gone. Nothing has noticed yet."""
    bb.record_watcher(kind="ui")
    monkeypatch.setattr(bb, "WATCHER_STALE_SECONDS", -1.0)

    assert bb.live_watcher() is None
    assert await bb.request_approval_via_registry(tool_name="shell_exec", timeout=5) is False


@pytest.mark.asyncio
async def test_a_timeout_denies(watching):
    """Nobody clicks. The tool does not run."""
    approved = await bb.request_approval_via_registry(
        tool_name="shell_exec", timeout=1
    )
    assert approved is False


@pytest.mark.asyncio
async def test_a_vanished_row_denies(watching, monkeypatch):
    """If something else is administering this database we cannot read a
    decision out of it, and an unreadable decision is not an approval."""

    async def _delete_when_pending() -> None:
        for _ in range(500):
            rows = await asyncio.to_thread(hg.pending_gates)
            if rows:
                conn = hg._connect()
                conn.execute("DELETE FROM hitl_gates WHERE gate_id = ?", (rows[0].gate_id,))
                conn.commit()
                conn.close()
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(_delete_when_pending())
    approved = await bb.request_approval_via_registry(tool_name="shell_exec", timeout=10)
    await task
    assert approved is False


@pytest.mark.asyncio
async def test_an_unreadable_database_denies(watching, monkeypatch):
    def _boom() -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(bb, "_connect", _boom)
    assert bb.live_watcher() is None
    assert await bb.request_approval_via_registry(tool_name="shell_exec", timeout=5) is False


@pytest.mark.asyncio
async def test_the_kill_switch_restores_the_old_behaviour_exactly(watching, monkeypatch):
    """An operator who wants danger tools withheld should not have to
    downgrade to get that back."""
    monkeypatch.setenv("KAZMA_BUS_BRIDGE", "0")

    assert bb.bridge_enabled() is False
    assert bb.live_watcher() is None
    assert await bb.request_approval_via_registry(tool_name="shell_exec", timeout=5) is False
    bb.record_watcher(kind="ui")  # must not even write a beat


@pytest.mark.asyncio
async def test_the_registry_kill_switch_is_honoured_too(watching, monkeypatch):
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    assert await bb.request_approval_via_registry(tool_name="shell_exec", timeout=5) is False


@pytest.mark.asyncio
async def test_only_an_approval_approves(watching):
    """Anything that is not an approve/yolo decision is a denial.

    A gate settled as `timeout`, `orphaned` or anything else invented later
    must not read as consent because it merely stopped being pending.
    """

    async def _settle_oddly() -> None:
        for _ in range(500):
            rows = await asyncio.to_thread(hg.pending_gates)
            if rows:
                await asyncio.to_thread(hg.claim_gate, rows[0].gate_id, "orphaned", "sweeper")
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(_settle_oddly())
    approved = await bb.request_approval_via_registry(tool_name="shell_exec", timeout=10)
    await task
    assert approved is False


# ── the heartbeat ───────────────────────────────────────────────────────────


def test_a_heartbeat_is_visible_to_another_reader(gate_db):
    assert bb.live_watcher() is None
    bb.record_watcher(kind="ui", detail="hitl-timeout-watchdog")
    w = bb.live_watcher()
    assert w is not None
    assert w.kind == "ui" and w.detail == "hitl-timeout-watchdog"
    assert w.age_seconds < 5


def test_heartbeats_do_not_accumulate_one_row_per_beat(gate_db):
    for _ in range(5):
        bb.record_watcher(kind="ui")
    conn = hg._connect()
    try:
        n = conn.execute("SELECT COUNT(*) FROM hitl_watchers").fetchone()[0]
    finally:
        conn.close()
    assert n == 1, "a restart-per-beat table would grow without bound"


def test_a_failing_heartbeat_never_raises(gate_db, monkeypatch):
    """It is written from inside the approval-timeout watchdog. Taking that
    loop down to report a missed beat would cost more than the beat."""

    def _boom() -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(bb, "_connect", _boom)
    bb.record_watcher(kind="ui")  # must not raise


def test_the_bridge_uses_the_registrys_own_database_path(gate_db):
    """Two processes disagreeing about which file is *the* gate database is
    the failure this module exists to avoid, so the path is never recomputed
    here — it is asked for."""
    assert bb.gate_db_path() == hg._db_path()


# ── the gate actually uses it ───────────────────────────────────────────────
#
# The bridge existing is not the claim. The claim is that
# `LocalToolRegistry.execute()` -> `SafetyMiddleware.check()` reaches a human
# through it, and still refuses when it cannot. The MCP server contributes
# nothing to either outcome, which is the point (architecture note H-8).


@pytest.fixture
def headless_safety(gate_db, monkeypatch):
    """SafetyMiddleware with HITL on and no bus — the `kazma mcp` situation."""
    from kazma_core.swarm import bus as bus_mod
    from kazma_core.swarm.safety import SafetyMiddleware

    bus = bus_mod.get_message_bus()
    monkeypatch.setattr(bus, "_adapter", bus_mod.NullBusAdapter(), raising=False)
    safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
    safety.approval_timeout = 10
    return safety


@pytest.mark.asyncio
async def test_check_queues_for_a_human_when_one_is_watching(headless_safety):
    bb.record_watcher(kind="ui")
    decider = asyncio.create_task(_decide_when_pending("approve"))
    allowed = await headless_safety.check("shell_exec", "echo hi", task_id="t1")
    await decider
    assert allowed is True


@pytest.mark.asyncio
async def test_check_still_denies_when_the_human_says_no(headless_safety):
    bb.record_watcher(kind="ui")
    decider = asyncio.create_task(_decide_when_pending("deny"))
    allowed = await headless_safety.check("shell_exec", "echo hi", task_id="t1")
    await decider
    assert allowed is False


@pytest.mark.asyncio
async def test_check_refuses_exactly_as_before_when_nobody_is_watching(headless_safety):
    """The regression that matters. With no watcher the behaviour must be
    byte-for-byte what it was before the bridge existed: refuse, immediately,
    without queueing anything."""
    started = time.monotonic()
    allowed = await headless_safety.check("shell_exec", "echo hi", task_id="t1")
    assert allowed is False
    assert time.monotonic() - started < 2.0
    assert hg.pending_gates() == []


@pytest.mark.asyncio
async def test_a_safe_tool_never_touches_the_bridge(headless_safety):
    """Read-tier tools run immediately. A bridge that gated them would put a
    card in front of the operator for every `file_read`."""
    bb.record_watcher(kind="ui")
    assert await headless_safety.check("file_read", "/etc/hosts") is True
    assert hg.pending_gates() == []


@pytest.mark.asyncio
async def test_the_bridge_cannot_turn_a_disabled_gate_on(gate_db, monkeypatch):
    """HITL off means execute() runs danger tools unattended — that is a
    separate (documented) posture, and the bridge must not quietly start
    gating things the operator turned off."""
    from kazma_core.swarm.safety import SafetyMiddleware

    bb.record_watcher(kind="ui")
    safety = SafetyMiddleware(enabled=False)
    assert await safety.check("shell_exec", "echo hi") is True
    assert hg.pending_gates() == []


# ── what the MCP server tells the client ────────────────────────────────────


def test_the_mcp_server_publishes_danger_tools_when_a_watcher_is_live(gate_db, monkeypatch):
    from kazma_core.mcp.server import ApprovalPath
    from kazma_core.swarm import bus as bus_mod
    from kazma_core.swarm.safety import SafetyMiddleware, set_safety

    bus = bus_mod.get_message_bus()
    monkeypatch.setattr(bus, "_adapter", bus_mod.NullBusAdapter(), raising=False)
    set_safety(SafetyMiddleware(enabled=True, allow_headless_danger=False))
    monkeypatch.delenv("KAZMA_MCP_ALLOW_UNGATED", raising=False)

    assert ApprovalPath.detect().gated is False, "no watcher yet"

    bb.record_watcher(kind="ui")
    path = ApprovalPath.detect()
    assert path.gated is True
    assert path.overridden is False, "this is a real approval path, not an override"
    assert "watching the gate registry" in path.reason


def test_the_banner_says_why_when_it_still_cannot_gate(gate_db, monkeypatch):
    """A client-spawned server with nothing running behind it must say so in
    words the operator can act on, not just publish fewer tools."""
    from kazma_core.mcp.server import ApprovalPath
    from kazma_core.swarm import bus as bus_mod
    from kazma_core.swarm.safety import SafetyMiddleware, set_safety

    bus = bus_mod.get_message_bus()
    monkeypatch.setattr(bus, "_adapter", bus_mod.NullBusAdapter(), raising=False)
    set_safety(SafetyMiddleware(enabled=True, allow_headless_danger=False))
    monkeypatch.delenv("KAZMA_MCP_ALLOW_UNGATED", raising=False)

    reason = ApprovalPath.detect().reason
    assert "no running Kazma instance is watching" in reason
