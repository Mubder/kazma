"""Corpus for resolve_gate_views — disagreement cases, not default-resolver.

Plan: docs/plans/HITL_VIEW_MODEL.md §5. Overlay (corpus case 3) is a
client map applied AFTER this function; it is not a second table here.
"""

from __future__ import annotations

from kazma_core.safety.hitl_gates import GateRow
from kazma_ui.gate_view import (
    apply_views_to_parts,
    resolve_gate_views,
    stamp_parts_for_read,
)


def _part(
    iid: str,
    state: str,
    *,
    tool: str = "file_write",
    kind: str = "security",
) -> dict:
    return {
        "type": "hitl",
        "state": state,
        "interrupt_id": iid,
        "tool": tool,
        "payload": {"interrupt_id": iid, "tool": tool, "kind": kind},
    }


def _row(
    gid: str,
    state: str,
    *,
    tool: str = "file_write",
    alias: str = "",
    kind: str = "security",
    decision: str = "",
) -> GateRow:
    return GateRow(
        gate_id=gid,
        thread_id="t1",
        tool=tool,
        state=state,
        alias_id=alias,
        kind=kind,
        decision=decision,
    )


def _by_id(views: list) -> dict[str, dict]:
    return {str(v["interrupt_id"]): v for v in views}


def test_non_authoritative_omits_everything() -> None:
    """Corpus 7: registry unread → no chrome, even for a pending part."""
    views = resolve_gate_views(
        [_part("g1", "pending")],
        [_row("g1", "pending")],
        authoritative=False,
    )
    assert views == []


def test_missing_live_list_without_authority_omits() -> None:
    views = resolve_gate_views(
        [_part("g1", "pending")],
        None,
        authoritative=False,
    )
    assert views == []


def test_part_pending_row_claimed_is_inflight_above_answer() -> None:
    """Corpus 1: 2026-09-19 screenshot — sort vs label disagreement."""
    views = resolve_gate_views(
        [_part("g1", "pending")],
        [_row("g1", "claimed")],
        authoritative=True,
    )
    assert len(views) == 1
    v = views[0]
    assert v["state"] == "inflight"
    assert v["interactive"] is False
    assert v["slot"] == "settled"


def test_part_approved_row_pending_stays_live() -> None:
    """Corpus 2: never invent Approved while the registry is still pending."""
    views = resolve_gate_views(
        [_part("g1", "approved")],
        [_row("g1", "pending")],
        authoritative=True,
    )
    v = views[0]
    assert v["state"] == "pending"
    assert v["interactive"] is True
    assert v["slot"] == "pending"


def test_authoritative_no_row_stale_pending_is_error_not_buttons() -> None:
    """Corpus 4: ghost-card. A leftover pending stamp is not a live question."""
    views = resolve_gate_views(
        [_part("g1", "pending")],
        [],
        authoritative=True,
    )
    v = views[0]
    assert v["state"] == "error"
    assert v["interactive"] is False
    assert v["slot"] == "settled"


def test_authoritative_no_row_approved_keeps_approved_label() -> None:
    """Corpus 5: the common case — twenty historical approvals.

    Not generic 'resolved'. Not pending. No buttons.
    """
    views = resolve_gate_views(
        [_part("g1", "approved")],
        [],
        authoritative=True,
    )
    v = views[0]
    assert v["state"] == "approved"
    assert v["interactive"] is False
    assert v["slot"] == "settled"


def test_authoritative_no_row_denied_keeps_denied_label() -> None:
    """Corpus 6."""
    views = resolve_gate_views(
        [_part("g1", "denied", tool="file_delete")],
        [],
        authoritative=True,
    )
    v = views[0]
    assert v["state"] == "denied"
    assert v["interactive"] is False
    assert v["slot"] == "settled"
    assert v["tool"] == "file_delete"


def test_two_parts_one_pending_one_claimed_preserve_ask_order() -> None:
    """Corpus 8."""
    parts = [
        _part("a", "approved", tool="file_write"),
        _part("b", "pending", tool="file_delete"),
    ]
    rows = [
        _row("a", "claimed", tool="file_write"),
        _row("b", "pending", tool="file_delete"),
    ]
    views = resolve_gate_views(parts, rows, authoritative=True)
    assert [v["interrupt_id"] for v in views] == ["a", "b"]
    by = _by_id(views)
    assert by["a"]["state"] == "inflight"
    assert by["a"]["slot"] == "settled"
    assert by["a"]["interactive"] is False
    assert by["b"]["state"] == "pending"
    assert by["b"]["slot"] == "pending"
    assert by["b"]["interactive"] is True


def test_alias_id_covers_the_part() -> None:
    """Two-id rule: part carries the hash id, row has native + alias."""
    views = resolve_gate_views(
        [_part("hash-x", "pending")],
        [_row("intr-real", "pending", alias="hash-x")],
        authoritative=True,
    )
    v = views[0]
    assert v["gate_id"] == "intr-real"
    assert v["interrupt_id"] == "hash-x"
    assert v["interactive"] is True
    assert v["state"] == "pending"


