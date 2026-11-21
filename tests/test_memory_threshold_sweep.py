"""The threshold sweep (``scripts/memory_threshold_sweep.py``, 2026-11-21):
its rule and its split, without running the benchmark.

* The split is fixed: the same tuning and held halves on every machine,
  balanced by category and script, no question in both.
* The rule keeps today's values unless a configuration loses no answer, on
  any store, and wins by more than one question or 0.01 of quality.
* A value one step from a lost answer, or on its grid's edge, does not move
  (rule 5). Negative controls: the rule without rule 5 moves it; the rule
  without the "any store" answers moves to a configuration that lost one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def sweep():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import memory_threshold_sweep
    finally:
        sys.path.pop(0)
    return memory_threshold_sweep


def _q(qid: str, category: str, text: str = "where") -> dict:
    return {"id": qid, "category": category, "text": text}


def test_the_split_is_fixed_balanced_and_disjoint(sweep):
    data = {"questions": [_q(f"q{i}", "single") for i in range(10)]
            + [_q(f"a{i}", "abstain") for i in range(5)]
            + [_q(f"r{i}", "arabic", "أين أسكن") for i in range(4)],
            "second": [_q(f"h{i}", "single") for i in range(3)]}
    people = {"questions": [{"id": f"s{i}", "category": "self", "lang": "en"} for i in range(6)]}
    halves = sweep.split(data, people)
    assert halves == sweep.split(data, people)
    tune, held = set(halves["bench"]["tune"]), set(halves["bench"]["held"])
    assert not tune & held and len(tune | held) == 22
    assert abs(len(tune) - len(held)) <= 1
    # Each category is dealt in turn: neither half gets every Arabic question.
    assert {q for q in tune if q.startswith("r")} and {q for q in held if q.startswith("r")}
    assert len(halves["speakers"]["tune"]) == len(halves["speakers"]["held"]) == 3


def _row(floor: float, gap: float, lost: int, abstained: int = 0, quality: float = 1.0) -> dict:
    return {"config": {"episode_floor": floor, "episode_gap": gap}, "lost": lost,
            "m": {"abstained": abstained, "quality": quality}}


BASE = {"episode_floor": 0.04, "episode_gap": 0.15}
AXES = {"episode_floor": [0.03, 0.04, 0.05, 0.06, 0.07], "episode_gap": [0.13, 0.14, 0.15, 0.16, 0.17]}


def _grid(lost_at: set[tuple[float, float]], better: dict[tuple[float, float], tuple[int, float]]) -> list[dict]:
    rows = []
    for f in AXES["episode_floor"]:
        for g in AXES["episode_gap"]:
            abstained, quality = better.get((f, g), (0, 1.0))
            rows.append(_row(f, g, 1 if (f, g) in lost_at else 0, abstained, quality))
    return rows


def test_today_stays_when_nothing_wins_by_more_than_a_tie(sweep):
    rows = _grid(set(), {(0.05, 0.15): (1, 1.005)})  # one more empty question, quality within 0.01
    assert sweep.choose(rows, BASE, AXES)["config"] == BASE


def test_a_configuration_that_loses_an_answer_is_never_chosen(sweep):
    rows = _grid({(0.06, 0.15)}, {(0.06, 0.15): (5, 1.5)})
    assert sweep.choose(rows, BASE, AXES)["config"] == BASE


def test_a_clear_win_inside_a_safe_neighbourhood_moves(sweep):
    rows = _grid(set(), {(0.05, 0.15): (3, 1.2)})
    assert sweep.choose(rows, BASE, AXES)["config"] == {"episode_floor": 0.05, "episode_gap": 0.15}


def test_a_value_one_step_from_a_lost_answer_does_not_move(sweep):
    rows = _grid({(0.06, 0.15)}, {(0.05, 0.15): (3, 1.2)})
    assert sweep.choose(rows, BASE, AXES)["config"] == BASE
    # Negative control: without rule 5 the same rows move to the edge of a loss.
    assert sweep.choose(rows, BASE)["config"] == {"episode_floor": 0.05, "episode_gap": 0.15}


def test_a_value_on_the_grid_edge_does_not_move(sweep):
    rows = _grid(set(), {(0.07, 0.15): (3, 1.2)})
    assert sweep.choose(rows, BASE, AXES)["config"] == BASE


def test_an_answer_lost_on_any_store_counts(sweep):
    today = {"main": [{"id": "q1", "rr": 1.0, "hit": True, "precision": 1.0}],
             "scale": [{"id": "q1", "rr": 1.0, "hit": True, "precision": 1.0}],
             "small": [], "speakers": [{"id": "s1", "rr": 1.0, "hit": True, "precision": 1.0, "fact_hit": True}]}
    tighter = {"main": today["main"], "scale": [{"id": "q1", "rr": 0.0, "hit": False, "precision": 0.0}],
               "small": [], "speakers": [{"id": "s1", "rr": 1.0, "hit": True, "precision": 1.0, "fact_hit": False}]}
    lost = sweep.shown_answers(today) - sweep.shown_answers(tighter)
    assert lost == {"scale:q1", "speakers:s1:fact"}
    # Negative control: the main store alone sees nothing lost.
    assert not ({a for a in sweep.shown_answers(today) if a.startswith("main:")}
                - {a for a in sweep.shown_answers(tighter) if a.startswith("main:")})


def test_every_swept_value_is_one_recall_reads_and_today_is_inside_its_grid(sweep):
    """A value at its grid's edge can never move (rule 5): today's must sit
    inside every grid, or the sweep could not move it either way."""
    from kazma_core.memory import recall

    for attr in sweep.PARAMS.values():
        assert isinstance(getattr(recall, attr), float)
    for _name, axes in sweep.STAGES:
        for name, values in axes.items():
            today = float(getattr(recall, sweep.PARAMS[name]))
            assert values[0] < today < values[-1], name
