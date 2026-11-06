"""Every step of the stop is timed (resilience plan C3).

8 of 163 reloads ran past the guard's graceful stop, and 9 shutdowns on 2
November exceeded their budget; the log could not say which step held them.
``StopClock`` times each step of ``KazmaAppBuilder._shutdown_services`` and
logs one line at the end: every step's time, or, over budget, a WARNING
naming the three slowest (the weekly report counts those, target zero).

A fake clock; the stop with its marks removed is the negative control (it
cannot name a step).
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from kazma_core import shutdown

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "kazma-ui" / "kazma_ui" / "app.py"


class _Now:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def test_a_stop_over_budget_names_its_slowest_steps(monkeypatch, caplog):
    monkeypatch.setenv("KAZMA_SHUTDOWN_BUDGET_S", "50")
    now = _Now()
    clock = shutdown.StopClock(now)
    for label, secs in (("cron", 0.2), ("documents and memory", 41.0), ("the swarm", 6.0),
                        ("the agent", 9.5), ("the chat apps", 1.0)):
        now.t += secs
        clock.mark(label)
    with caplog.at_level(logging.INFO, logger="kazma_core.shutdown"):
        assert clock.report() is True
    line = caplog.records[-1]
    assert line.levelno == logging.WARNING
    assert "ran over its 50 s budget: 57.7 s" in line.getMessage()
    assert "slowest: documents and memory 41.0 s; the agent 9.5 s; the swarm 6.0 s" in line.getMessage()


def test_a_stop_in_budget_lists_each_step(monkeypatch, caplog):
    monkeypatch.setenv("KAZMA_SHUTDOWN_BUDGET_S", "50")
    now = _Now()
    clock = shutdown.StopClock(now)
    now.t += 2.0
    clock.mark("cron")
    now.t += 0.01
    clock.mark("the agent")
    with caplog.at_level(logging.INFO, logger="kazma_core.shutdown"):
        assert clock.report() is False
    message = caplog.records[-1].getMessage()
    assert "took 2.0 s of its 50 s budget (cron 2.0 s)" in message


def _marks() -> list[str]:
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_shutdown_services":
            return [c.args[0].value for c in ast.walk(node)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "_mark_stop" and c.args and isinstance(c.args[0], ast.Constant)]
    raise AssertionError("_shutdown_services not found")


def test_every_step_of_the_stop_is_marked():
    marks = _marks()
    for step in ("cron", "documents and memory", "the swarm", "the agent", "the checkpointer",
                 "the stores", "the chat sessions"):
        assert step in marks, step
    assert len(marks) == len(set(marks))


def test_without_marks_the_clock_cannot_name_a_step(monkeypatch, caplog):
    # The negative control: the stop before C3 timed nothing.
    monkeypatch.setenv("KAZMA_SHUTDOWN_BUDGET_S", "5")
    now = _Now()
    clock = shutdown.StopClock(now)
    now.t += 30
    with caplog.at_level(logging.INFO, logger="kazma_core.shutdown"):
        clock.report()
    assert caplog.records[-1].getMessage().endswith("slowest: ")
