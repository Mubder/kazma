"""Passages: meaning search reaches what a memory's own vector misses.

The memory plan's W5 + W1b (2026-11-22, the main session's decisions). A
memory's vector is made from the start of its first side that is not blank
(the question, as a rule): what the assistant answered was never searched by
meaning. A passage is a vector of about 256 tokens of what that vector
misses -- the answer, a question past 4,000 characters -- cut at sentence
breaks (``memory/passages.py``). A memory's meaning is the better of its
vector and its best passage, and the block shows the stretch the passage
covers.

Held here through the product's writer and the real ``recall``, in English
and Arabic, with a concept embedder in which a question shares no word with
the answer it asks about (so keyword search cannot find it): with passages
the memory is found and its line shows the passage's stretch; with passages
off (today's recall) it is not found, and the line shows the answer's start
(the controls). A forget, a text change, a removal and a move to another
space take a memory's passages; an archive keeps them; passages written
after their memory changed are not stored; the repair fills old memories.
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

_FILLER = {
    "en": " ".join(f"Point {i}: plan the week and keep the list short." for i in range(60)),
    "ar": " ".join(f"النقطة {i}: خطط للأسبوع وخل القائمة قصيرة." for i in range(60)),
}
_ASKED = {
    "en": ("What should I make tonight?", "Make a red lentil soup with plenty of cumin, simmered slowly.",
           "What was that legume broth you suggested?", "lentil soup"),
    "ar": ("شنو أسوي الليلة؟", "سوِّ شوربة عدس حمرا مع كمون كثير، على نار هادية.",
           "شنو كان المرق اللي اقترحته بالبقوليات؟", "شوربة عدس"),
}
_OTHERS = {
    "en": [("Will it rain tomorrow?", "Light rain is expected after noon."),
           ("Remind me about the dentist.", "Booked for Monday at nine."),
           ("How was the match?", "The home side won two to one."),
           ("Which book next?", "Try the history of the silk road.")],
    "ar": [("بيمطر باجر؟", "متوقع مطر خفيف بعد الظهر."),
           ("ذكرني بموعد الدكتور.", "محجوز يوم الاثنين الساعة تسع."),
           ("شلون كانت المباراة؟", "فاز الفريق المضيف اثنين واحد."),
           ("أي كتاب بعدين؟", "جرب تاريخ طريق الحرير.")],
}


def _store(language: str) -> str:
    question, answer, _asked, _words = _ASKED[language]
    for i, (q, a) in enumerate(_OTHERS[language]):
        _write(q, a, session=f"other{i}")
    # The answer the question asks about lies deep in a long answer.
    return _write(question, f"{_FILLER[language]} {answer} {_FILLER[language]}", session="cook")


def _recall(language: str):
    from kazma_core.memory.recall import format_recall_block, recall

    result = recall(_ASKED[language][2], limit=5, tenant_id="default", local_only=True)
    return result, format_recall_block(result)


@pytest.mark.parametrize("language", ["en", "ar"])
def test_recall_finds_an_answer_only_its_passage_holds(mem, language) -> None:
    with passages.used():
        eid = _store(language)
        result, block = _recall(language)
    assert eid in [h.id for h in result.episodes]
    hit = next(h for h in result.episodes if h.id == eid)
    assert hit.metadata.get("passage", ("",))[0] == "answer"
    # W1b: the line shows the stretch the passage covers, deep in the answer.
    assert _ASKED[language][3] in block


@pytest.mark.parametrize("on", [True, False], ids=["passages", "control_off"])
@pytest.mark.parametrize("language", ["en", "ar"])
def test_the_postgres_primary_path_reads_the_passages_as_the_local_path_does(mem, monkeypatch, language,
                                                                             on) -> None:
    """A Postgres-primary install's recall takes the better of a memory's
    vector and its best passage too, from its own database: it injects what
    the local path injects, the answer only a passage holds among them (and,
    passages off, neither path finds it: the control)."""
    from kazma_core.memory import state_backend as sb
    from kazma_core.memory.backends import LocalSqliteVectorBackend
    from kazma_core.memory.recall import recall

    from tests.test_memory_primary_parity import _Mirror

    with passages.used(on):
        eid = _store(language)
        local, _block = _recall(language)
        monkeypatch.setattr(sb, "is_state_primary", lambda cfg=None: True)
        monkeypatch.setattr(sb, "get_state_backend", lambda: _Mirror(mem.db))
        monkeypatch.setattr("kazma_core.memory.backends.get_vector_backend",
                            lambda conn_=None, **_k: LocalSqliteVectorBackend(mem.db))
        primary = recall(_ASKED[language][2], limit=5, tenant_id="default")
    assert [h.id for h in primary.episodes] == [h.id for h in local.episodes]
    assert (eid in [h.id for h in primary.episodes]) is on
    if on:
        hit = next(h for h in primary.episodes if h.id == eid)
        assert hit.metadata.get("passage", ("",))[0] == "answer"


@pytest.mark.parametrize("language", ["en", "ar"])
def test_control_today_s_recall_does_not_find_it(mem, language) -> None:
    eid = _store(language)  # passages off: written and searched as before
    result, block = _recall(language)
    assert eid not in [h.id for h in result.episodes]
    assert _ASKED[language][3] not in block


@pytest.mark.parametrize("language", ["en", "ar"])
def test_control_without_the_anchor_the_line_shows_the_answers_start(mem, monkeypatch, language) -> None:
    from kazma_core.memory import recall as recall_mod

    real = recall_mod._rank_episodes_by_evidence

    def without_anchor(*args, **kwargs):
        ranked = real(*args, **kwargs)
        for hit in ranked:
            hit.metadata.pop("passage", None)
        return ranked

    monkeypatch.setattr(recall_mod, "_rank_episodes_by_evidence", without_anchor)
    with passages.used():
        eid = _store(language)
        result, block = _recall(language)
    assert eid in [h.id for h in result.episodes]
    assert _ASKED[language][3] not in block
