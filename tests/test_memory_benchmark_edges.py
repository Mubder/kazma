"""The edge rule (benchmark audit R4; the owner's decision 3, 2026-11-18).

The recorded vectors keep one byte a number, so the model's real vector is
anywhere within half a rounding step of the recorded one, and another
machine's model differs from it by about that much. Question q601 ("what is
it that I do for a living?") clears recall's floor by 0.0004: its hit holds
on this recording only, and the audit saw it flip in 7 of 30 draws inside
the rounding.

``scripts/memory_bench.py lock`` replays the benchmark with the vectors moved
inside their rounding (``EDGE_DRAWS`` seeded draws) and refuses when a hit
flips, unless the question is on the edge list with its margin named
(``lock --edge QUESTION "why"``): the only exit. These tests hold it:

* CI asks the draws again for the questions near the cut, so a change that
  brings one to the edge without moving a score is seen; the list names
  exactly the questions that flip, each with its margin as measured;
* the negative control: today's dataset without q601 on the list stops the
  lock, naming q601 and its margin;
* the list's rules: a question that no longer flips leaves it, one that does
  not flip cannot be named, a new dataset starts the list again.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from kazma_core.memory import benchmark

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"
VECTORS = FIXTURES / "vectors.npz"
THRESHOLDS = FIXTURES / "thresholds.json"
SLACK = 0.0001


def _script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_memory_bench_edges", REPO / "scripts" / "memory_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def report():
    replay = benchmark.ReplayEmbedder(VECTORS)
    mp = pytest.MonkeyPatch()
    mp.setattr("kazma_core.memory.embedder.get_embedder", lambda: replay)
    mp.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    try:
        yield benchmark.run_benchmark(keep_results=True)
    finally:
        mp.undo()


def _results(report: dict) -> dict[str, dict]:
    return {r["id"]: r for r in report["results"] + report["second"]["results"]}


@pytest.fixture(scope="module")
def near_edge(report):
    """The lock's own draws, for the questions within EDGE_WATCH of the cut."""
    watch = {qid for qid, r in _results(report).items()
             if r["margin"] is not None and abs(r["margin"]) < benchmark.EDGE_WATCH}
    return benchmark.edge_check(VECTORS, report, only=watch)


def _locked_edges() -> dict:
    return json.loads(THRESHOLDS.read_text(encoding="utf-8"))["edges"]


def _list_problems(locked: dict, check: dict, results: dict[str, dict]) -> list[str]:
    """Why the lock's edge list is not what the draws find (empty when it is)."""
    problems = []
    named = {h["id"]: h for h in locked.get("hits", [])}
    for qid in sorted(set(check["flips"]) - set(named)):
        problems.append(f"{qid} flips inside the rounding and is not on the edge list: "
                        f"python scripts/memory_bench.py lock (it will name it, or refuse)")
    for qid in sorted(set(named) - set(check["flips"])):
        problems.append(f"{qid} is on the edge list and no longer flips: python scripts/memory_bench.py lock")
    for qid, hit in sorted(named.items()):
        r = results.get(qid)
        if r is None:
            problems.append(f"{qid} is on the edge list and is not a question of the dataset")
            continue
        if r["margin"] is None or abs(r["margin"] - hit["margin"]) > SLACK:
            problems.append(f"{qid}: the list names margin {hit['margin']}, the replay measures {r['margin']}")
        if bool(r["hit"]) != bool(hit["hit"]):
            problems.append(f"{qid}: the list says the hit {'holds' if hit['hit'] else 'misses'}, the replay "
                            f"says otherwise")
        if not str(hit.get("why") or "").strip():
            problems.append(f"{qid}: on the edge list with no reason")
    return problems


# ── CI: the list is what the draws find ───────────────────────────────────


def test_the_lock_names_its_draws():
    locked = _locked_edges()
    assert (locked["draws"], locked["seed"], locked["move"]) == (
        benchmark.EDGE_DRAWS, benchmark.EDGE_SEED, benchmark.EDGE_MOVE)
    assert benchmark.EDGE_DRAWS >= 10
    # The watch covers what a draw can move with room to spare.
    assert benchmark.EDGE_WATCH >= 3 * locked["largest_move"], (
        f"a lock measured margins moving by {locked['largest_move']}: raise EDGE_WATCH")


def test_the_edge_list_is_what_the_draws_find(report, near_edge):
    problems = _list_problems(_locked_edges(), near_edge, _results(report))
    assert not problems, "\n  ".join(["The edge list is not what the draws find:", *problems])


def test_q601_stands_on_the_edge(report, near_edge):
    """The audit's witness, kept until R6's dataset version measures it again."""
    r = _results(report)["q601"]
    assert r["hit"] and 0 < r["margin"] < 0.001
    assert "q601" in near_edge["flips"]


def test_the_list_check_sees_an_unnamed_flip_and_a_wrong_margin(report, near_edge):
    """Negative control for the CI check: a lock without q601, and one that
    names another margin, are each refused."""
    locked = _locked_edges()
    results = _results(report)
    without = {**locked, "hits": [h for h in locked["hits"] if h["id"] != "q601"]}
    assert any(p.startswith("q601 flips") for p in _list_problems(without, near_edge, results))
    moved = {**locked, "hits": [{**h, "margin": round(h["margin"] + 0.01, 4)} for h in locked["hits"]]}
    assert any("names margin" in p for p in _list_problems(moved, near_edge, results))
    stale = {**locked, "hits": [*locked["hits"], {"id": "q001", "hit": True, "margin": 0.5, "why": "x"}]}
    assert any(p.startswith("q001 is on the edge list") for p in _list_problems(stale, near_edge, results))


