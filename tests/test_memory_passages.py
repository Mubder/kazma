"""Passages: meaning search reaches what a memory's own vector misses.

The memory plan's W5 + W1b (2026-11-22, the main session's decisions). A
memory's vector is made from the start of its first side that is not blank
(the question, as a rule): what the assistant answered was never searched by
meaning. A passage is a vector of about 256 tokens of what that vector
misses -- the answer, a question past 4,000 characters -- cut at sentence
breaks (``memory/passages.py``). The memories whose best passage is among
the question's nearest are candidates the reranker reads beside that
passage (W3), and the block shows the stretch it was kept by.

Held here through the product's writer and the real ``recall``, in English
and Arabic, with a concept embedder in which a question shares no word with
the answer it asks about (so keyword search cannot find it). A forget, a
text change, a removal and a move to another space take a memory's
passages; an archive keeps them; passages written after their memory
changed are not stored; the repair fills old memories. How recall uses them
(candidates for the reranker, W3) is held by ``tests/test_memory_rerank.py``.
"""

from __future__ import annotations

import math
import re
import sqlite3
from types import SimpleNamespace

import pytest
from kazma_core.memory import passages
from kazma_core.memory.passages import _passage_spans, missed_spans
from kazma_core.summarizer import estimate_text_tokens

#: Words that mean one thing, in either language, share a dimension.
_CONCEPTS = {
    0: ("lentil", "lentils", "legume", "legumes", "عدس", "البقوليات", "بقوليات", "العدس"),
    1: ("soup", "broth", "شوربة", "مرق", "الشوربة", "المرق"),
    2: ("cook", "cooking", "dinner", "أطبخ", "عشاء", "العشاء"),
    3: ("weather", "rain", "طقس", "مطر"),
    4: ("cumin", "spice", "كمون", "بهار"),
}
_DIM = 16


class _ConceptEmbedder:
    """A word's concept, else a hash of it: a question about "legume broth"
    meets an answer about "lentil soup" with no word in common."""

    dim = _DIM

    def __init__(self) -> None:
        self.calls = 0
        self.hook = None

    def encode(self, text: str) -> list[float]:
        self.calls += 1
        if self.hook is not None:
            self.hook(text)
        vec = [0.0] * _DIM
        for word in re.findall(r"\w+", (text or "").lower()):
            dim = next((d for d, words in _CONCEPTS.items() if word in words), None)
            if dim is None:
                dim = 5 + sum(ord(c) for c in word) % (_DIM - 5)
                vec[dim] += 0.02  # a word of no concept weighs little, as filler does in meaning
            else:
                vec[dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    embedder = _ConceptEmbedder()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "concepts-test")
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    dual_write._reset_mirror()
    yield SimpleNamespace(db=db, embedder=embedder)
    dual_write._reset_mirror()
    db.close()


# ── cutting ──────────────────────────────────────────────────────────────

_LONG = {
    "en": " ".join(f"Sentence number {i} says something about the topic at hand." for i in range(120)),
    "ar": " ".join(f"هذه جملة رقم {i} تقول شيئا عن الموضوع المطروح هنا." for i in range(120)),
}


@pytest.mark.parametrize("language", ["en", "ar"])
def test_passages_are_cut_at_sentence_breaks(language) -> None:
    text = _LONG[language]
    spans = _passage_spans(text)
    assert len(spans) > 2
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    for (start, stop), (next_start, _next_stop) in zip(spans, spans[1:]):
        assert estimate_text_tokens(text[start:stop]) <= 256
        assert text[stop - 1] in ".!?؟" and text[next_start - 1] in " ."  # whole sentences
        assert start < next_start < stop  # every passage overlaps the one after it
        assert estimate_text_tokens(text[next_start:stop]) <= 32


def test_a_sentence_longer_than_a_passage_is_cut_at_spaces() -> None:
    text = "word " * 600
    spans = _passage_spans(text)
    assert len(spans) >= 2 and all(estimate_text_tokens(text[a:b]) <= 260 for a, b in spans)


def test_what_a_memorys_own_vector_misses() -> None:
    assert missed_spans("What should I cook?", "Try lentil soup.") == [("answer", 0, 16)]
    assert missed_spans("", "An answer alone is its own vector.") == []
    long_question = "x" * 4500 + "."
    assert {s[0] for s in missed_spans(long_question, "")} == {"question"}
    assert all(stop > 4000 for _side, _start, stop in missed_spans(long_question, ""))
    # A memory whose vector came from its summary: both sides are missed.
    assert [s[0] for s in missed_spans("Q here.", "A here.", "A summary.")] == ["question", "answer"]


# ── writing, and what takes them ─────────────────────────────────────────


def _write(question: str, answer: str, *, session: str = "s1", turn: int = 1, tenant: str = "default") -> str:
    from kazma_core.memory.dual_write import mirror_episode

    eid = mirror_episode(session_id=session, turn_number=turn, user_text=question, assistant_text=answer,
                         source="chat", tenant_id=tenant)
    assert eid
    return eid


