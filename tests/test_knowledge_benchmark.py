"""Knowledge Library retrieval quality, measured, and only allowed to improve (KQ01).

``kazma_core.stores.knowledge_benchmark`` publishes four documentation
libraries (English, Arabic, and a runbook in both) into a private store the
way an ingest does, asks every question through the tool's search and the
turn's auto-inject, and scores each category. CI cannot load the embedder
(bge-m3), so it replays the vectors bge-m3 returned for exactly these texts
(``tests/fixtures/knowledge_bench/vectors.npz``, recorded by ``python
scripts/knowledge_bench.py vectors``); the vectors are served by an exact
in-process client (the real chromadb gave the same scores, ``run --chroma``).

The scores are a ratchet (``thresholds.json``): a drop fails, and so does an
improvement nobody locked in. The ratchet names the dataset version it was
measured on; a new version is locked with ``lock --rebaseline``, which the
script refuses on the same version.

Until KQ01 both paths showed something for every question: the tool five
hits and the auto-inject three, for questions nothing in the libraries
answers (abstention 0.00). The negative controls put the old ranking back.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory.benchmark import ReplayEmbedder  # noqa: E402
from kazma_core.stores import knowledge_benchmark as bench  # noqa: E402
from kazma_core.stores import knowledge_index as ki  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "knowledge_bench"
THRESHOLDS = FIXTURES / "thresholds.json"
#: Scores are exact (recorded vectors, exact client); a change this small is noise.
SLACK = 0.0001


def _script():
    spec = importlib.util.spec_from_file_location("_knowledge_bench", REPO / "scripts" / "knowledge_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _replay() -> ReplayEmbedder:
    return ReplayEmbedder(FIXTURES / "vectors.npz")


@pytest.fixture(scope="module")
def report():
    return bench.run_profiles(embedder=_replay(), keep_results=True)


def _floor() -> dict[str, float]:
    locked = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    version = bench.load_dataset()["version"]
    assert locked.get("dataset_version") == version, (
        f"thresholds.json measures dataset v{locked.get('dataset_version')}, the dataset is "
        f"v{version}: record its vectors, then python scripts/knowledge_bench.py lock --rebaseline"
    )
    return locked["scores"]


def _compare(scores: dict[str, float], floor: dict[str, float]) -> tuple[list[str], list[str]]:
    dropped = [f"{k}: {scores.get(k)} < {v}" for k, v in floor.items() if scores.get(k, -1.0) < v - SLACK]
    rose = [f"{k}: {v} > {floor[k]}" for k, v in scores.items() if k in floor and v > floor[k] + SLACK]
    return dropped, rose


def test_the_dataset_is_what_the_builder_builds():
    """Nobody edits the JSON by hand: the builder is the source."""
    built = _script().build()
    assert bench.load_dataset() == json.loads(json.dumps(built, ensure_ascii=False))
    cats: dict[str, int] = {}
    for q in built["questions"]:
        cats[q["category"]] = cats.get(q["category"], 0) + 1
    assert all(n >= 4 for n in cats.values()), cats
    assert {"abstain", "abstain_ar", "arabic", "mixed", "cross", "paraphrase"} <= set(cats)


def test_retrieval_quality_only_improves(report):
    dropped, rose = _compare(bench.gated_scores(report), _floor())
    assert not dropped, "Knowledge retrieval got worse on the benchmark:\n  " + "\n  ".join(dropped)
    assert not rose, ("Knowledge retrieval improved -- lock it in: python scripts/knowledge_bench.py lock\n  "
                      + "\n  ".join(rose))


def test_both_paths_and_both_profiles_are_measured(report):
    data = bench.load_dataset()
    for profile in ("hybrid", "keyword"):
        part = report["profiles"][profile]
        assert part["chunks"] == sum(len(p["sections"]) for lib in data["libraries"] for p in lib["pages"])
        for path in ("explicit", "inject"):
            assert part[path]["overall"]["questions"] == len(data["questions"])
            assert part["heldout"][path]["overall"]["questions"] == len(data["heldout"])
    assert report["profiles"]["hybrid"]["meaning"] and not report["profiles"]["keyword"]["meaning"]


def test_unanswerable_questions_are_mostly_left_empty(report):
    """What the floor buys, in plain numbers: half of the questions nothing
    answers get nothing on both paths, and an answer is still found for 19
    in 20 of the others."""
    for path in ("explicit", "inject"):
        part = report["profiles"]["hybrid"][path]
        assert part["overall"]["abstention"] >= 0.5
        answered = [r for r in part["results"] if "rr" in r]
        assert sum(r["hit"] for r in answered) / len(answered) >= 0.94


def test_the_gate_catches_the_fusion_without_its_floor(monkeypatch):
    """Negative control: the ranking before KQ01 -- the fused candidates in
    fusion order, nothing judged -- fails the gate on abstention, on both
    paths."""
    monkeypatch.setattr(ki, "_judge", lambda query, candidates, sims, dense: candidates)
    old = bench.run_profiles(embedder=_replay())
    dropped, _ = _compare(bench.gated_scores(old), _floor())
    for path in ("explicit", "inject"):
        assert any(d.startswith(f"hybrid.{path}.overall.abstention") for d in dropped), dropped
        assert old["profiles"]["hybrid"][path]["overall"]["abstention"] == 0.0


def test_the_gate_catches_a_ranker_that_finds_nothing(monkeypatch):
    """Negative control: a search that shows nothing passes abstention and
    fails the rest."""
    monkeypatch.setattr(ki, "_judge", lambda query, candidates, sims, dense: [])
    broken = bench.run_profiles(embedder=_replay())
    dropped, _ = _compare(bench.gated_scores(broken), _floor())
    assert broken["profiles"]["hybrid"]["explicit"]["overall"]["abstention"] == 1.0
    assert any(d.startswith("hybrid.explicit.exact.hit_rate") for d in dropped), dropped
    assert any(d.startswith("hybrid.inject.arabic.hit_rate") for d in dropped), dropped


def test_the_lock_never_lowers_the_ratchet(tmp_path, monkeypatch):
    """``lock`` refuses a lower score, and ``--rebaseline`` needs a new dataset."""
    script = _script()
    locked = tmp_path / "thresholds.json"
    version = bench.load_dataset()["version"]
    monkeypatch.setattr(script, "THRESHOLDS", locked)
    monkeypatch.setattr(script, "_replay", lambda: None)
    scores = {"hybrid.explicit.exact.hit_rate": 0.9, "hybrid.inject.overall.abstention": 0.5}
    monkeypatch.setattr(bench, "gated_scores", lambda _report: dict(scores))
    monkeypatch.setattr(bench, "run_profiles", lambda **_kw: {})

    def write(version_, values):
        locked.write_text(json.dumps({"dataset_version": version_, "scores": values}), encoding="utf-8")

    write(version, {"hybrid.explicit.exact.hit_rate": 0.95, "hybrid.inject.overall.abstention": 0.5})
    assert script._cmd_lock(rebaseline=False) == 1  # 0.9 < 0.95: refused
    assert json.loads(locked.read_text(encoding="utf-8"))["scores"]["hybrid.explicit.exact.hit_rate"] == 0.95
    assert script._cmd_lock(rebaseline=True) == 1  # same dataset: no rebaseline

    write(version, {"hybrid.explicit.exact.hit_rate": 0.8, "hybrid.inject.overall.abstention": 0.5})
    assert script._cmd_lock(rebaseline=False) == 0  # control: a rise is written
    assert json.loads(locked.read_text(encoding="utf-8"))["scores"]["hybrid.explicit.exact.hit_rate"] == 0.9

    write(version - 1, {"hybrid.explicit.exact.hit_rate": 0.99})  # an older dataset's ratchet
    assert script._cmd_lock(rebaseline=False) == 1
    assert script._cmd_lock(rebaseline=True) == 0
    assert json.loads(locked.read_text(encoding="utf-8")) == {"dataset_version": version, "scores": scores}


def test_the_builder_refuses_a_held_out_question_that_repeats_a_tuning_one(monkeypatch):
    script = _script()
    first = script.QUESTIONS[0]
    monkeypatch.setattr(script, "HELDOUT", [*script.HELDOUT, ("h999", first[1], first[2], first[3], [])])
    with pytest.raises(ValueError, match="repeats a tuning question"):
        script.build()


def test_the_benchmark_never_writes_vectors_outside_its_folder(monkeypatch, tmp_path):
    """``KAZMA_VECTOR_PATH`` sends a store's vectors to the install's folder:
    the benchmark refuses rather than write there."""
    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    with pytest.raises(RuntimeError, match="KAZMA_VECTOR_PATH"):
        bench.run_benchmark(embedder=None)
    assert not (tmp_path / "vector_memory").exists()


def test_a_directory_client_serves_only_its_folder_and_never_a_served_one(tmp_path):
    from kazma_core.memory import vector_store_global as vsg

    folder = str(tmp_path / "vectors")
    client = bench._ExactVectorClient()
    with vsg.directory_client(folder, client):
        assert vsg._shared_client(folder) is client
        with pytest.raises(RuntimeError, match="already serves"), \
                vsg.directory_client(folder, bench._ExactVectorClient()):
            pass
        assert vsg._shared_client(folder) is client  # the refusal took nothing over
    assert vsg._client_key(folder) not in vsg._CLIENTS
