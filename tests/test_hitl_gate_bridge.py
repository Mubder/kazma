"""P1 dual-write bridge — registry mirrors the web HITL lifecycle.

Legacy stays authoritative in P1; the bridge must never raise and never
change user-visible behavior. These tests exercise the bridge directly.
"""

from __future__ import annotations

import pytest

from kazma_core.safety import hitl_gates as hg
from kazma_core.safety.hitl_gates import (
    gate_for,
    live_gates,
    make_gate_id,
    pending_gates,
    register_gate,
    GateRow,
)
from kazma_ui.hitl_gate_bridge import (
    gate_claimed,
    gate_pending_from_payload,
    gate_resuming,
    settle_thread_gates,
)


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    hg.gate_events._subs = []
    yield
    hg.set_db_path_for_tests(None)


def _payload(thread="t1", tool="file_write", iid="intr-1", **kw):
    p = {
        "thread_id": thread,
        "tool": tool,
        "args": {"path": "x.txt"},
        "kind": "security",
        "message": "needs approval",
    }
    if iid is not None:
        p["interrupt_id"] = iid
    p.update(kw)
    return p


async def test_pending_items_hide_hash_twin():
    """Dashboard listed native-id AND hash-id rows as two cards."""
    from kazma_ui.hitl_gate_bridge import pending_items_from_registry

    await gate_pending_from_payload(_payload())
    alias = make_gate_id("t1", "file_write", {"path": "x.txt"})
    conn = hg._connect()
    try:
        conn.execute(
            "INSERT INTO hitl_gates (gate_id, alias_id, thread_id, tenant_id, "
            "session_id, turn_id, mechanism, kind, tool, args_json, message, "
            "payload_json, state, decision, actor, supersedes, created_at) "
            "VALUES (?, '', 't1', '', '', '', 'graph', 'security', 'file_write', "
            "'{\"path\": \"x.txt\"}', '', '', 'pending', '', '', '', ?)",
            (alias, __import__("time").time()),
        )
        conn.commit()
    finally:
        conn.close()
    assert len(pending_gates()) == 2
    items = await pending_items_from_registry()
    assert items is not None and len(items) == 1
    assert items[0]["interrupt_id"] == "intr-1"


async def test_pending_from_payload_registers_row():
    await gate_pending_from_payload(_payload(), session_id="s1", turn_id="turn1")
    rows = pending_gates()
    assert len(rows) == 1
    assert rows[0].gate_id == "intr-1"
    assert rows[0].session_id == "s1" and rows[0].turn_id == "turn1"
    assert rows[0].alias_id == make_gate_id("t1", "file_write", {"path": "x.txt"})


async def test_pending_idempotent_across_polls():
    await gate_pending_from_payload(_payload())
    await gate_pending_from_payload(_payload())
    assert len(pending_gates()) == 1


async def test_alias_converges_pre_pause_registration():
    # A graph-side pre-registration used the hash id...
    alias = make_gate_id("t1", "file_write", {"path": "x.txt"})
    register_gate(GateRow(gate_id=alias, thread_id="t1", tool="file_write"))
    # ...the post-stream scan then registers with the real LangGraph id.
    await gate_pending_from_payload(_payload(iid="intr-real"))
    assert len(pending_gates()) == 1  # ONE row — window closed
    assert gate_for("intr-real") is not None
    assert gate_for(alias).gate_id == "intr-real"


async def test_missing_interrupt_id_falls_back_to_alias():
    await gate_pending_from_payload(_payload(iid=None))
    rows = pending_gates()
    assert len(rows) == 1
    assert rows[0].gate_id == make_gate_id("t1", "file_write", {"path": "x.txt"})


async def test_claim_records_decision():
    await gate_pending_from_payload(_payload())
    await gate_claimed("t1", "intr-1", "approve", "web:me")
    row = gate_for("intr-1")
    assert row.state == "claimed" and row.decision == "approve" and row.actor == "web:me"


async def test_claim_creates_missing_row_reconcile_on_approve():
    # Registry never saw the pause (crash window) — approve must backfill it.
    await gate_claimed("t1", "intr-lost", "approve", "web:me",
                       tool="shell_exec", payload={"kind": "security"})
    row = gate_for("intr-lost")
    assert row is not None and row.state == "claimed" and row.tool == "shell_exec"


async def test_claim_conflict_never_raises():
    await gate_pending_from_payload(_payload())
    await gate_claimed("t1", "intr-1", "approve", "a")
    # Different decision — registry conflicts, bridge swallows (P1 legacy wins).
    await gate_claimed("t1", "intr-1", "deny", "b")
    assert gate_for("intr-1").decision == "approve"  # winner untouched


async def test_resuming_then_settle_leaves_second_pending():
    # THE incident shape: gate #1 approved+resuming, gate #2 arrives pending.
    await gate_pending_from_payload(_payload(iid="w", tool="file_write"))
    await gate_claimed("t1", "w", "approve", "me", tool="file_write")
    await gate_resuming("w")
    assert gate_for("w").state == "resuming"
    await gate_pending_from_payload(_payload(iid="d", tool="file_delete"))
    # Turn drive hits terminal for gate #1's resume:
    await settle_thread_gates("t1")
    assert gate_for("w").state == "settled"
    assert gate_for("d").state == "pending"       # the live question SURVIVES
    assert [r.gate_id for r in live_gates("t1")] == ["d"]


async def test_bridge_noop_when_kill_switch_off(monkeypatch):
    monkeypatch.setenv("KAZMA_GATE_REGISTRY", "0")
    await gate_pending_from_payload(_payload())
    monkeypatch.delenv("KAZMA_GATE_REGISTRY")
    assert pending_gates() == []


async def test_bridge_survives_registry_failure(monkeypatch):
    # Point the registry at an impossible path — bridge must not raise.
    hg.set_db_path_for_tests(r"\\?\nonexistent-device\gates.db")
    await gate_pending_from_payload(_payload())
    await gate_claimed("t1", "intr-1", "approve", "a")
    await settle_thread_gates("t1")


# ── gateway thread-level claim (P3) ─────────────────────────────────────────


async def test_thread_claim_claims_oldest_pending():
    from kazma_ui.hitl_gate_bridge import gate_claimed_for_thread

    await gate_pending_from_payload(_payload(iid="g-old", tool="file_write"))
    await gate_pending_from_payload(_payload(iid="g-new", tool="file_delete"))
    await gate_claimed_for_thread("t1", "approve", "telegram:42")
    assert gate_for("g-old").state == "claimed"
    assert gate_for("g-old").actor == "telegram:42"
    assert gate_for("g-new").state == "pending"   # second question stays live


async def test_thread_claim_backfills_when_registry_missed_the_pause():
    from kazma_ui.hitl_gate_bridge import gate_claimed_for_thread

    await gate_claimed_for_thread(
        "t-gw", "deny", "discord:7",
        tool="shell_exec", payload={"kind": "security", "args": {}},
    )
    rows = live_gates("t-gw")
    assert len(rows) == 1
    assert rows[0].state == "claimed" and rows[0].decision == "deny"
