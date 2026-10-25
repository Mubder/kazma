"""The run supervision soak harness finds what it is meant to find.

``scripts/run_supervision_soak.py`` drives the real supervisor, incident store,
delivery and decision path through a simulated world with restarts, refused
sends and owner presses. A short run must come back clean, and each planted
bug -- a card sent twice, runs an adapter stops reporting, an action executed
outside the incident's claim -- must be caught (the negative controls).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("run_supervision_soak", REPO / "scripts/run_supervision_soak.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # @dataclass reads its module from sys.modules
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def test_a_short_soak_is_clean(harness, tmp_path):
    report = harness.soak(cycles=150, runs=30, seed=11, workdir=tmp_path)
    assert report["violations"] == {}, report["violations"]
    assert report["incidents"] > 10 and report["cards_sent"] == report["incidents"]
    assert report["stats"].get("restarts", 0) >= 1
    assert report["stats"].get("decisions_ok", 0) > 5


@pytest.mark.parametrize("plant,violation", [
    ("double_send", "double_send"), ("missed", "missed"), ("double_action", "double_action")])
def test_each_planted_bug_is_caught(harness, tmp_path, plant, violation):
    report = harness.soak(cycles=220, runs=40, seed=3, plant=plant, workdir=tmp_path)
    assert violation in report["violation_counts"], report
