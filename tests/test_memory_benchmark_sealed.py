"""The sealed question set (benchmark audit R8; the owner's decision 4).

A set of recall questions written by a session that does not build or tune
recall (``docs/plans/MEMORY_SEALED_SET.md``), kept out of the per-commit gate:

* CI checks the file's shape only (``sealed_problems``), never its scores;
* ``scripts/memory_bench.py sealed --look "why"`` is the only way it is
  scored, and every look is logged in the lock with its date, reason, commit,
  the file's hash and the scores;
* a look that decides a change (``--decided``) spends the set: the next look
  is refused until the questions are new.

The questions themselves are another session's to write; these tests run the
rules on a planted set built from the dataset's own questions.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import numpy as np  # noqa: F401 -- the recorded vectors need it
import pytest
from kazma_core.memory import benchmark

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"
SEALED = FIXTURES / "sealed_questions.json"
THRESHOLDS = FIXTURES / "thresholds.json"


def _script():
    spec = importlib.util.spec_from_file_location("_memory_bench_sealed", REPO / "scripts" / "memory_bench.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# ── CI: the shape, never the scores ───────────────────────────────────────


def test_the_sealed_file_has_the_specified_shape():
    """The file another session writes; until it exists nobody can have
    looked at it."""
    looks = (json.loads(THRESHOLDS.read_text(encoding="utf-8")).get("sealed") or {}).get("looks") or []
    if not SEALED.is_file():
        assert (REPO / "docs" / "plans" / "MEMORY_SEALED_SET.md").is_file()
        assert not looks, "the lock logs a look at a sealed set that is not there"
        return
    problems = benchmark.sealed_problems(json.loads(SEALED.read_text(encoding="utf-8")), benchmark.load_dataset())
    assert not problems, "The sealed set is not the one specified:\n  " + "\n  ".join(problems)


#: The functions that may score the sealed set: the look command alone.
_SCORERS = {"run_benchmark", "_cmd_sealed", "sealed_dataset"}


def _scoring_readers(sources: dict[str, str]) -> list[str]:
    """Code outside the look command that names the sealed file and scores
    something: the per-commit gate must never score the sealed set."""
    found = []
    for rel, text in sources.items():
        if "sealed_questions.json" not in text and "SEALED" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if rel.endswith("scripts/memory_bench.py") and node.name in ("_cmd_sealed", "_sealed_vectors"):
                continue
            body = ast.unparse(node)
            if ("SEALED" in body or "sealed_questions.json" in body) and any(
                    isinstance(c, ast.Call) and getattr(c.func, "attr", getattr(c.func, "id", "")) in _SCORERS
                    for c in ast.walk(node)):
                if rel.endswith("tests/test_memory_benchmark_sealed.py") and node.name.startswith("test_a_look"):
                    continue  # scores a planted file at a temporary path
                found.append(f"{rel}::{node.name}")
    return found


def test_nothing_in_the_gate_scores_the_sealed_set():
    sources = {}
    for root in (REPO / "tests", REPO / "scripts"):
        for path in root.rglob("*.py"):
            if "__pycache__" not in path.parts:
                sources[path.relative_to(REPO).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    assert not _scoring_readers(sources), _scoring_readers(sources)


def test_the_gate_sees_a_test_that_scores_the_sealed_set():
    """Negative control: a test that runs the benchmark on the sealed file."""
    planted = {"tests/test_planted.py": (
        "from kazma_core.memory import benchmark\n"
        "SEALED = 'tests/fixtures/memory_bench/sealed_questions.json'\n"
        "def test_peek():\n"
        "    benchmark.run_benchmark(benchmark.sealed_dataset(json.load(open(SEALED))))\n")}
    assert _scoring_readers(planted) == ["tests/test_planted.py::test_peek"]


# ── The shape check ───────────────────────────────────────────────────────


def _planted() -> tuple[dict, dict[str, str]]:
    """A set of the specified shape, from the dataset's own questions (text
    changed, gold kept), and its texts mapped to the recorded ones."""
    data = benchmark.load_dataset()
    answerable = [q for q in data["questions"] + data["second"] if q["gold"]]
    questions, source = [], {}
    n = 0
    for category in benchmark.SEALED_CATEGORIES:
        for i in range(benchmark.SEALED_PER_CATEGORY):
            n += 1
            base = answerable[n % len(answerable)]
            text = f"{base['text']} (planted {category} {i})"
            source[text] = base["text"]
            q = {"id": f"s{n:03d}", "category": category, "lang": "en", "text": text,
                 "gold": [] if category == "abstain" else list(base["gold"]), "below": [], "why": "planted"}
            if category == "arabic":
                q.update(lang="ar", dialect="gulf" if i % 2 else "msa")
            elif category == "mixed":
                q["lang"] = "mixed"
            elif category != "keyword" and i < 2:
                q.update(lang="ar", dialect="gulf")
            questions.append(q)
    sealed = {"version": 1, "dataset_version": data["version"], "written_by": "a test",
              "written_on": "2026-11-20", "note": "planted", "questions": questions}
    return sealed, source


def test_a_set_of_the_specified_shape_passes():
    sealed, _ = _planted()
    assert benchmark.sealed_problems(sealed, benchmark.load_dataset()) == []


@pytest.mark.parametrize("fault, expect", [
    (lambda s: s["questions"].pop(), "abstain: 7 questions, at least 8"),
    (lambda s: s["questions"][0]["gold"].append("p99#9"), "no memory p99#9"),
    (lambda s: s["questions"][1].update(id="s001"), "used twice"),
    (lambda s: s["questions"][2].update(why=""), "no why"),
    (lambda s: s["questions"][-1]["gold"].append("p01#2"), "an abstain question has no gold"),
    (lambda s: s.update(dataset_version=4), "written for dataset v4"),
    (lambda s: s["questions"][3].update(text=benchmark.load_dataset()["questions"][0]["text"]), "repeats a question"),
    (lambda s: s["questions"][0].update(lang="en", dialect=None), "single: 1 Arabic questions"),
    (lambda s: s["questions"][4].update(lang="fr"), "lang is en, ar or mixed"),
])
def test_the_shape_check_refuses_each_fault(fault, expect):
    sealed, _ = _planted()
    fault(sealed)
    problems = benchmark.sealed_problems(sealed, benchmark.load_dataset())
    assert any(expect in p for p in problems), problems


# ── The look: scored on request, logged, spent by a decision ──────────────


@pytest.fixture
def look_copy(tmp_path, monkeypatch):
    script = _script()
    sealed, source = _planted()
    path = tmp_path / "sealed_questions.json"
    path.write_text(json.dumps(sealed, ensure_ascii=False), encoding="utf-8")
    locked = tmp_path / "thresholds.json"
    locked.write_text(THRESHOLDS.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(script, "SEALED", path)
    monkeypatch.setattr(script, "SEALED_VECTORS", tmp_path / "sealed_vectors.npz")
    monkeypatch.setattr(script, "THRESHOLDS", locked)
    monkeypatch.setattr(script, "_limit_cpu_threads", lambda: None)
    replay = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    asked: list[str] = []

    class _Model:  # the "real" model: the recorded vector of the text it stands for
        def encode(self, text):
            asked.append(text)
            return replay.encode(source[text])

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: _Model())
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model)
    return script, path, locked, sealed, asked


def test_a_look_is_logged_and_a_deciding_look_spends_the_set(look_copy, capsys):
    script, path, locked, sealed, asked = look_copy
    assert script._cmd_sealed(None) == 1  # a look needs its reason
    assert "needs its reason" in capsys.readouterr().err

    assert script._cmd_sealed("the first look") == 0
    looks = json.loads(locked.read_text(encoding="utf-8"))["sealed"]["looks"]
    assert len(looks) == 1 and looks[0]["reason"] == "the first look" and not looks[0]["decided"]
    assert looks[0]["questions"] == 96 and looks[0]["file_sha256"]
    assert 0 <= looks[0]["scores"]["sealed.overall.hit_rate"] <= 1
    assert len(asked) == 96  # the questions' vectors, recorded once

    asked.clear()
    assert script._cmd_sealed("decide on the floor", decided=True) == 0
    assert asked == []  # replayed: the model is not asked again
    assert script._cmd_sealed("one more") == 1
    assert "spent" in capsys.readouterr().err
    assert len(json.loads(locked.read_text(encoding="utf-8"))["sealed"]["looks"]) == 2

    # New questions (another version of the file) may be looked at again.
    sealed["version"] = 2
    path.write_text(json.dumps(sealed, ensure_ascii=False), encoding="utf-8")
    assert script._cmd_sealed("the new set") == 0


def test_a_set_not_of_the_specified_shape_is_not_scored(look_copy, capsys):
    script, path, locked, sealed, _ = look_copy
    sealed["questions"] = sealed["questions"][:10]
    path.write_text(json.dumps(sealed, ensure_ascii=False), encoding="utf-8")
    assert script._cmd_sealed("a look") == 1
    assert "not the one specified" in capsys.readouterr().err
    assert "sealed" not in json.loads(locked.read_text(encoding="utf-8"))


def test_the_lock_keeps_every_look(tmp_path, monkeypatch):
    """Locking the recall scores again never drops the sealed set's log."""
    script = _script()
    locked = tmp_path / "thresholds.json"
    data = json.loads(THRESHOLDS.read_text(encoding="utf-8"))
    data["sealed"] = {"file": "sealed_questions.json", "looks": [{"date": "2026-11-20", "reason": "kept"}]}
    locked.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(script, "THRESHOLDS", locked)
    monkeypatch.setattr(benchmark, "gated_scores", lambda _r: dict(data["scores"]))
    monkeypatch.setattr(benchmark, "run_benchmark", lambda **_kw: {})
    monkeypatch.setattr(benchmark, "edge_check", lambda *_a, **_kw: {
        "draws": benchmark.EDGE_DRAWS, "seed": benchmark.EDGE_SEED, "move": benchmark.EDGE_MOVE,
        "largest_move": 0.0, "flips": {}, "shown": {}})
    assert script._cmd_lock(rebaseline=False) == 0
    assert json.loads(locked.read_text(encoding="utf-8"))["sealed"] == data["sealed"]
