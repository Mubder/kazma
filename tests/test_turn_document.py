"""TurnDocument parts: reasoning survives a short final hop."""

from __future__ import annotations

from kazma_ui.turn_document import (
    activity_of,
    merge_hitl_part,
    merge_parts,
    parts_from_stream,
    split_stream_and_final,
    text_of,
)


def test_split_prefix_is_one_text_part() -> None:
    r, t = split_stream_and_final("Hello", "Hello world")
    assert r == ""
    assert t == "Hello world"


def test_split_distinct_hops_keeps_reasoning() -> None:
    notes = "There's a live API endpoint GET /api/settings/agent/safety"
    final = "Everything checks out. The verification is complete."
    r, t = split_stream_and_final(notes, final)
    assert r == notes
    assert t == final


def test_parts_from_stream_round_trip_activity() -> None:
    parts = parts_from_stream(
        streamed="Let me read the file.",
        final="The timeout is 300 seconds.",
        activity=[
            {
                "kind": "tool",
                "title": "file_read",
                "detail": "hitl.py",
                "state": "done",
            }
        ],
    )
    assert text_of(parts) == "The timeout is 300 seconds."
    act = activity_of(parts)
    kinds = {row["kind"] for row in act}
    assert "thought" in kinds
    assert "tool" in kinds


def test_merge_hitl_pending_cannot_replace_approved() -> None:
    """Replay of approval_required after Approve must not resurrect buttons."""
    approved = [{
        "type": "hitl",
        "tool": "file_write",
        "state": "approved",
        "interrupt_id": "abc",
        "payload": {"tool": "file_write", "interrupt_id": "abc", "path": "x"},
    }]
    replay = [{
        "type": "hitl",
        "tool": "file_write",
        "state": "pending",
        "interrupt_id": "abc",
        "payload": {"tool": "file_write", "interrupt_id": "abc"},
    }]
    merged = merge_parts(approved, replay)
    hitl = [p for p in merged if p.get("type") == "hitl"]
    assert len(hitl) == 1
    assert hitl[0]["state"] == "approved"
    assert (hitl[0].get("payload") or {}).get("path") == "x"


def test_merge_hitl_new_interrupt_id_gets_its_own_slot() -> None:
    """A turn that pauses twice keeps BOTH gates.

    The 2026-09-19 "approved twice then silence" incident. ``_part_key``
    used to return a bare ``("hitl",)``, so the second gate overwrote the
    first: the document held one decision while the transcript showed two
    cards, and the renderer had no authority left to reconcile against.
    """
    first = [{
        "type": "hitl",
        "tool": "file_write",
        "state": "approved",
        "interrupt_id": "one",
        "payload": {"interrupt_id": "one"},
    }]
    second = [{
        "type": "hitl",
        "tool": "python_exec",
        "state": "pending",
        "interrupt_id": "two",
        "payload": {"interrupt_id": "two", "tool": "python_exec"},
    }]
    merged = merge_parts(first, second)
    hitl = [p for p in merged if p.get("type") == "hitl"]
    assert len(hitl) == 2
    # Ask order is preserved — the transcript reads top to bottom.
    assert hitl[0]["interrupt_id"] == "one"
    assert hitl[0]["state"] == "approved", "the first gate must keep its claim"
    assert hitl[1]["interrupt_id"] == "two"
    assert hitl[1]["state"] == "pending"


def test_hitl_part_of_prefers_the_gate_still_waiting() -> None:
    """``/status`` must report the gate blocking the graph, not the newest."""
    from kazma_ui.turn_document import hitl_part_of, hitl_parts_of

    parts = [
        {"type": "hitl", "tool": "a", "state": "approved", "interrupt_id": "one"},
        {"type": "hitl", "tool": "b", "state": "pending", "interrupt_id": "two"},
        {"type": "hitl", "tool": "c", "state": "approved", "interrupt_id": "three"},
    ]
    assert hitl_part_of(parts)["interrupt_id"] == "two"
    assert len(hitl_parts_of(parts)) == 3
    settled = [p for p in parts if p["state"] != "pending"]
    assert hitl_part_of(settled)["interrupt_id"] == "three", "falls back to newest"
    assert hitl_part_of([]) is None


