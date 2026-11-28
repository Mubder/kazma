"""The scale bench (``scripts/memory_scale_speed.py``, the memory plan's W8).

A private store is filled with the bench's own insert and recall is timed.
The embedder here is a stub. Nothing of an install is opened, and the real
model is not loaded.

* 1x is the episode count in ``live_lengths.json``. 10x and 100x are that
  count times the factor, in the counter and in the database.
* The report carries p50, p95, model calls and tokens, in English and Arabic.
  A report with Arabic removed is the control.
* One slow recall is the p95. The same questions, all fast, are the control.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def scale():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import memory_scale_speed as mod
    finally:
        sys.path.pop(0)
    return mod


def test_one_x_is_the_published_episode_count(scale):
    """1x is the file's episode count. A second hardcoded number would drift."""
    lengths = json.loads((REPO / "tests" / "fixtures" / "memory_bench" / "live_lengths.json").read_text(
        encoding="utf-8"))
    assert scale.ONE_X_EPISODES == int(lengths["episodes"]) == 1723
    assert scale.store_counts(1)["episodes"] == 1723
    assert scale.store_counts(10)["episodes"] == 17230
    assert scale.store_counts(100)["episodes"] == 172300
    with pytest.raises(ValueError):
        scale.store_counts(2)


def test_the_question_set_is_both_languages(scale):
    assert scale.question_set_problems(scale.QUESTIONS) == []
    english = [q for q in scale.QUESTIONS if q["lang"] == "en"]
    assert scale.question_set_problems(english)  # the control: one language is refused


def test_sizes_are_built_as_declared_and_filler_stores_no_fact(scale, tmp_path):
    """The database holds the declared rows. A store with no preference planted
    holds no belief: the Zipf filler is the control."""
    embedder = scale.StubEmbedder()
    path = tmp_path / "ten.db"
    held = scale.build_store(path, embedder, factor=10, episodes=2, fact_every=2)
    declared = scale.store_counts(10, episodes=2, fact_every=2)
    conn = sqlite3.connect(path)
    try:
        stored = int(conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0])
    finally:
        conn.close()
    assert stored == held["episodes"] == declared["episodes"] == 20
    assert held["facts_en"] > 0
    assert held["facts_ar"] > 0
    assert held["facts"] == held["facts_en"] + held["facts_ar"]

    plain = scale.build_store(tmp_path / "plain.db", scale.StubEmbedder(), factor=1, episodes=4, fact_every=0)
    assert plain["episodes"] == 4
    assert plain["facts"] == 0
    assert plain["fact_turns"] == 0


def test_recall_reports_times_calls_and_both_languages(scale, tmp_path):
    """Recall's paid calls and tokens are counted, and both languages are timed.
    The embedder is the stub the recall itself is given."""
    questions = ({"lang": "en", "text": "Where do I live?"}, {"lang": "ar", "text": "وين أسكن؟"})
    embedder = scale.CountingEmbedder(scale.StubEmbedder())
    report = scale.measure((1,), embedder, episodes=4, fact_every=25, questions=questions)
    row = report["sizes"]["1"]
    assert row["episodes"] == 4
    assert row["model_calls"] == 0
    assert row["prompt_tokens"] == 0
    assert row["completion_tokens"] == 0
    assert row["local_embeds"] == len(questions)
    assert row["warmup_s"] >= 0.0
    assert "local_only" in report["tokens_what"]
    assert "write_episode" in report["fill_what"]
    assert "p50" in row["recall_s"] and "p95" in row["recall_s"]
    assert "p50" in row["by_lang"]["en"] and "p50" in row["by_lang"]["ar"]
    assert scale.report_problems(report, questions, factors=(1,), episodes=4, fact_every=25) == []

    broken = json.loads(json.dumps(report))
    del broken["sizes"]["1"]["by_lang"]["ar"]
    problems = scale.report_problems(broken, questions, factors=(1,), episodes=4, fact_every=25)
    assert any(p.endswith("missing ar") for p in problems)  # the control


def test_a_planted_slow_recall_shows_in_p95(scale):
    """p95 is the product measures' rank. Ten questions, one of them slow:
    that one is the p95. The same ten, all fast, stay fast: the control.
    The clock is injected, so the test does not sleep."""
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    slow = "the slow question"
    questions = [{"lang": "en" if i % 2 == 0 else "ar", "text": f"q{i}"} for i in range(9)]
    questions.append({"lang": "en", "text": slow})

    def recall(text: str, _conn: object) -> None:
        clock["t"] += 0.4 if text == slow else 0.001

    timed = scale.time_recalls(None, questions, recall_fn=recall, clock=now)
    assert timed["recall_s"]["n"] == 10
    assert timed["recall_s"]["p50"] == pytest.approx(0.001)
    assert timed["recall_s"]["p95"] == pytest.approx(0.4)
    assert timed["slowest_question"] == slow
    # The untimed recall is outside the set. It is not one of the ten, and it
    # is not the planted slow question, so it cannot move p95.
    assert scale.WARMUP_TEXT not in {q["text"] for q in questions}
    assert scale.WARMUP_TEXT not in {q["text"] for q in scale.QUESTIONS}
    assert timed["warmup_s"] == pytest.approx(0.001)

    clock["t"] = 0.0

    def fast(text: str, _conn: object) -> None:
        del text
        clock["t"] += 0.001

    control = scale.time_recalls(None, questions, recall_fn=fast, clock=now)
    assert control["recall_s"]["p95"] == pytest.approx(0.001)
    assert control["recall_s"]["p50"] == pytest.approx(0.001)
    assert control["warmup_s"] == pytest.approx(0.001)
    assert control["recall_s"]["n"] == 10


