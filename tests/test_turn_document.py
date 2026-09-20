"""TurnDocument parts: reasoning survives a short final hop."""

from __future__ import annotations

import re

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


# ══════════════════════════════════════════════════════════════════════════
# Tool call identity — UNIFIED_TURN_BLOCK.md Phase 1
# ══════════════════════════════════════════════════════════════════════════
#
# Mirrors the block of the same name in tests/js/test_turn_document.js. Both
# sides are also driven over one shared corpus by
# tests/test_unified_turn_fixtures.py; these are the unit-level rules, kept
# here so a change to merge semantics fails next to the code it belongs to.


def test_tool_part_key_is_the_call_id() -> None:
    """The old key was name + state + result[:80].

    Two consequences, both wrong: the SAME call changed identity when it
    finished, so the renderer rebuilt its row (losing expansion and focus)
    on every update; and two concurrent calls to one tool shared a key, so
    the second overwrote the first.
    """
    from kazma_ui.turn_document import part_key_str

    running = {"type": "tool", "name": "file_read", "call_id": "run-1",
               "result": "", "state": "running"}
    done = {"type": "tool", "name": "file_read", "call_id": "run-1",
            "result": "alpha", "state": "done"}
    other = {"type": "tool", "name": "file_read", "call_id": "run-2",
             "result": "alpha", "state": "done"}

    assert part_key_str(running) == "tool#run-1"
    assert part_key_str(running) == part_key_str(done)
    assert part_key_str(done) != part_key_str(other)


def test_tool_part_without_a_call_id_keeps_the_legacy_key() -> None:
    """Old transcripts must keep deduping the way they were written."""
    from kazma_ui.turn_document import part_key_str

    assert part_key_str(
        {"type": "tool", "name": "file_read", "result": "x", "state": "done"}
    ) == "tool:file_read:done:x"


def test_tool_merge_advances_but_never_regresses() -> None:
    """With a stable key the second stamp arrives as a merge, not a new part.

    The duplicate-key branch used to return early, which would have frozen
    every tool row at "running". Going backwards is equally wrong: a
    replayed start frame after the result landed must not un-finish a call.
    """
    from kazma_ui.turn_document import merge_parts, merge_tool_part

    running = {"type": "tool", "name": "file_read", "call_id": "run-1",
               "result": "", "state": "running"}
    done = {"type": "tool", "name": "file_read", "call_id": "run-1",
            "result": "alpha", "state": "done"}

    forward = [p for p in merge_parts([running], [done]) if p["type"] == "tool"]
    assert len(forward) == 1
    assert forward[0]["state"] == "done"
    assert forward[0]["result"] == "alpha"

    backward = [p for p in merge_parts([done], [running]) if p["type"] == "tool"]
    assert len(backward) == 1
    assert backward[0]["state"] == "done"
    assert backward[0]["result"] == "alpha"

    # A terminal frame with no result must not blank the delivered one.
    blanked = merge_tool_part(done, dict(done, result=""))
    assert blanked["result"] == "alpha"


def test_activity_rows_carry_the_part_key() -> None:
    """The renderer keys rows by this id, so it must be the part's own key.

    Deriving it anywhere else is how "which row is this" drifts from "which
    part is this" (plan §3, stable identities).
    """
    from kazma_ui.turn_document import activity_of, part_key_str

    parts = [
        {"type": "tool", "name": "file_read", "call_id": "run-a",
         "result": "alpha", "state": "done"},
        {"type": "tool", "name": "file_read", "call_id": "run-b",
         "result": "beta", "state": "running"},
    ]
    rows = activity_of(parts)
    assert [r["id"] for r in rows] == [part_key_str(p) for p in parts]
    assert [r["id"] for r in rows] == ["tool#run-a", "tool#run-b"]
    # No timestamp means no key at all. JavaScript used to write ts: null
    # here, so the two normalizers produced different shapes for one part.
    assert "ts" not in rows[0]


def test_activity_round_trip_keeps_the_call_id() -> None:
    """A history load must not split one call into two rows."""
    from kazma_ui.turn_document import _activity_to_parts, activity_of

    parts = [
        {"type": "tool", "name": "file_read", "call_id": "run-a",
         "result": "alpha", "state": "done"},
        {"type": "tool", "name": "file_read", "call_id": "run-b",
         "result": "beta", "state": "done"},
    ]
    back = _activity_to_parts(activity_of(parts))
    assert [p.get("call_id") for p in back] == ["run-a", "run-b"]


def test_legacy_turn_id_is_the_shared_value() -> None:
    """Pinned, not just prefix-checked.

    ``tests/js/test_turn_document.js`` asserts the same two strings. A
    "starts with legacy-" assertion on each side is exactly what let sha256
    here and a 32-bit string hash in the browser both look correct for
    months (UNIFIED_TURN_BLOCK_PHASE0.md §6.1).
    """
    from kazma_ui.turn_document import legacy_turn_id

    assert legacy_turn_id(
        {"ts": "2026-09-20T10:00:00Z", "content": "Hello there."}
    ) == "legacy-0cdee065e20a7d91"
    # The hash walks UTF-8 bytes; JavaScript strings are UTF-16, so a naive
    # port agrees on ASCII and diverges on the first Arabic character or
    # emoji — in this product, the normal case.
    assert legacy_turn_id(
        {"ts": "2026-09-20T11:30:00Z", "content": "تم الحفظ 😀"}
    ) == "legacy-4373231117a879b5"


