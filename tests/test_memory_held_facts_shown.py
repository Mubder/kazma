"""The deep pass is shown the fact a turn changes (the memory plan's W6, step 1).

``held_facts`` compared two LISTS of words with ``<=``, which is alphabetical
order: "What should I cook tonight?" was shown Acme's facts, an Arabic turn
every English-named subject's, and "أختي نورة صارت جراحة" was not shown Noura's
job. And the speaker's newest thirty facts took every place, so an older fact
of theirs, or anyone else's, could not be replaced: the old value and the new
one both stayed current. Now the list is the facts the turn is about
(``memory.held_facts``).

``benchmark_held.run_held_pairs`` tells eight changes in English, Gulf and MSA
to stores with none and with forty newer facts, through what the memory worker
reads for a turn and the one write path, with bge-m3's recorded vectors
(``scripts/memory_bench.py held-pairs-vectors``).

Negative controls: today's comparison of lists, and the newest facts alone,
lose the pairs; so does each language's when its way of choosing is taken away.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from kazma_core.memory import benchmark, benchmark_held, held_facts
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.memory.limits import DEEP_PASS_HELD_FACTS, DEEP_PASS_HELD_TOKENS
from kazma_core.memory.schema_v2 import ensure_primary_schema
from kazma_core.summarizer import estimate_text_tokens

FIXTURE = Path(__file__).parent / "fixtures" / "memory_bench" / benchmark_held.HELD_PAIRS_VECTORS

def _run(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    replay = benchmark.ReplayEmbedder(FIXTURE)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    return benchmark_held.run_held_pairs()


def _same_language(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r["held_in"] == ("en" if r["dialect"] == "en" else "ar")]


def _lost(rows: list[dict]) -> list[tuple]:
    return [(r["pair"], r["dialect"], r["held_in"], r["newer"]) for r in rows if not (r["shown"] and r["closed"])]


def test_the_fact_a_turn_changes_is_shown_and_closed_in_each_dialect(monkeypatch):
    rows = _run(monkeypatch)
    same = _same_language(rows)
    assert {d: sum(r["dialect"] == d for r in same) for d in ("en", "gulf", "msa")} == {"en": 16, "gulf": 16, "msa": 16}
    assert _lost(same) == []
    crossed = [r for r in rows if r not in same]
    assert len(crossed) == 32 and _lost(crossed) == []
    # Nobody the turn is not about; in another alphabet than the turn's, two.
    assert [r for r in same if r["unrelated"]] == []
    assert max(len(r["unrelated"]) for r in crossed) <= 2
    assert max(r["places"] for r in rows) == DEEP_PASS_HELD_FACTS


def test_control_the_comparison_of_lists_and_the_newest_alone_lose_them(monkeypatch):
    """What was live: the named by alphabetical order, the newest first."""
    from kazma_core.memory.query_terms import content_terms

    def names(self, subject):
        wanted = content_terms(subject.replace("_", " "))
        return bool(wanted) and wanted <= content_terms(self._text)

    monkeypatch.setattr(held_facts._Said, "names", names)
    monkeypatch.setattr(held_facts, "_choose", lambda ways, places: [*ways["newest"], *ways["named"]][:places])
    rows = _run(monkeypatch)
    lost = _lost(_same_language(rows))
    for dialect in ("en", "gulf", "msa"):
        assert sum(1 for _p, d, _h, newer in lost if d == dialect and newer == 40) == 8, (dialect, lost)
    assert [r for r in rows if r["unrelated"]]


@pytest.mark.parametrize(("way", "dialects"), [("named", {"en", "gulf", "msa"}), ("near", {"en", "gulf", "msa"})])
def test_control_each_way_of_choosing_is_needed_in_each_dialect(monkeypatch, way, dialects):
    if way == "named":
        monkeypatch.setattr(held_facts._Said, "names", lambda *_a: False)
        # Near in meaning finds a named person too: without both, it is lost.
        monkeypatch.setattr(held_facts, "_NEAR_ANOTHER", 2.0)
    else:
        monkeypatch.setattr(held_facts, "_nearest", lambda *_a: [])
    assert {d for _p, d, _h, newer in _lost(_same_language(_run(monkeypatch))) if newer == 40} == dialects


def test_control_a_name_in_the_other_alphabet_is_found_only_near(monkeypatch):
    """"عمر نقل الدوحة" and ``omar``: no word of the turn names him."""
    monkeypatch.setattr(held_facts, "_OTHER_ALPHABET", 0)
    rows = _run(monkeypatch)
    assert {(pair, dialect) for pair, dialect, *_ in _lost(rows)} == {("friend", "gulf"), ("friend", "msa")}


@pytest.mark.parametrize(("subject", "text", "other"), [
    ("omar", "عمر نقل الدوحة", True), ("omar", "Omar moved", False), ("omar", "My café", False),
    ("عمر", "Omar moved", True), ("عمر", "عمر نقل", False), ("omar", "شريت iPhone حق عمر", True)])
def test_a_name_in_another_alphabet(subject, text, other):
    assert held_facts._in_another_alphabet(subject, text) is other


@pytest.mark.parametrize(("gone", "lost"), [
    (None, {"habit", "car"}), ("said", {"habit", "car", "allergy", "plan"}), ("one", {"habit", "car", "home"}),
    ("named", {"habit", "car", "job", "friend", "project"})])
def test_with_no_embedder_the_words_find_the_same_in_each_dialect(monkeypatch, gone, lost):
    """A habit and a car no word of the turn names need the meaning; every
    other pair is found by words, and each way of choosing by words is needed."""
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    choose = held_facts._choose
    if gone:
        monkeypatch.setattr(held_facts, "_choose", lambda ways, places: choose({**ways, gone: []}, places))
    rows = [r for r in _same_language(benchmark_held.run_held_pairs()) if r["newer"] == 40]
    assert {d: {p for p, dd, *_ in _lost(rows) if dd == d} for d in ("en", "gulf", "msa")} == dict.fromkeys(
        ("en", "gulf", "msa"), lost)


# ── Who a turn names ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("text", "subject", "named"), [
    ("What should I cook tonight?", "acme", False), ("What should I cook tonight?", "berlin_office", False),
    ("My sister Noura is a surgeon now.", "noura", True), ("Noura's job changed", "noura", True),
    ("How is the Berlin office?", "berlin_office", True), ("Berlin was cold", "berlin_office", False),
    ("My sister moved", "user_sister", True), ("I maxed out the card", "max", False), ("Max called", "max", True),
    ("Will called me", "will", True), ("I will call you", "will", False),
    ("شنو أطبخ اليوم؟", "acme", False), ("شنو أطبخ اليوم؟", "سارة", False),
    ("أختي نورة صارت جراحة", "نورة", True), ("قلت لنورة وسارة", "نورة", True), ("قلت لنورة وسارة", "سارة", True),
    ("رحت مع منصور أمس", "منصور", True), ("مشروع فالكون تأجل", "مشروع_فالكون", True),
    ("تأجل المشروع", "مشروع_فالكون", False), ("كلمت علي أمس", "علي", True), ("الكتاب على الطاولة", "علي", False),
])
def test_a_subject_is_named_by_its_words(text, subject, named):
    assert held_facts._Said(text).names(subject) is named


# ── The list's bounds ────────────────────────────────────────────────────


@pytest.fixture
def conn(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    yield db
    db.close()


def _hold(conn, subject, name, value, at, *, kind="set"):
    mutate_belief(conn, subject, name, value, predicate_type=kind, extraction_method="llm_inferred",
                  tenant_id="default", source_session="s", source_turn=1, now=at, private=True)


@pytest.mark.parametrize("word", ["word ", "كلمة "])
def test_the_list_is_bounded_in_tokens_in_both_languages(conn, word):
    for n in range(60):
        _hold(conn, "user", "noted", f"{n} {word * 60}", float(n))
    _hold(conn, "user", "likes", "tea", 0.5)
    held = held_facts.held_facts(conn, tenant_id="default", text="Do I like tea?")
    lines = "\n".join(held_facts.held_line(h) for h in held)
    assert estimate_text_tokens(lines) <= DEEP_PASS_HELD_TOKENS
    assert 5 < len(held) <= DEEP_PASS_HELD_FACTS and [h["ref"] for h in held] == [f"f{n + 1}" for n in range(len(held))]
    assert any(h["object"] == "tea" for h in held)  # what the turn says is kept first


def test_the_shares_are_the_places():
    assert sum(held_facts._SHARES.values()) == DEEP_PASS_HELD_FACTS


def test_one_person_with_many_facts_does_not_take_every_place(conn):
    for n in range(40):
        _hold(conn, "hana", "visited", f"place {n}", 100.0 + n)
        _hold(conn, "user", "visited", f"city {n}", 200.0 + n)
    _hold(conn, "omar", "works_as", "chef", 1.0)
    held = held_facts.held_facts(conn, tenant_id="default", text="Hana and Omar came over.")
    by = {s: sum(h["subject"] == s for h in held) for s in ("user", "hana", "omar")}
    assert by["omar"] == 1 and 0 < by["hana"] <= 10 and by["user"] >= 10 and len(held) == DEEP_PASS_HELD_FACTS


def test_what_was_just_told_is_shown_whatever_the_turn_says(conn):
    """"Sorry, I meant the other one" holds no word of the fact it puts right."""
    for n in range(50):
        _hold(conn, "user", "visited", f"city {n}", float(n))
    for said in ("Sorry, I meant the other one.", "آسف، قصدي الثانية"):
        held = held_facts.held_facts(conn, tenant_id="default", text=said)
        assert {f"city {n}" for n in range(40, 50)} <= {h["object"] for h in held}


def test_the_link_to_a_subjects_hub_is_not_a_fact_to_replace(conn):
    _hold(conn, "user", "related_to", "hana", 5.0)
    _hold(conn, "user", "likes", "tea", 1.0)
    held = held_facts.held_facts(conn, tenant_id="default", text="Hana called.")
    assert [h["predicate"] for h in held] == ["likes"]


def test_the_facts_are_named_from_what_the_model_reads(conn):
    """A name past the part of the turn the model is given names nobody."""
    from kazma_core.memory.limits import DEEP_PASS_QUESTION

    _hold(conn, "hana", "works_as", "pilot", 1.0)
    far = "x " * DEEP_PASS_QUESTION + "Hana is a captain now."
    assert held_facts.held_facts(conn, tenant_id="default", text=far) == []
    assert [h["subject"] for h in held_facts.held_facts(conn, tenant_id="default", text="Hi", answer="Hana called.")] \
        == ["hana"]


# ── The turn's stored vector ─────────────────────────────────────────────


def test_a_vector_of_another_model_is_not_compared(monkeypatch):
    from kazma_core.memory.embedder import serialize_f32_embedding
    from kazma_core.memory.worker_bootstrap import _stored_vector

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "m2")
    blob = serialize_f32_embedding([1.0, 0.5])
    assert _stored_vector({"embedding": blob, "embedding_model_version": "m2"}) == [1.0, 0.5]
    assert _stored_vector({"embedding": blob, "embedding_model_version": "m1"}) is None
    assert _stored_vector({"embedding": None, "embedding_model_version": "m2"}) is None


def test_a_vector_search_that_fails_is_said_and_the_rest_is_shown(conn, monkeypatch, caplog):
    _hold(conn, "user", "likes", "tea", 1.0)

    def broken(*_a, **_kw):
        raise sqlite3.OperationalError("no")

    monkeypatch.setattr("kazma_core.memory.vector_engine.VectorEngine.search_beliefs", broken)
    with caplog.at_level("WARNING", logger="kazma_core.memory.held_facts"):
        held = held_facts.held_facts(conn, tenant_id="default", text="hi", vector=[0.1, 0.2])
    assert [h["object"] for h in held] == ["tea"] and "could not be read" in caplog.text