def test_a_dead_embedder_does_not_start_the_bench(scale):
    """An embedder that returns nothing must not be timed as if it had worked."""

    class _Dead:
        def encode_batch(self, texts: list[str]) -> list[list[float]]:
            return [[] for _ in texts]

    with pytest.raises(SystemExit):
        scale._warm(_Dead())


def test_only_100x_is_refused_at_two_hours(scale, tmp_path):
    """100x runs only when the fill estimate is under two hours. 10x is not
    gated on that number. The sample is the fill, not the recall."""
    del tmp_path
    assert scale.within_two_hours(7199.9, factor=100) is True
    assert scale.within_two_hours(7200, factor=100) is False
    assert scale.within_two_hours(8000, factor=10) is True
    over = {"sizes": {"100": {"fill_s": 8000.0}}}
    under = {"sizes": {"100": {"fill_s": 1000.0}}}
    assert scale.hundred_x_block(over)
    assert scale.hundred_x_block(under) is None

    sized = scale.estimate_fill(scale.StubEmbedder(), sample=2, episodes=2)
    assert sized["recall_included"] is False
    assert sized["sample_n"] == 2
    assert sized["sizes"]["100"]["episodes"] == 200
    assert sized["sizes"]["1"]["episodes"] == 2
    assert sized["sizes"]["100"]["fill_s"] == pytest.approx(sized["per_episode_s"] * 200)
    assert "write_episode" in sized["fill_what"]


def test_each_size_embeds_its_questions(scale, tmp_path):
    """Every factor encodes the question set.

    One wrapper kept across two recalls is the control: recall's
    question-vector cache is keyed by the embedder's identity, the set fits
    in its 64 entries, and the second recall embeds nothing.
    """
    questions = ({"lang": "en", "text": "Where do I live?"}, {"lang": "ar", "text": "وين أسكن؟"})
    path = tmp_path / "one.db"
    embedder = scale.CountingEmbedder(scale.StubEmbedder())
    scale.build_store(path, embedder, factor=1, episodes=2, fact_every=0)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        embedder.reset()
        first = scale.time_recalls(conn, questions, recall_fn=scale._product_recall, embedder=embedder)
        embedder.reset()
        second = scale.time_recalls(conn, questions, recall_fn=scale._product_recall, embedder=embedder)
    finally:
        conn.close()
    assert first["local_embeds"] == len(questions)
    assert second["local_embeds"] == 0

    report = scale.measure((1, 10), scale.StubEmbedder(), episodes=2, fact_every=0, questions=questions)
    for key in ("1", "10"):
        assert report["sizes"][key]["local_embeds"] == len(questions)
        assert report["sizes"][key]["warmup_s"] >= 0.0


def test_a_failed_priority_drop_is_not_called_below_normal(scale, monkeypatch, caplog, capsys):
    """A report says below normal only when the drop happened."""
    monkeypatch.setattr(scale, "_priority_lowered", lambda: True)
    assert scale._below_normal() is True
    assert scale._machine(True)["priority"] == "below normal"
    assert "could not lower priority" not in caplog.text

    monkeypatch.setattr(scale, "_priority_lowered", lambda: False)
    with caplog.at_level("WARNING"):
        assert scale._below_normal() is False
    assert scale._machine(False)["priority"] == "not lowered"
    assert "could not lower priority" in caplog.text

    def boom():
        raise OSError("nice")

    monkeypatch.setattr(scale, "_priority_lowered", boom)
    assert scale._below_normal() is False

    monkeypatch.setattr(scale, "_below_normal", lambda: False)
    monkeypatch.setattr(scale, "_private_data_folder", lambda: None)
    monkeypatch.setattr(scale, "_limit_threads", lambda: None)
    monkeypatch.setattr(scale, "_real_embedder", lambda: scale.StubEmbedder())
    monkeypatch.setattr(scale, "_warm", lambda _embedder: None)
    monkeypatch.setattr(scale, "measure", lambda *_a, **_k: {"sizes": {}})
    assert scale.main(["run", "--factors", "1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["machine"]["priority"] == "not lowered"
