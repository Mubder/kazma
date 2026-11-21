"""The benchmark's unrelated chat says each turn once, and a store of four
times the pool is measured too (dataset v8, the benchmark audit's R9).

Until v7 the pool's 1,060 turns were 181 different ones, about six copies
each: recall's background (a question's meaning ranks 4 to 15) was made of
copies, and the thresholds were swept on it.

* Every turn of the pool, and of the scale run's pools, is said once, and
  none repeats a turn the questions are about. Negative control: v7's pool,
  still in the file for the older versions, fails the same check (1,060
  turns of 74 messages).
* v5 and v7 replay exactly: ``dataset_as_of`` gives each its own memories
  back (v7's fingerprint, taken from its own file).
* No pool turn says something about the person asking: the unanswerable
  questions ask exactly that. A planted turn is caught (the control).
* The scale run holds its lock (``thresholds.json`` ``scale``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory import benchmark  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"
#: ``benchmark.memory_fingerprint`` of dataset v7's own file
#: (``git show 5b8d029ab:kazma-core/kazma_core/memory/benchmark_data/dataset.json``).
V7_SHA256 = "9eff1b1b7291f63364b42b1c1c842e7637f07d69776ffa79de1242ed77d34441"


def _script():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import memory_bench
    finally:
        sys.path.pop(0)
    return memory_bench


def _pool_keys(sessions: list[dict]) -> list[str]:
    key = _script()._pool_key
    return [key(t["user"]) for s in sessions for t in s["turns"]]


def _said_twice(sessions: list[dict], others: list[dict]) -> int:
    keys = _pool_keys(sessions)
    return len(keys) - len(set(keys)) + len(set(keys) & set(_pool_keys(others)))


def test_every_turn_of_the_pool_is_said_once():
    data = benchmark.load_dataset()
    pool = [s for s in data["sessions"] if s["kind"] == "pool"] + data["scale"]["sessions"]
    others = [s for s in data["sessions"] if s["kind"] not in ("pool",)]
    assert len(pool) == _script().POOL_SESSIONS * data["scale"]["factor"]
    assert _said_twice(pool, others) == 0


def test_control_v7s_pool_says_its_turns_six_times():
    v7 = benchmark.dataset_as_of(benchmark.load_dataset(), 7)
    pool = [s for s in v7["sessions"] if s["kind"] == "distractor"]
    keys = _pool_keys(pool)
    assert (len(keys), len(set(keys))) == (1060, 74)
    assert _said_twice(pool, []) > 900


def test_v5_and_v7_replay_exactly():
    data = benchmark.load_dataset()
    assert benchmark.memory_fingerprint(data, as_of=7) == V7_SHA256
    held = data["ids_hold_since"]
    assert benchmark.memory_fingerprint(data, as_of=held["version"]) == held["sha256"]
    assert "scale" not in benchmark.dataset_as_of(data, 7)
    assert not [s for s in benchmark.dataset_as_of(data, 7)["sessions"] if s["kind"] == "pool"]


def test_no_pool_turn_says_something_about_the_person():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import memory_pool_generate as gen
    finally:
        sys.path.pop(0)
    data = benchmark.load_dataset()
    # The generated turns (v7's 74 hand-written ones, kept as they were,
    # were read by hand: "my colleague is visiting Tokyo" states nothing the
    # benchmark asks).
    generated = {t["user"] for t in json.loads((FIXTURES / "pool_turns.json").read_text(encoding="utf-8"))["turns"]}
    pool = [t for s in data["sessions"] if s["kind"] == "pool" for t in s["turns"] if t["user"] in generated]
    pool += [t for s in data["scale"]["sessions"] for t in s["turns"]]
    assert len(pool) > 4000
    names = gen.persona_words(json.loads(benchmark.DATASET_PATH.read_text(encoding="utf-8")))
    about = [t["user"] for t in pool if gen.keep(t, names=names) == "about the person"]
    assert about == []
    # Control: the check catches the person's own facts, in both languages.
    for planted in ("My dog is called Rex, what should I feed him?", "I live in Doha, is it hot?",
                    "زوجتي تحب القهوة، وش أشتري لها؟"):
        assert gen.keep({"user": planted, "assistant": "ok"}, names=names) == "about the person"


@pytest.fixture()
def replay(monkeypatch):
    rec = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: rec)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: rec.model)
    monkeypatch.setattr("kazma_core.memory.recall._recall_deadline_s", lambda: 300.0)
    return rec


def test_the_scale_run_holds_the_lock(replay):
    locked = json.loads((FIXTURES / "thresholds.json").read_text(encoding="utf-8"))["scale"]
    report = benchmark.run_scale_benchmark()
    assert report["factor"] == locked["factor"] and report["memories"] == locked["memories"]
    now = benchmark.scale_scores(report)
    below = {k: (v, now.get(k)) for k, v in locked["scores"].items() if now.get(k, -1.0) < v}
    assert below == {}