def test_live_row_without_a_part_still_emits() -> None:
    """A pending registry row with no persisted part is still a live question."""
    views = resolve_gate_views(
        [],
        [_row("g-live", "pending", tool="shell_exec")],
        authoritative=True,
    )
    assert len(views) == 1
    assert views[0]["gate_id"] == "g-live"
    assert views[0]["interactive"] is True
    assert views[0]["slot"] == "pending"


def test_interactivity_never_comes_from_the_part_stamp() -> None:
    """Negative control: a pending stamp without a covering row is NOT live."""
    views = resolve_gate_views(
        [_part("g1", "pending")],
        [],
        authoritative=True,
    )
    assert views[0]["interactive"] is False


def test_stamp_does_not_mutate_input_parts() -> None:
    original = [_part("g1", "approved")]
    stamped = stamp_parts_for_read(original, [], authoritative=True)
    assert original[0].get("view") is None
    assert stamped is not None
    assert stamped[0]["view"]["state"] == "approved"
    assert stamped[0]["view"]["interactive"] is False


def test_stamp_omits_view_when_not_authoritative() -> None:
    parts = [_part("g1", "pending")]
    stamped = stamp_parts_for_read(parts, [_row("g1", "pending")], authoritative=False)
    assert stamped is not None
    assert "view" not in stamped[0]


def test_view_for_interrupt_claimed_row_is_inflight(tmp_path) -> None:
    from kazma_core.safety import hitl_gates as hg
    from kazma_core.safety.hitl_gates import claim_gate, register_gate
    from kazma_ui.gate_view import view_for_interrupt

    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    try:
        register_gate(_row("g1", "pending"))
        claim_gate("g1", "approve", "web:test")
        v = view_for_interrupt("t1", "g1", tool="file_write")
        assert v is not None
        assert v["state"] == "inflight"
        assert v["interactive"] is False
        assert v["slot"] == "settled"
        assert v["gate_id"] == "g1"
    finally:
        hg.set_db_path_for_tests(None)


def test_status_and_messages_wire_is_additive() -> None:
    """A0: gate_views is added; gates stays until A1."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui" / "kazma_ui" / "sse_chat" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert '"gate_views": gate_views' in src
    assert '"gates": gates' not in src
    assert "stamp_parts_for_read" in src


def test_apply_views_copies() -> None:
    parts = [_part("g1", "pending")]
    views = resolve_gate_views(parts, [_row("g1", "pending")], authoritative=True)
    out = apply_views_to_parts(parts, views)
    assert out is not parts
    assert out[0]["view"]["interactive"] is True


def test_attach_view_decision_frame_beats_pending_row(tmp_path) -> None:
    """Emit-before-CAS: frame is approved, live row still pending.

    The stamped view must follow the frame, not the leftover pending row.
    """
    from kazma_core.safety import hitl_gates as hg
    from kazma_core.safety.hitl_gates import register_gate
    from kazma_ui.gate_view import attach_view_to_hitl_frame

    hg.set_db_path_for_tests(str(tmp_path / "gates.db"))
    try:
        register_gate(_row("g1", "pending"))
        frame = {
            "type": "hitl",
            "data": {
                "state": "approved",
                "interrupt_id": "g1",
                "tool": "file_write",
                "thread_id": "t1",
            },
        }
        out = attach_view_to_hitl_frame(frame, "t1")
        view = (out.get("data") or {}).get("view") or {}
        assert view.get("state") == "approved"
        assert view.get("interactive") is False
        assert view.get("slot") == "settled"
        assert "gate_views" in (out.get("data") or {})
    finally:
        hg.set_db_path_for_tests(None)


def test_approve_emits_after_cas() -> None:
    from pathlib import Path

    ui = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"
    # The approve route records through the one decision writer...
    route = (ui / "routes_direct" / "misc.py").read_text(encoding="utf-8")
    assert "await record_gate_decision(" in route
    # ...which claims the registry before it tells the journal. The same
    # order is asserted behaviourally in tests/test_gate_decision_recorder.py.
    src = (ui / "hitl_decision.py").read_text(encoding="utf-8")
    claim_at = src.index("await gate_claimed(")
    emit_at = src.index("get_turn_broker().emit(")
    assert claim_at < emit_at, "journal emit still runs before the registry CAS"


def test_delivery_stamps_hitl_and_done_frames() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parent.parent
        / "kazma-ui"
        / "kazma_ui"
        / "delivery.py"
    ).read_text(encoding="utf-8")
    assert "is_hitl_frame_type" in src
    assert "attach_gate_views_to_done_frame" in src
    assert 'ftype in ("done", "turn_complete")' in src
