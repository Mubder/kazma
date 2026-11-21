"""The golden memory cases, through the product's own golden eval (no LLM).

``kazma_core.memory.eval_golden.run_golden_eval`` is what the Dashboard runs:
it seeds each case the way the product writes memories (turns through
``dual_write.episode_row``, facts through ``mutate_belief``) into a private
database and asks ``recall()``. This file used to carry its own copy of the
seeding, which wrote facts as raw rows with no vector -- so the two drifted,
and the test measured something the product never did.

CI has no embedder: a case marked ``needs_meaning`` (its question shares no
word with its answer) is skipped there, with that reason, and must pass
where an embedder runs.

It is the Memory page's quick recall check (the benchmarks audit's F11 and
R10, 2026-11-21): every case runs among the fixture's twenty unrelated
memories and passes only when none of them is shown. A recall that shows
every memory whatever was asked fails every case; without the unrelated
memories (the eval as it was) it passed every one -- the negative control.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "memory_golden.json"


def _cases() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8")).get("cases") or []


@pytest.fixture()
def no_embedder(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)


def test_golden_set_passes_on_words_alone(no_embedder):
    from kazma_core.memory.eval_golden import run_golden_eval

    report = run_golden_eval()
    assert report["failed"] == 0, [c for c in report["cases"] if c["status"] == "fail"]
    meaning = {c["id"] for c in _cases() if c.get("needs_meaning") and not c.get("optional")}
    skipped = {c["id"] for c in report["cases"] if c.get("reason") == "needs an embedder"}
    assert meaning and meaning <= skipped
    assert report["passed"] >= 3 and report["pass_rate"] == 1.0


def test_a_meaning_case_is_never_judged_on_words(monkeypatch):
    from kazma_core.memory.eval_golden import _needs_meaning_unavailable

    case = next(c for c in _cases() if c.get("needs_meaning"))

    class _Encodes:
        def encode(self, text):
            return [1.0, 0.0]

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: None)
    assert _needs_meaning_unavailable(case)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Encodes())
    assert not _needs_meaning_unavailable(case)  # an embedder: the case runs
    assert not _needs_meaning_unavailable({"id": "words", "query": "x"})


def test_golden_facts_are_stored_the_way_extraction_stores_them(no_embedder, monkeypatch):
    """Slugged subjects, and nothing sent past the eval's own database."""
    import sqlite3

    from kazma_core.memory import eval_golden
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    shared: list[str] = []
    monkeypatch.setattr("kazma_core.memory.state_backend.remirror_belief_by_id",
                        lambda *_a, **_k: shared.append("mirror"))
    eval_golden._seed_belief(
        conn, {"subject": "Platform Team", "predicate": "owns", "object": "Kazma Gateway"}, now=1.0
    )
    row = conn.execute("SELECT subject, predicate, object, valid_from FROM beliefs").fetchone()
    assert tuple(row) == ("platform_team", "owns", "Kazma Gateway", 1.0)
    assert shared == []


def _show_everything(query, *, conn, **_kw):
    """A recall that shows every memory, whatever was asked."""
    from kazma_core.memory.recall import RecallHit, RecallResult

    episodes = [RecallHit(id=str(r[0]), content=str(r[1] or ""), score=1.0)
                for r in conn.execute("SELECT id, user_text FROM episodes")]
    beliefs = [RecallHit(id=str(r[0]), content=f"{r[1]} {r[2]} {r[3]}", score=1.0, kind="belief")
               for r in conn.execute("SELECT id, subject, predicate, object FROM beliefs")]
    return RecallResult(beliefs=beliefs, episodes=episodes)


def test_every_case_runs_among_twenty_unrelated_memories(no_embedder):
    from kazma_core.memory.eval_golden import _distractors, run_golden_eval

    distractors = _distractors()
    assert len(distractors) == 20 and all(d.get("marker") for d in distractors)
    assert len({d["marker"] for d in distractors}) == 20
    report = run_golden_eval()
    assert report["unrelated_per_case"] == 20
    ran = [c for c in report["cases"] if c["status"] != "skipped"]
    assert ran and all(c["shown_wrongly"] == [] for c in ran)


def test_a_recall_that_shows_everything_fails_every_case(no_embedder, monkeypatch):
    from kazma_core.memory import eval_golden

    monkeypatch.setattr("kazma_core.memory.recall.recall", _show_everything)
    report = eval_golden.run_golden_eval()
    assert report["passed"] == 0 and report["failed"] >= 3
    assert all(c["found"] and c["shown_wrongly"] for c in report["cases"] if c["status"] == "fail")


def test_control_without_the_unrelated_memories_showing_everything_passes(no_embedder, monkeypatch):
    """The eval as it was (each case alone): the same recall passes every case."""
    from kazma_core.memory import eval_golden

    monkeypatch.setattr("kazma_core.memory.recall.recall", _show_everything)
    monkeypatch.setattr(eval_golden, "_distractors", lambda: [])
    report = eval_golden.run_golden_eval()
    assert report["failed"] == 0 and report["passed"] >= 3
