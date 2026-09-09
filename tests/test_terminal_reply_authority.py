"""Terminal reply authority — the final synthesis IS the answer.

Born from the live 2026-09-09 duplication incident: a unified-/unrestricted
mission streamed 6,455 chars of multi-hop narration (containing an interim
results table), then respond_node synthesized the 2,813-char final (which
repeated the table). Both text-selection rules — prose-max in
``pick_user_facing_text`` and longer-wins in ``resolve_reply_text`` —
preferred the NARRATION, so the stored reply was the progress log and the
user read the answer twice.

The fix is terminal authority: when a turn COMPLETED (no pending interrupt,
checkpoint holds the synthesis), the checkpoint text wins regardless of
length. Longer-wins survives only for cancelled/interrupted turns (the
2026-08-27 fragment incident). The superseded narration is preserved as a
``reasoning`` part — auditable, rendered in the CoT accordion, never the
reply.
"""

from __future__ import annotations

from kazma_ui.reply_sink import resolve_reply_text
from kazma_ui.turn_document import parts_from_stream, text_of

NARRATION = (
    "Starting the 6 checks now — running the independent probes in parallel.\n"
    "Check 6 already passed live. Retry round 1: paths were wrong.\n"
    "Status: Partial — interim table follows…\n"
    "# 1 MCP servers FAIL\n# 2 Unified fields PARTIAL\n…\n"
    * 3
)
FINAL = "Status: Partial — 2/6 strict PASS.\n# 4 PASS\n# 6 PASS\nTotal: 2/6."


def test_terminal_final_beats_longer_narration() -> None:
    """The incident case: shorter final synthesis wins over longer narration."""
    chosen = resolve_reply_text(FINAL, NARRATION, terminal=True)
    assert chosen == FINAL.strip()
    assert "Starting the 6 checks" not in chosen


def test_cancelled_turn_keeps_longer_streamed() -> None:
    """2026-08-27 regression guard: no synthesis → streamed accumulation wins."""
    fragment = "Saved."  # checkpoint holds only the last hop
    streamed = "Working… step 1 done… step 2 done… swept 96 seconds…"
    chosen = resolve_reply_text(fragment, streamed, terminal=False)
    assert chosen == streamed


def test_terminal_with_empty_checkpoint_falls_back_to_streamed() -> None:
    chosen = resolve_reply_text("", NARRATION, terminal=True)
    assert chosen == NARRATION.strip()


def test_terminal_without_streamed_returns_final() -> None:
    chosen = resolve_reply_text(FINAL, "", terminal=True)
    assert chosen == FINAL.strip()


def test_displaced_narration_becomes_reasoning_part() -> None:
    """parts_from_stream: superseded narration is preserved, not dropped."""
    parts = parts_from_stream(streamed=NARRATION, final=FINAL)
    assert text_of(parts) == FINAL.strip()
    reasoning = [p for p in parts if p.get("type") == "reasoning"]
    assert reasoning, "narration must survive as a reasoning part"
    assert "Starting the 6 checks" in reasoning[0]["text"]


def test_prefix_streamed_stays_single_text() -> None:
    """Normal streaming (narration is a prefix of the final) — one text part."""
    final = NARRATION + "\nAll checks done."
    parts = parts_from_stream(streamed=NARRATION, final=final)
    assert text_of(parts) == final.strip()
    assert not [p for p in parts if p.get("type") == "reasoning"]


def test_hitl_persist_parts_carries_streamed() -> None:
    from kazma_ui.sse_chat._streaming import _hitl_persist_parts

    parts = _hitl_persist_parts(FINAL, False, None, streamed=NARRATION)
    assert parts is not None
    assert text_of(parts) == FINAL.strip()
    assert [p for p in parts if p.get("type") == "reasoning"]


def test_hitl_part_still_appended_with_streamed() -> None:
    from kazma_ui.sse_chat._streaming import _hitl_persist_parts

    parts = _hitl_persist_parts(
        "", True, {"tool": "shell_exec", "interrupt_id": "iid-1"},
        streamed=NARRATION,
    )
    assert parts is not None
    kinds = [p.get("type") for p in parts]
    assert "hitl" in kinds
    # Interrupted pause with NO synthesis: the narration IS the pending
    # row text (close_turn's interrupted rule) — no reasoning displacement
    # because there is no final to displace it.
    assert text_of(parts) == NARRATION.strip()
