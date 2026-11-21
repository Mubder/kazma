"""Roll-ups on the retrieval benchmark (dataset v7, the memory framework
audit's F13, part G).

* The dataset's roll-ups are the ones the product plans: the planner, run on
  the seeded weekly summaries with their recorded vectors on the asking day,
  makes exactly these months and projects with exactly these parts. A
  dataset that lists roll-ups the product would not make measures nothing.
* The ``rollup`` category: a project's whole story, a month's talk, in
  English and Arabic. Negative control: the same questions on the store
  without roll-ups (dataset v6's memories) miss the month.
* A month's roll-up answers a question about its month, never a topic:
  "Where do I live?" is shown no month. Negative control: the month ranked
  as a topic, as first written, is shown for it (and for five more v6
  questions it does not answer). And "What did we talk about back in June?"
  names no topic, so it is answered by June's roll-up by the period it
  holds: ranked by meaning, as first written, June measured 0.08 against
  the floor of 0.21 and the question missed.
* v6 replays unchanged: ``dataset_as_of(v7, 6)`` holds no roll-up and no
  v7 question.
"""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory import benchmark  # noqa: E402

VECTORS = Path(__file__).resolve().parent / "fixtures" / "memory_bench" / "vectors.npz"


@pytest.fixture(scope="module")
def replay():
    embedder = benchmark.ReplayEmbedder(VECTORS)
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder), \
            mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: embedder.model):
        yield embedder


def test_the_datasets_roll_ups_are_the_ones_the_planner_makes(replay, tmp_path, monkeypatch):
    from kazma_core.memory import topic_rollups
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    data = benchmark.load_dataset()
    assert data["version"] == 7
    without = benchmark.dataset_as_of(data, 6)
    conn = sqlite3.connect(str(tmp_path / "bench.db"))
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    refs = benchmark._seed(conn, without)
    back = {v: k for k, v in refs.items()}
    monkeypatch.setattr(topic_rollups, "_PLANNED_PER_PASS", 100)
    monkeypatch.setattr(topic_rollups, "_month_of", _utc_month_of)
    asked_on = benchmark.BASE_TIME + 116 * 86400
    topic_rollups.plan_rollups(conn, now=asked_on)
    planned = {}
    for row in conn.execute("SELECT id, level, period_key FROM memory_summaries WHERE level != 'week'"):
        parts = sorted(back[p[0]] for p in conn.execute(
            "SELECT part_id FROM memory_summary_parts WHERE rollup_id = ?", (row["id"],)))
        planned[(row["level"], row["period_key"] if row["level"] == "month" else parts[0])] = parts
    conn.close()
    listed = {(r["level"], r["key"] if r["level"] == "month" else r["parts"][0]): r["parts"]
              for r in data["rollups"]}
    assert planned == listed


def _utc_month_of(ts):
    """The month in UTC: the benchmark reads its days in UTC."""
    from datetime import UTC, datetime

    day = datetime.fromtimestamp(float(ts), UTC)
    start = datetime(day.year, day.month, 1, tzinfo=UTC)
    end = datetime(day.year + (day.month == 12), day.month % 12 + 1, 1, tzinfo=UTC)
    return f"{day.year}-{day.month:02d}", start.timestamp(), end.timestamp()


def test_v6_replays_without_roll_ups():
    data = benchmark.load_dataset()
    v6 = benchmark.dataset_as_of(data, 6)
    assert v6["rollups"] == []
    assert not [q for q in v6["questions"] + v6["second"] if q["category"] == "rollup"]
    assert {r["new_in"] for r in data["rollups"]} == {7}


def _rollup_scores(data):
    report = benchmark.run_benchmark(data, keep_results=True)
    rows = [r for r in report["results"] if r["category"] == "rollup"]
    return {r["id"]: r["hit"] for r in rows}


@pytest.fixture(scope="module")
def with_and_without(replay):
    data = benchmark.load_dataset()
    rollup_questions = [q for q in data["questions"] if q["category"] == "rollup"]
    without = {**benchmark.dataset_as_of(data, 6), "questions": rollup_questions, "second": []}
    return (_rollup_scores({**data, "questions": rollup_questions, "second": []}),
            _rollup_scores(without))


def test_the_rollup_questions_are_answered_with_roll_ups(with_and_without):
    hits, _ = with_and_without
    assert hits == {"q1401": True, "q1402": True, "q1403": True, "q1404": True}


def test_control_without_roll_ups_the_months_talk_is_missed(with_and_without):
    """Negative control: on the store without roll-ups (v6's memories) no
    memory holds what a month was about."""
    _, without = with_and_without
    assert without["q1403"] is False and without["q1404"] is False


def _summaries_shown(question_ids, *, months_as_topics=False, no_period_rule=False):
    """Which summaries recall shows each question, on the v7 store."""
    from kazma_core.memory import recall
    from kazma_core.memory.question_shape import standalone_query
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    data = benchmark.load_dataset()
    asked = {q["id"]: q for q in data["questions"] + data["second"]}
    shown = {}
    with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch("kazma_core.memory.recall._memory_zone", _utc))
        if months_as_topics:
            stack.enter_context(mock.patch("kazma_core.memory.recall._overlaps", lambda stretch, ask: True))
        if no_period_rule:
            stack.enter_context(mock.patch("kazma_core.memory.recall._months_said_in", lambda *a, **k: []))
        conn = sqlite3.connect(str(Path(folder) / "bench.db"))
        try:
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            refs = benchmark._seed(conn, data)
            back = {v: k for k, v in refs.items()}
            for qid in question_ids:
                q = asked[qid]
                result = recall.recall(standalone_query(q["text"], q.get("context")), conn=conn,
                                       tenant_id=benchmark.BENCH_TENANT, limit=5, local_only=True,
                                       now=benchmark.BASE_TIME + q["now_day"] * 86400)
                shown[qid] = [back.get(h.id, h.id) for h in result.summaries]
        finally:
            conn.close()
    return shown


def _utc():
    from datetime import UTC

    return UTC


_UNRELATED = ("q401", "q404", "q016", "q902", "q1012")


def test_a_month_is_shown_only_for_a_question_about_it(replay):
    shown = _summaries_shown(_UNRELATED)
    assert not [s for qid in _UNRELATED for s in shown[qid] if s.startswith("rm_")], shown


def test_control_a_month_ranked_as_a_topic_is_shown_for_where_do_i_live(replay):
    shown = _summaries_shown(_UNRELATED, months_as_topics=True)
    assert all(any(s.startswith("rm_") for s in shown[qid]) for qid in _UNRELATED), shown


def test_a_question_about_a_months_talk_is_answered_by_its_roll_up(replay):
    assert _summaries_shown(("q1403", "q1404")) == {"q1403": ["rm_2026-06"], "q1404": ["rm_2026-06"]}


def test_control_by_meaning_alone_the_months_talk_is_missed(replay):
    shown = _summaries_shown(("q1403", "q1404"), no_period_rule=True)
    assert "rm_2026-06" not in shown["q1403"] and "rm_2026-06" not in shown["q1404"], shown