def _passages(db, eid: str) -> list[tuple]:
    return [tuple(r) for r in db.execute(
        "SELECT side, start, stop FROM episode_passages WHERE episode_id = ? ORDER BY side, start", (eid,))]


def test_the_writer_stores_passages_only_while_they_are_on(mem) -> None:
    off = _write("What should I cook tonight?", "Try a red lentil soup with cumin.", session="off")
    assert _passages(mem.db, off) == []
    with passages.used():
        on = _write("What should I cook tonight?", "Try a red lentil soup with cumin.", session="on")
    assert _passages(mem.db, on) == [("answer", 0, 33)]


def test_a_forget_a_text_change_a_removal_and_a_move_take_the_passages(mem) -> None:
    with passages.used():
        ids = [_write(f"Question {i}?", f"Answer number {i} about lentil soup.", session=f"t{i}")
               for i in range(5)]
    assert all(_passages(mem.db, eid) for eid in ids)
    mem.db.execute("UPDATE episodes SET tier = 'archived' WHERE id = ?", (ids[0],))
    mem.db.execute("UPDATE episodes SET tier = 'forgotten', user_text = '', assistant_text = '' WHERE id = ?",
                   (ids[1],))
    mem.db.execute("UPDATE episodes SET assistant_text = 'Changed.' WHERE id = ?", (ids[2],))
    mem.db.execute("DELETE FROM episodes WHERE id = ?", (ids[3],))
    mem.db.execute("UPDATE episodes SET tenant_id = 'another-space' WHERE id = ?", (ids[4],))
    mem.db.commit()
    assert _passages(mem.db, ids[0])  # archived: still recallable, still searched
    assert [_passages(mem.db, eid) for eid in ids[1:]] == [[], [], [], []]
    assert mem.db.execute("SELECT COUNT(*) FROM episode_passage_state WHERE episode_id IN (?, ?, ?, ?)",
                          ids[1:]).fetchone()[0] == 0


def test_passages_of_a_memory_that_changed_while_they_were_made_are_not_stored(mem) -> None:
    with passages.used():
        eid = _write("What should I cook tonight?", "Try a red lentil soup with cumin.")
        mem.db.execute("DELETE FROM episode_passages WHERE episode_id = ?", (eid,))
        mem.db.commit()

        def change(_text: str) -> None:
            other = sqlite3.connect(mem.db.execute("PRAGMA database_list").fetchone()[2])
            other.execute("UPDATE episodes SET assistant_text = 'Something else.' WHERE id = ?", (eid,))
            other.commit()
            other.close()
            mem.embedder.hook = None

        mem.embedder.hook = change
        assert passages.write_passages(mem.db, eid) is None
    assert _passages(mem.db, eid) == []


def test_the_repair_fills_old_memories_newest_first_within_its_budget(mem) -> None:
    ids = [_write(f"Question {i}?", f"Answer number {i}.", session=f"old{i}") for i in range(4)]
    mem.db.execute("UPDATE episodes SET created_at = created_at + rowid")
    mem.db.commit()
    assert passages.fill_passages(mem.db) == {"filled": 0, "passages": 0}  # off: nothing
    with passages.used():
        assert passages.fill_passages(mem.db, budget=2) == {"filled": 2, "passages": 2}
        assert [bool(_passages(mem.db, eid)) for eid in ids] == [False, False, True, True]
        assert passages.fill_passages(mem.db, budget=10)["filled"] == 2
        assert passages.fill_passages(mem.db, budget=10)["filled"] == 0  # every one done


def test_a_measured_store_missing_a_passage_is_refused(mem) -> None:
    """The benchmark's seeding fills every passage or refuses the store: a
    replayed recording without a passage's text answers None (the embedder
    "failed"), and a store measured with passages missing would score
    passages that were never there."""
    from kazma_core.memory.benchmark import fill_passages_whole

    _write("What should I cook tonight?", "Try a red lentil soup with cumin.", session="a")
    _write("Will it rain?", "Light rain after noon.", session="b")
    assert fill_passages_whole(mem.db) == 0  # off: nothing filled, nothing checked
    unrecorded = "Light rain after noon."

    def missing(text: str) -> None:
        if text == unrecorded:
            raise KeyError(text)

    mem.embedder.hook = missing
    with passages.used(), pytest.raises(RuntimeError, match="1 memories got no passages"):
        fill_passages_whole(mem.db)
    mem.embedder.hook = None
    with passages.used():
        assert fill_passages_whole(mem.db) == 1  # the one left, now recorded


# ── recall ───────────────────────────────────────────────────────────────
#
# Passages bring candidates for the reranker (W3) and never lift a memory's
# meaning (counted that way they lost answers, W5): a memory found by its
# passage, read by it and shown at its stretch, on both recall paths, is held
# by ``tests/test_memory_rerank.py``, with passages alone as the control.
