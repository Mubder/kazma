"""Recall in a small store leaves unrelated conversations out (2026-11-21).

A store holding fewer memories than the meaning pool (``recall._DENSE_POOL``)
has no tail: the background window of ranks 4-15 is nearly the whole store,
the average of unrelated memories, so the nearest unrelated turns always
cleared the floor as "possibly related". The quick recall check (each case
among twenty unrelated memories) failed 4 of 6 with the real model; in small
stores made from the benchmark, no unanswerable question came back empty.
The turns' background is now at least the store's own mean plus the measured
offset of a full store's background (``recall._TAIL_OFFSET``).

* The small-store run (``benchmark.run_small_store_benchmark``, recorded
  vectors) holds the lock's ``small`` scores. Negative control: with the old
  background, precision falls below them.
* A full pool is unchanged (the main benchmark's lock is exact).
* A question asking for every memory, another embedding model and the facts'
  lane keep their own window.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from kazma_core.memory import benchmark  # noqa: E402
from kazma_core.memory import recall as rm  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "memory_bench"
LOCK = json.loads((FIXTURES / "thresholds.json").read_text(encoding="utf-8"))


@pytest.fixture()
def replay(monkeypatch):
    rec = benchmark.ReplayEmbedder(FIXTURES / "vectors.npz")
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: rec)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: rec.model)
    monkeypatch.setattr("kazma_core.memory.recall._recall_deadline_s", lambda: 300.0)
    return rec


def test_the_small_store_run_holds_the_lock(replay):
    overall = benchmark.run_small_store_benchmark()["overall"]
    locked = LOCK["small"]["scores"]
    assert overall["questions"] == LOCK["small"]["questions"]
    below = {k: (v, overall[k]) for k, v in locked.items() if overall[k] < v}
    assert below == {}


def test_control_with_the_old_background_unrelated_turns_are_shown(replay, monkeypatch):
    monkeypatch.setattr(rm, "_tail_offset", lambda kind: None)
    overall = benchmark.run_small_store_benchmark()["overall"]
    assert overall["precision"] < LOCK["small"]["scores"]["precision"] - 0.05, overall
    assert overall["abstained"] < LOCK["small"]["scores"]["abstained"], overall


# ── the rule itself ─────────────────────────────────────────────────────────


@pytest.fixture()
def bge(monkeypatch):
    from kazma_core.memory.embedder import DEFAULT_MODEL

    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: DEFAULT_MODEL)


SMALL = [0.60, 0.51, 0.47, 0.45, 0.45, 0.44, 0.43, 0.42, 0.41, 0.40, 0.40, 0.39, 0.38, 0.37, 0.36]


def test_a_small_store_judges_turns_against_its_mean_plus_the_offset(bge):
    own = sum(sorted(SMALL, reverse=True)[3:15]) / 12
    raised = sum(SMALL) / len(SMALL) + rm._TAIL_OFFSET["episode"]
    assert raised > own
    assert rm._question_background(SMALL) == pytest.approx(raised)
    # The nearest unrelated turn (0.51) no longer clears the floor; the answer does.
    assert 0.51 - rm._question_background(SMALL) < rm._EVIDENCE_FLOOR
    assert 0.60 - rm._question_background(SMALL) >= rm._EVIDENCE_FLOOR


def test_a_full_pool_and_the_other_lanes_keep_their_window(bge, monkeypatch):
    full = [0.6 - i * 0.004 for i in range(rm._DENSE_POOL)]
    window = sum(sorted(full, reverse=True)[3:15]) / 12
    assert rm._question_background(full) == pytest.approx(window)
    own = sum(sorted(SMALL, reverse=True)[3:15]) / 12
    assert rm._question_background(SMALL, kind="belief") == pytest.approx(own)
    monkeypatch.setattr(rm, "_LISTING", _Var(True))
    assert rm._question_background(SMALL) == pytest.approx(own)


def test_another_embedding_model_keeps_its_window(monkeypatch):
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedding_model_name", lambda: "text-embedding-3-small")
    own = sum(sorted(SMALL, reverse=True)[3:15]) / 12
    assert rm._question_background(SMALL) == pytest.approx(own)


class _Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def test_a_few_memories_that_all_match_keep_their_window(bge, monkeypatch):
    """A store of a few memories, every one of them about the question: their
    mean is the answers' own, and the background raised above it dropped them
    all (the full suite, 2026-11-21: an archived and an active memory of one
    question; a space's one matching memory)."""
    few = [0.71, 0.69, 0.68, 0.40, 0.38]
    own = rm._question_background(few)
    assert 0.68 - own >= rm._EVIDENCE_FLOOR
    # Control: without the size guard the answers fall below the floor.
    monkeypatch.setattr(rm, "_SMALL_TAIL_MIN", 0)
    raised = rm._question_background(few)
    assert raised > own and 0.68 - raised < rm._EVIDENCE_FLOOR
