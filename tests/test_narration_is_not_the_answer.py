"""A paused turn has not answered yet.

Reported from the installed build, 2026-09-20: while the model worked,
text appeared **under the CoT block** — in the answer region — and was
replaced the moment Approve was clicked.

Measured cause. Mid-turn there is no final text to compare a stream
against, so ``split_stream_and_final(stream, stream)`` classifies
everything streamed as ``text``. The separation only happens
retroactively, when a differing final arrives — which on a resume leg is
the backfill frame. So the reader watches narration sit in the answer
region and then get displaced.

The client now folds narration into the thoughts region the moment the
model asks to act (``turn_document.js:foldNarration``). This file locks
the other half: what gets **persisted** at a pause, so that a refresh
mid-pause shows the same thing the live page does.

That agreement is not cosmetic. ``UNIFIED_TURN_BLOCK.md`` §10's
convergence oracle requires live delivery and fresh hydration to
normalize identically; a fix applied to only one of them would make the
two disagree while every existing test still passed, because each side
would be self-consistent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def persist_parts():
    from kazma_ui.sse_chat._streaming import _hitl_persist_parts

    return _hitl_persist_parts


GATE = {"interrupt_id": "g1", "tool": "file_write", "args": {"path": "README.md"}}


def _of_type(parts, kind):
    return [p for p in (parts or []) if isinstance(p, dict) and p.get("type") == kind]


# ══════════════════════════════════════════════════════════════════════
# Paused
# ══════════════════════════════════════════════════════════════════════


def test_a_paused_turn_persists_its_text_as_thinking(persist_parts) -> None:
    """The turn stopped to ask permission, so it has not replied."""
    parts = persist_parts("Reading the layout first.", True, GATE)

    assert not _of_type(parts, "text"), (
        "a paused turn persisted an answer; a refresh mid-pause will show "
        "narration under the CoT block as though the model had replied"
    )
    thoughts = _of_type(parts, "reasoning")
    assert len(thoughts) == 1
    assert thoughts[0]["text"] == "Reading the layout first."


def test_the_gate_still_survives_the_pause(persist_parts) -> None:
    """Refresh mid-pause must still rebuild the card — that is what this
    function existed for before it learned about narration."""
    parts = persist_parts("Reading the layout first.", True, GATE)
    gates = _of_type(parts, "hitl")
    assert len(gates) == 1
    assert gates[0]["interrupt_id"] == "g1"
    assert gates[0]["state"] == "pending"


def test_a_pause_with_nothing_said_persists_no_thought(persist_parts) -> None:
    """An empty narration is not a thought. A blank row in the fold is
    worse than no row: it says the model thought something and lost it."""
    parts = persist_parts("", True, GATE)
    assert not _of_type(parts, "reasoning")
    assert not _of_type(parts, "text")
    assert len(_of_type(parts, "hitl")) == 1


def test_whitespace_only_narration_is_not_a_thought(persist_parts) -> None:
    parts = persist_parts("   \n\t ", True, GATE)
    assert not _of_type(parts, "reasoning")


# ══════════════════════════════════════════════════════════════════════
# Finished
# ══════════════════════════════════════════════════════════════════════


def test_a_finished_turn_still_persists_its_answer(persist_parts) -> None:
    """The other direction, and the one that would hurt most to break."""
    parts = persist_parts("Scaffold ready.", False, None)
    texts = _of_type(parts, "text")
    assert len(texts) == 1
    assert texts[0]["text"] == "Scaffold ready."


def test_superseded_narration_still_becomes_a_thought(persist_parts) -> None:
    """The pre-existing multi-hop case, unchanged.

    When the terminal content differs from what was streamed, the
    streamed text was narration and the difference is the answer. That
    behaviour predates this fix and must survive it.
    """
    parts = persist_parts(
        "Scaffold ready.", False, None,
        streamed="First I will read the layout.",
    )
    assert [p["text"] for p in _of_type(parts, "text")] == ["Scaffold ready."]
    assert [p["text"] for p in _of_type(parts, "reasoning")] == [
        "First I will read the layout."
    ]


# ══════════════════════════════════════════════════════════════════════
# The client half, and the agreement between them
# ══════════════════════════════════════════════════════════════════════


def test_the_projector_folds_narration_when_the_model_acts() -> None:
    """Source-level, because the behavioural proof is in
    ``tests/js/test_turn_document.js`` where the projector runs.

    Named here so the two halves of one fix are findable from either
    side: if this disappears while the persist half stays, live and
    hydrated diverge silently.
    """
    js = (
        ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules"
        / "turn_document.js"
    ).read_text(encoding="utf-8")
    assert "function foldNarration(doc)" in js, (
        "the client no longer folds narration at a gate; it will sit in "
        "the answer region while the stored row calls it a thought"
    )
    code = "\n".join(
        line for line in js.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )
    assert code.count("foldNarration(next)") >= 2, (
        "foldNarration is no longer called from both the gate and the "
        "tool-start paths"
    )
    assert "doc.stream = ''" in code, (
        "the fold leaves `stream` populated, so the next leg's tokens "
        "append to the narration and rebuild the text part it removed"
    )


def test_a_thought_renders_as_markdown_not_as_source() -> None:
    """Also reported: the CoT block showed literal ``` fences.

    ``_detailHtml`` escapes everything, which is right for a tool payload
    — a JSON result or a shell transcript is wanted verbatim — and wrong
    for a thought, which is prose the model wrote with fences in it.
    """
    chat = (
        ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js"
    ).read_text(encoding="utf-8")
    body = chat.split("function _detailHtml(detail, forceExpanded, kind)", 1)
    assert len(body) > 1, "_detailHtml no longer takes the row kind"
    body = body[1].split("\n  /**", 1)[0]
    assert "kind === 'thought'" in body
    assert "KS.markdown" in body, "a thought is escaped again"
    assert "escapeHtml(t)" in body, (
        "everything is rendered as markdown now; a tool payload must stay "
        "verbatim"
    )
    # Truncation can cut inside a fence, and an unclosed fence swallows
    # the rest of the row.
    assert "fences % 2" in body, (
        "a truncated thought can leave an unclosed code fence"
    )


def test_the_rendered_thought_has_styling_that_fits_a_row() -> None:
    """`white-space: pre-wrap` is for escaped source.

    Markdown already emits block elements, so leaving pre-wrap on doubles
    every blank line and the row grows to twice its height inside a fold
    that is meant to be scannable.
    """
    css = (
        ROOT / "kazma-ui" / "kazma_ui" / "static" / "css" / "kazma.css"
    ).read_text(encoding="utf-8")
    assert ".step-detail.step-detail-md" in css
    block = css.split(".step-detail.step-detail-md", 1)[1][:200]
    assert "white-space: normal" in block, (
        "a rendered thought still has pre-wrap; every blank line doubles"
    )
    assert ".step-detail-md pre" in css and "overflow-x: auto" in css, (
        "a code block in a thought widens the whole workbench panel"
    )
