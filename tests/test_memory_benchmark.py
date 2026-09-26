"""Recall quality, measured, and only allowed to improve (Stage 2, R7).

``kazma_core.memory.benchmark`` seeds a private database with the benchmark's
1,109 chat turns the way the product writes them, asks its questions through
the real ``recall()``, and scores each category. CI cannot load the real
embedder (bge-m3), so it replays the vectors bge-m3 returned for exactly
these texts (``tests/fixtures/memory_bench/vectors.npz``, recorded by
``python scripts/memory_bench.py vectors``): real semantics, no model.

The scores are a ratchet (``thresholds.json``): a drop fails, and so does an
improvement nobody locked in -- a gate that sits below what the code does
cannot see the next regression. The ratchet names the dataset version it was
measured on; a new dataset (v2 added the facts extraction stores) is locked
with ``lock --rebaseline``, which the script refuses on the same version.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory import benchmark  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"
THRESHOLDS = FIXTURES / "thresholds.json"
#: Scores are exact (recorded vectors, fixed clock); a change this small is noise.
SLACK = 0.0001


def _build_script():
    spec = importlib.util.spec_from_file_location("_memory_bench", REPO / "scripts" / "memory_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def report():
    replay = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    mp = pytest.MonkeyPatch()
    mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    mp.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    try:
        yield benchmark.run_benchmark(keep_results=True)
    finally:
        mp.undo()


def _compare(scores: dict[str, float], floor: dict[str, float]) -> tuple[list[str], list[str]]:
    dropped = [f"{k}: {scores.get(k)} < {v}" for k, v in floor.items()
               if scores.get(k, -1.0) < v - SLACK]
    rose = [f"{k}: {v} > {floor[k]}" for k, v in scores.items()
            if k in floor and v > floor[k] + SLACK]
    return dropped, rose


def test_the_dataset_is_what_the_builder_builds():
    """Nobody edits the JSON by hand: the builder is the source."""
    built = _build_script().build()
    assert benchmark.load_dataset() == json.loads(json.dumps(built, ensure_ascii=False))
    cats: dict[str, int] = {}
    for q in built["questions"]:
        cats[q["category"]] = cats.get(q["category"], 0) + 1
    assert all(n >= 3 for n in cats.values()), cats


def _floor() -> dict[str, float]:
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    version = benchmark.load_dataset()["version"]
    assert locked.get("dataset_version") == version, (
        f"thresholds.json measures dataset v{locked.get('dataset_version')}, the dataset is "
        f"v{version}: record its vectors, then python scripts/memory_bench.py lock --rebaseline"
    )
    return locked["scores"]


def test_recall_quality_only_improves(report):
    dropped, rose = _compare(benchmark.gated_scores(report), _floor())
    assert not dropped, "Recall got worse on the benchmark:\n  " + "\n  ".join(dropped)
    assert not rose, (
        "Recall improved -- lock it in: python scripts/memory_bench.py lock\n  "
        + "\n  ".join(rose)
    )


def test_the_gate_catches_a_recall_that_finds_nothing(monkeypatch, report):
    """Negative control: an empty recall passes abstention and fails the rest."""
    from kazma_core.memory.recall import RecallResult

    replay = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    monkeypatch.setattr("kazma_core.memory.recall.recall", lambda *a, **k: RecallResult([], []))
    broken = benchmark.run_benchmark()
    dropped, _ = _compare(benchmark.gated_scores(broken), _floor())
    assert broken["categories"]["abstain"]["hit_rate"] == 1.0
    assert any(d.startswith("single.hit_rate") for d in dropped), dropped


def test_every_question_was_scored(report):
    assert report["overall"]["questions"] == len(benchmark.load_dataset()["questions"])
    assert report["memories"] > 1000


def test_the_lock_never_lowers_the_ratchet(tmp_path, monkeypatch):
    """``lock`` refuses a lower score, and ``--rebaseline`` needs a new dataset."""
    script = _build_script()
    locked = tmp_path / "thresholds.json"
    version = benchmark.load_dataset()["version"]
    monkeypatch.setattr(script, "THRESHOLDS", locked)
    scores = {"single.hit_rate": 0.9, "abstain.hit_rate": 0.5}
    monkeypatch.setattr(benchmark, "gated_scores", lambda _report: dict(scores))
    monkeypatch.setattr(benchmark, "run_benchmark", lambda **_kw: {})

    def write(version_, values):
        locked.write_text(json.dumps({"dataset_version": version_, "scores": values}),
                          encoding="utf-8")

    write(version, {"single.hit_rate": 0.95, "abstain.hit_rate": 0.5})
    assert script._cmd_lock(rebaseline=False) == 1  # 0.9 < 0.95: refused
    assert json.loads(locked.read_text(encoding="utf-8"))["scores"]["single.hit_rate"] == 0.95
    assert script._cmd_lock(rebaseline=True) == 1  # same dataset: no rebaseline

    write(version, {"single.hit_rate": 0.8, "abstain.hit_rate": 0.5})  # control: a rise
    assert script._cmd_lock(rebaseline=False) == 0
    assert json.loads(locked.read_text(encoding="utf-8"))["scores"]["single.hit_rate"] == 0.9

    write(version - 1, {"single.hit_rate": 0.99})  # an older dataset's ratchet
    assert script._cmd_lock(rebaseline=False) == 1
    assert script._cmd_lock(rebaseline=True) == 0
    assert json.loads(locked.read_text(encoding="utf-8")) == {
        "dataset_version": version, "scores": scores}
