"""TurnDocument parts: reasoning survives a short final hop."""

from __future__ import annotations

from kazma_ui.turn_document import (
    activity_of,
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
