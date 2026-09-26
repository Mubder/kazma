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
    monkeypatch.setattr("kazma_core.memory.unified_index.upsert_unified",
                        lambda *_a, **_k: shared.append("unified"))
    eval_golden._seed_belief(
        conn, {"subject": "Platform Team", "predicate": "owns", "object": "Kazma Gateway"}, now=1.0
    )
    row = conn.execute("SELECT subject, predicate, object, valid_from FROM beliefs").fetchone()
    assert tuple(row) == ("platform_team", "owns", "Kazma Gateway", 1.0)
    assert shared == []