def test_every_margin_agrees_with_its_hit(report):
    """The margin is the distance to the cut recall applied: positive exactly
    when the hit holds. Not for dataset v6's ``list`` questions: their
    answers are cut by how many are shown (nine long runs; a list shows at
    most six weekly summaries), which the margin does not count, and shown
    as the siblings of the best fact, on its evidence, not their own. None
    only for an answer that was never a candidate (v6's ``dense``
    paraphrases, outside the nearest memories by meaning): a miss, far from
    any edge."""
    for qid, r in _results(report).items():
        if r["category"] == "list":
            continue
        if r["margin"] is None:
            assert not r["hit"], qid
            continue
        assert (r["margin"] >= 0) == bool(r["hit"]), (qid, r["margin"], r["hit"])


# ── The lock refuses ──────────────────────────────────────────────────────


@pytest.fixture
def lock_copy(tmp_path, monkeypatch, report, near_edge):
    """The script, writing a copy of the lock, on the replay and its draws."""
    script = _script()
    path = tmp_path / "thresholds.json"
    path.write_text(THRESHOLDS.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(script, "THRESHOLDS", path)
    real_run = benchmark.run_benchmark
    monkeypatch.setattr(benchmark, "run_benchmark",
                        lambda dataset=None, **kw: report if dataset is None else real_run(dataset, **kw))
    monkeypatch.setattr(benchmark, "edge_check", lambda *_a, **_kw: near_edge)
    return script, path


def test_a_hit_at_the_edge_stops_the_lock(lock_copy, capsys):
    """Negative control: today's dataset, q601 not on the list, is refused,
    with the question and its margin named; nothing is written."""
    script, path = lock_copy
    data = json.loads(path.read_text(encoding="utf-8"))
    data["edges"] = {**data["edges"], "hits": []}
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_text(encoding="utf-8")
    assert script._cmd_lock(rebaseline=False) == 1
    err = capsys.readouterr().err
    assert "q601" in err and "0.0004" in err and "lock --edge q601" in err
    assert "never move a threshold" in err
    assert path.read_text(encoding="utf-8") == before


def test_naming_it_is_the_exit_and_it_is_recorded(lock_copy):
    script, path = lock_copy
    data = json.loads(path.read_text(encoding="utf-8"))
    data["edges"] = {**data["edges"], "hits": []}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert script._cmd_lock(rebaseline=False, edges=[("q601", "the audit's witness")]) == 0
    hit = {h["id"]: h for h in json.loads(path.read_text(encoding="utf-8"))["edges"]["hits"]}["q601"]
    assert hit["margin"] == pytest.approx(0.0004, abs=SLACK)
    assert hit["why"] == "the audit's witness" and hit["flipped"] >= 1 and hit["since"]


# ── The list's rules ──────────────────────────────────────────────────────


def _check(flips: dict[str, int]) -> dict:
    return {"draws": benchmark.EDGE_DRAWS, "seed": benchmark.EDGE_SEED, "move": benchmark.EDGE_MOVE,
            "largest_move": 0.002, "flips": flips, "shown": {}}


def test_the_list_follows_the_draws(monkeypatch, report):
    script = _script()
    old = {"hits": [{"id": "q601", "margin": 0.0004, "hit": True, "why": "kept", "since": "2026-11-20"},
                    {"id": "q703", "margin": 0.0065, "hit": True, "why": "was", "since": "2026-11-01"}]}

    monkeypatch.setattr(benchmark, "edge_check", lambda *_a, **_kw: _check({"q601": 2}))
    section, problems, settled = script._edge_lock(report, old, fresh=False, name=None)
    assert not problems
    assert [h["id"] for h in section["hits"]] == ["q601"]
    assert section["hits"][0]["since"] == "2026-11-20" and section["hits"][0]["flipped"] == 2
    assert settled == ["q703"]  # no longer flips: off the list

    # A question that does not flip cannot be named.
    _s, problems, _ = script._edge_lock(report, old, fresh=False, name=[("q703", "why")])
    assert any(p.startswith("--edge q703") for p in problems)

    # A new dataset version starts the list again: q601 must be named anew.
    _s, problems, _ = script._edge_lock(report, old, fresh=True, name=None)
    assert any(p.startswith("q601 ") for p in problems)


# ── The moved replay ──────────────────────────────────────────────────────


def test_a_draw_moves_each_number_inside_its_rounding():
    replay = benchmark.ReplayEmbedder(VECTORS)
    moved = benchmark._MovedEmbedder(VECTORS, benchmark.EDGE_SEED)
    again = benchmark._MovedEmbedder(VECTORS, benchmark.EDGE_SEED)
    other = benchmark._MovedEmbedder(VECTORS, benchmark.EDGE_SEED + 1)
    text = benchmark.load_dataset()["questions"][0]["text"]
    i = replay._index[benchmark._text_key(text)]
    steps = (np.asarray(moved.encode(text)) - np.asarray(replay.encode(text))) / replay._scale[i]
    assert 0 < np.abs(steps).max() <= benchmark.EDGE_MOVE + 1e-4
    assert moved.encode(text) == again.encode(text)
    assert moved.encode(text) != other.encode(text)


def test_a_float_recording_has_no_rounding_to_move_inside(tmp_path):
    path = tmp_path / "vectors.npz"
    np.savez_compressed(path, keys=np.asarray(["k"]), vecs=np.ones((1, 4), dtype=np.float32),
                        scale=np.ones(1, dtype=np.float32), model=np.asarray("m"))
    with pytest.raises(ValueError, match="measure its spread"):
        benchmark._MovedEmbedder(path, benchmark.EDGE_SEED)