def test_merge_hitl_new_tool_without_ids_is_a_new_gate() -> None:
    """Approve file_write then file_delete in the same turn must not stay approved."""
    claimed = merge_hitl_part(
        None,
        {"type": "hitl", "tool": "file_write", "state": "approved", "payload": {"tool": "file_write"}},
    )
    nxt = merge_hitl_part(
        claimed,
        {"type": "hitl", "tool": "file_delete", "state": "pending", "payload": {"tool": "file_delete"}},
    )
    assert nxt["state"] == "pending"
    assert nxt["tool"] == "file_delete"


def test_merge_hitl_pending_replaces_with_approved() -> None:
    pending = [{
        "type": "hitl",
        "tool": "python_exec",
        "state": "pending",
        "payload": {"tool": "python_exec"},
    }]
    decided = [{
        "type": "hitl",
        "tool": "python_exec",
        "state": "approved",
        "payload": {"tool": "python_exec"},
    }]
    merged = merge_parts(pending, decided)
    hitl = [p for p in merged if p.get("type") == "hitl"]
    assert len(hitl) == 1
    assert hitl[0]["state"] == "approved"


def test_reasoning_merges_into_one_fold() -> None:
    from kazma_ui.turn_document import merge_parts, merge_reasoning_part

    a = [{"type": "reasoning", "text": "First notes."}]
    b = [{"type": "reasoning", "text": "Second hop."}]
    merged = merge_parts(a, b)
    thoughts = [p for p in merged if p.get("type") == "reasoning"]
    assert len(thoughts) == 1
    assert "First notes." in thoughts[0]["text"]
    assert "Second hop." in thoughts[0]["text"]
    grown = merge_reasoning_part(
        {"type": "reasoning", "text": "Let me look."},
        {"type": "reasoning", "text": "Let me look. Found it."},
    )
    assert grown["text"] == "Let me look. Found it."


def test_merge_displaced_text_becomes_reasoning() -> None:
    existing = [{"type": "text", "text": "let me pull the texts…"}]
    incoming = [{"type": "text", "text": "Posted all 4 Arabic tweets."}]
    merged = merge_parts(existing, incoming)
    assert text_of(merged) == "Posted all 4 Arabic tweets."
    assert any(
        p.get("type") == "reasoning" and "let me pull" in str(p.get("text"))
        for p in merged
    )
    again = merge_parts(merged, [{"type": "text", "text": "Posted all 4 Arabic tweets."}])
    assert sum(1 for p in again if p.get("type") == "reasoning") >= 1


def test_messages_api_forwards_parts_and_derived_activity() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui"
        / "kazma_ui"
        / "sse_chat"
        / "__init__.py"
    ).read_text(encoding="utf-8")
    assert 'item["parts"] = parts' in src
    assert "activity_of(parts)" in src
    assert '"turn_id"' in src or "item[\"turn_id\"]" in src
    assert "_extras" in src
    assert '"parts"' in src
    assert "hydrate_message" in src


def test_empty_hitl_stamp_keeps_answer_text() -> None:
    """stamp_hitl_part_state must not clobber a stored answer."""
    existing = [
        {"type": "hitl", "state": "pending", "interrupt_id": "a", "payload": {"tool": "x"}},
        {"type": "text", "text": "A" * 1367},
    ]
    incoming = [{"type": "hitl", "state": "approved", "interrupt_id": "a", "payload": {}}]
    merged = merge_parts(existing, incoming)
    assert text_of(merged) == "A" * 1367
    hitl = [p for p in merged if p.get("type") == "hitl"]
    assert hitl[0]["state"] == "approved"


def test_hydrate_message_fills_activity_and_turn_id() -> None:
    from kazma_ui.turn_document import hydrate_message, text_of

    raw = {
        "role": "assistant",
        "content": "The timeout is 300 seconds.",
        "activity": [
            {
                "kind": "tool",
                "title": "file_read",
                "detail": "hitl.py",
                "state": "done",
            }
        ],
    }
    out = hydrate_message(raw)
    assert out["turn_id"].startswith("legacy-")
    assert text_of(out["parts"]) == "The timeout is 300 seconds."
    kinds = {row["kind"] for row in (out.get("activity") or [])}
    assert "tool" in kinds


def test_chat_js_restores_cot_from_parts() -> None:
    from pathlib import Path

    chat = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui"
        / "kazma_ui"
        / "static"
        / "js"
        / "chat.js"
    ).read_text(encoding="utf-8")
    assert "KazmaTurnDocument.activityForMessage" in chat
    assert "KazmaTurnDocument.activityOf" in chat
    begin = chat.split("function beginTurn(opts)", 1)[1].split("\n  function ", 1)[0]
    assert "oldProg.remove()" not in begin