def test_sse_producer_stamps_a_tool_call_id() -> None:
    """The projector can only key by the call id if the producer sends one.

    LangGraph's ``run_id`` is the same on ``on_tool_start`` and the matching
    ``on_tool_end``, which is what makes start and end one row.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui" / "kazma_ui" / "sse_chat" / "_streaming.py"
    ).read_text(encoding="utf-8")
    # There are TWO producers: the astream_events branch and the resume
    # leg, which binds its own delta queue because ainvoke emits no graph
    # events. Both must stamp the id, or activity is keyed by the graph's
    # run id on one path and by nothing on the other.
    for frame in ('"tool_call"', '"tool_result"'):
        blocks = [
            chunk.split("})", 1)[0]
            for chunk in src.split(frame + ",")[1:]
        ]
        assert len(blocks) >= 2, (
            f"expected a {frame} producer on both the streaming and the "
            f"resume path, found {len(blocks)}"
        )
        for block in blocks:
            assert '"tool_call_id"' in block, f"{frame} carries no call id"
            assert 'get("run_id")' in block, (
                f"{frame} invents an id instead of using the graph's run id"
            )


# ══════════════════════════════════════════════════════════════════════════
# Document revision and schema version — UNIFIED_TURN_BLOCK.md Phase 1
# ══════════════════════════════════════════════════════════════════════════


def test_hydrate_message_states_revision_and_schema() -> None:
    """A reader must not have to infer "absent" from "zero".

    A row written before revisions existed is rev 0 / schema 1, said out
    loud, so a client deciding whether a snapshot is stale has an answer
    for every row rather than only for new ones.
    """
    from kazma_ui.turn_document import TURN_SCHEMA_VERSION, hydrate_message

    old = hydrate_message({"role": "assistant", "content": "hi", "ts": "t"})
    assert old["rev"] == 0
    assert old["schema"] == 1

    new = hydrate_message(
        {"role": "assistant", "content": "hi", "ts": "t", "rev": 4,
         "schema": TURN_SCHEMA_VERSION}
    )
    assert new["rev"] == 4
    assert new["schema"] == TURN_SCHEMA_VERSION


def test_upsert_bumps_the_revision_on_every_write(tmp_path, monkeypatch) -> None:
    """The revision orders WRITES to one turn, not frames on a thread.

    Invariant U05 depends on it being monotone: a client that applied rev N
    refuses a snapshot stamped rev < N, which is what stops a slow
    /messages response repainting an older answer over a newer one.

    ``monkeypatch``, not ``os.environ[...] =``. A raw assignment here
    outlived the test and every later one in the session inherited a
    ``KAZMA_DATA_DIR`` pointing at this ``tmp_path`` — which is how
    ``test_ui004_ui008_gateway_misc.py::…::test_file_write_workspace_not_drive_root``
    came to assert ``ws.parent.name == "kazma-data"`` and read
    ``'test_upsert_..._the_revision0'`` instead. It failed only in a full
    run, and passed alone, for two full-suite runs before anyone looked
    at the ordering.
    """
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    from kazma_ui.session_manager import get_session_manager, reset_session_manager
    from kazma_ui.reply_sink import open_reply_turn, upsert_reply
    from kazma_ui.turn_document import TURN_SCHEMA_VERSION

    reset_session_manager()
    sm = get_session_manager()
    sid = "rev-test-session"
    sess = sm.get_or_create(sid)
    sess.thread_id = sid
    sess.messages = [{"role": "user", "content": "go"}]
    sm.put(sess)

    turn = open_reply_turn(sid)

    def _row() -> dict:
        rows = get_session_manager().get_or_create(sid).messages
        return next(m for m in rows if m.get("turn_id") == turn)

    assert upsert_reply(sid, turn, "first")
    first = _row()
    assert first["rev"] == 1
    assert first["schema"] == TURN_SCHEMA_VERSION

    assert upsert_reply(sid, turn, "first and then some more")
    assert _row()["rev"] == 2

    # A straggler that changes nothing visible still advanced the row, so
    # it still advances the revision. The alternative — only bumping on a
    # content change — would let two different states share a revision.
    assert upsert_reply(sid, turn, "", open_turn=True)
    assert _row()["rev"] == 3

    reset_session_manager()


def test_client_refuses_a_stale_snapshot() -> None:
    """The browser half of U05, asserted against the shipped module.

    Behavioral coverage lives in tests/js/test_turn_document.js; this is
    the boundary check that the rule is in the file the page loads, which a
    Node-only test cannot claim.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules"
        / "turn_document.js"
    ).read_text(encoding="utf-8")
    hydrate = src.split("if (type === 'hydrate')", 1)[1].split("if (type === 'token'", 1)[0]
    assert "evRev < Number(doc.rev || 0)" in hydrate, (
        "the hydrate branch no longer refuses an older revision"
    )
    # A snapshot MERGES onto the document; it never assigns over it. The
    # merged list is filtered first (a paused row stores the same sentence
    # as both a thought and a text part, and the classification wins —
    # see test_narration_is_not_the_answer.py), so the assertion is on the
    # merge ONTO next.parts rather than on the exact argument expression.
    assert re.search(r"next\.parts = mergeParts\(next\.parts,", hydrate), (
        "a snapshot assigns over the document again — it covers only what "
        "was durable when taken, so live parts would be dropped"
    )
    assert "next.parts = ev.parts" not in hydrate, (
        "a snapshot is being assigned straight over the document's parts"
    )


def test_chat_js_forwards_the_revision() -> None:
    """A revision the page never sends is a revision that protects nothing."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")
    hydrates = src.count("type: 'hydrate',")
    assert hydrates >= 2, "hydrate construction moved; re-point this check"
    assert src.count("rev: lastMsg.rev,") == hydrates, (
        "a hydrate event is built without forwarding the stored revision"
    )
