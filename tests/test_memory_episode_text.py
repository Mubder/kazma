"""Recall shows the model what was asked AND what was answered (R3, 2026-09-27).

Recall showed an episode as "the summary, else the question, else the
answer": with a question present, the model saw what the user had asked and
never what it had answered, so a recalled "what did you suggest?" came back
without the suggestion. ``memory/episode_text.py`` is now the one home of an
episode's texts: ``display_text`` shows both sides; ``embed_text`` keeps the
vector question-first, as measured (see that module) -- embedding the answer
too lost precision in four benchmark categories and gained nothing.
"""

from __future__ import annotations

import ast
import sqlite3
import time
from pathlib import Path

import pytest

from kazma_core.memory.episode_text import display_text, embed_text
from kazma_core.memory.schema_v2 import ensure_primary_schema

REPO = Path(__file__).resolve().parents[1]


def test_recall_shows_who_said_what():
    assert display_text("Which road?", "The coastal highway.") == (
        "User: Which road? / Assistant: The coastal highway.")
    assert display_text("q" * 400, "a" * 900) == f"User: {'q' * 199}… / Assistant: {'a' * 299}…"
    # One side is shown as it is: a swarm result is not something the user said.
    assert display_text("Task: build Result: done", None) == "Task: build Result: done"
    assert display_text(None, None, "summary only") == "summary only"
    assert display_text("  spaced \n out ", "") == "spaced out"


def test_the_vector_text_is_the_question_as_measured():
    """The summary, else the question, else the answer: the first not blank."""
    assert embed_text("Which road?", "The coastal highway.") == "Which road?"
    assert embed_text("Which road?", "The coastal highway.", "  ") == "Which road?"
    assert embed_text("", "Only an answer.") == "Only an answer."
    assert embed_text("q", "a", "a summary") == "a summary"
    assert embed_text("  padded  ", None) == "padded"


def test_a_recalled_turn_carries_its_answer(tmp_path, monkeypatch):
    """Through recall itself: the answer reaches the injected text."""
    from kazma_core.memory import recall as recall_mod
    from kazma_core.memory.dual_write import episode_row

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    conn = sqlite3.connect(str(tmp_path / "memory_state.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    row = episode_row(session_id="s1", turn_number=1, source="benchmark",
                      user_text="What restaurant should we book for Noor's birthday in Porto?",
                      assistant_text="Book Cantinho do Avillez: seafood for her, a strong meat menu for you.")
    conn.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "summary_text, tier, structural_importance, created_at, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,'{}')",
        (row["id"], "default", "s1", 1, row["user_text"], row["assistant_text"],
         row["summary_text"], "episodic", 1, time.time()),
    )
    conn.commit()
    result = recall_mod.recall("restaurant for Noor's birthday in Porto", conn=conn)
    [hit] = [h for h in result.episodes if h.id == row["id"]]
    block = recall_mod.format_recall_block(result)
    assert "Assistant: Book Cantinho do Avillez" in block and "User: What restaurant" in block
    # Compared and de-duplicated by the question, as before: showing the
    # answer must not change which memories recall keeps.
    assert hit.content == row["user_text"]
    conn.close()


# ── one home ──────────────────────────────────────────────────────────────


def _picks_one_of_the_texts(tree: ast.AST) -> list[int]:
    """The old way of choosing an episode's text: an Or whose first choice is
    the summary and a later one a side of the turn.

    Episode ids, dedupe keys and presence checks take the question first
    (``user_text or ... summary_text``) and are not texts anyone reads.
    """
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            first, rest = ast.unparse(node.values[0]), ast.unparse(ast.Tuple(node.values[1:]))
            if "summary_text" in first and ("user_text" in rest or "assistant_text" in rest):
                lines.append(node.lineno)
    return lines


def test_every_episode_text_comes_from_one_place():
    offenders = []
    for path in sorted((REPO / "kazma-core" / "kazma_core" / "memory").glob("*.py")):
        if path.name == "episode_text.py":
            continue
        offenders += [f"{path.name}:{line}"
                      for line in _picks_one_of_the_texts(ast.parse(path.read_text(encoding="utf-8")))]
    assert not offenders, (
        "An episode's embedded or shown text is chosen outside memory/episode_text.py -- "
        "the answer went missing that way:\n  " + "\n  ".join(offenders))
    old = 'def show(row):\n    return (row["summary_text"] or row["user_text"] or row["assistant_text"] or "")[:400]\n'
    assert _picks_one_of_the_texts(ast.parse(old)) == [2]  # negative control


@pytest.mark.parametrize("source", ["benchmark", "golden_eval"])
def test_the_benchmark_and_the_golden_eval_embed_what_live_turns_embed(source):
    from kazma_core.memory.dual_write import episode_row

    row = episode_row(session_id="s", turn_number=1, user_text=" Which road? ",
                      assistant_text="The coastal highway.", source=source)
    assert row["embed_text"] == embed_text(" Which road? ", "The coastal highway.") == "Which road?"
