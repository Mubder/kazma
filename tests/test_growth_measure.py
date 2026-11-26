"""The regex tests' growth measure holds on a busy machine (``tests/_growth.py``).

CI, 2026-11-26: the old ``dd`` pattern takes sixteen times as long on four
times the input, yet ``test_control_the_old_patterns_grow_faster_than_the_input``
read it as linear on a loaded runner. The measure timed three small calls and
then three large ones, so one burst of load could cover every small call.
It now times the two inputs in turns. These tests drive the measure with a
stand-in clock: a quadratic cost, or a linear one, and a burst of load as
long as three calls at the same place in every comparison.

The gate: every test helper that says whether a function grows faster than
its input asks ``tests/_growth.py``, and none times on its own.
"""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Callable
from pathlib import Path

from tests._growth import TURNS, grows_faster_than_linear

REPO = Path(__file__).resolve().parents[1]

#: How long a burst of load lasts, in calls, and how much it slows them.
BURST = 3
SLOW = 10.0


class _BurstyClock:
    """A stand-in clock: ``cost(len(arg))`` seconds a call, ``SLOW`` times that inside a burst.

    Each comparison makes ``per_round`` timed calls; the burst covers
    ``BURST`` calls of each, starting at ``offset``.
    """

    def __init__(self, cost: Callable[[int], float], per_round: int, offset: int) -> None:
        self.cost, self.per_round, self.offset, self.calls = cost, per_round, offset, 0

    def __call__(self, fn, arg) -> float:
        at = self.calls % self.per_round
        self.calls += 1
        busy = self.offset <= at < self.offset + BURST
        return self.cost(len(arg)) * (SLOW if busy else 1.0)


def _quadratic(n: int) -> float:
    return n * n * 1e-9


def _linear(n: int) -> float:
    return n * 1e-6


def _block_measure(fn, make, small: int, factor: int, clock) -> bool:
    """The measure as it was until 2026-11-26: three small calls, then three large."""
    def once() -> bool:
        t1 = min(clock(fn, make(small)) for _ in range(3))
        t2 = min(clock(fn, make(factor * small)) for _ in range(3))
        return t2 > 1.5 * factor * t1

    first, second = once(), once()
    return first if first == second else once()


def _make(k: int) -> str:
    return "dd " * k


def test_a_quadratic_function_reads_as_growing_wherever_a_burst_falls():
    for offset in range(2 * TURNS - BURST + 1):
        clock = _BurstyClock(_quadratic, 2 * TURNS, offset)
        assert grows_faster_than_linear(lambda s: None, _make, 1000, clock=clock), offset


def test_a_linear_function_reads_as_linear_wherever_a_burst_falls():
    for offset in range(2 * TURNS - BURST + 1):
        clock = _BurstyClock(_linear, 2 * TURNS, offset)
        assert not grows_faster_than_linear(lambda s: None, _make, 1000, clock=clock), offset


def test_control_the_block_measure_read_a_quadratic_as_linear_under_a_burst():
    """What CI saw: a burst over the three small calls hid sixteenfold growth."""
    clock = _BurstyClock(_quadratic, 6, 0)
    assert not _block_measure(lambda s: None, _make, 1000, 4, clock)


def test_control_the_block_measure_read_a_linear_function_as_growing_under_a_burst():
    clock = _BurstyClock(_linear, 6, 3)
    assert _block_measure(lambda s: None, _make, 1000, 4, clock)


# ── the gate: one measure ─────────────────────────────────────────────────

_OWN_TIMING = {"perf_counter", "monotonic", "time", "_timed", "timed"}


def _measures_on_their_own(source: str) -> list[str]:
    """Growth helpers in ``source`` that time calls themselves instead of asking ``tests/_growth.py``."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.FunctionDef) or "grows_faster" not in node.name:
            continue
        if node.name.startswith("test_"):  # a test that asks, not a helper that measures
            continue
        called = {
            (c.func.attr if isinstance(c.func, ast.Attribute) else getattr(c.func, "id", ""))
            for c in ast.walk(node) if isinstance(c, ast.Call)
        }
        if called & _OWN_TIMING or "grows_faster_than_linear" not in called:
            found.append(node.name)
    return found


def _test_files() -> list[Path]:
    listed = subprocess.run(["git", "ls-files", "tests"], cwd=REPO, capture_output=True, text=True, check=True)
    return [REPO / p for p in listed.stdout.split() if p.endswith(".py") and not p.endswith("_growth.py")
            and p != "tests/test_growth_measure.py"]


def test_every_growth_helper_asks_the_one_measure():
    found = {}
    for path in _test_files():
        source = path.read_text(encoding="utf-8")
        if "grows_faster" in source:
            names = _measures_on_their_own(source)
            if names:
                found[path.relative_to(REPO).as_posix()] = names
    assert found == {}, found


def test_the_gate_sees_both_regex_files():
    seen = [p.name for p in _test_files() if "def _grows_faster_than_linear" in p.read_text(encoding="utf-8")]
    assert sorted(seen) == ["test_regexes_stay_linear.py", "test_sonar_regexes_linear.py"]


def test_control_a_helper_that_times_on_its_own_is_found():
    old = (
        "def _grows_faster_than_linear(fn, make, small, factor=4):\n"
        "    t1 = min(_timed(fn, make(small)) for _ in range(3))\n"
        "    t2 = min(_timed(fn, make(factor * small)) for _ in range(3))\n"
        "    return t2 > 1.5 * factor * t1\n"
        "def _grows_faster_than_linear_too(fn):\n"
        "    return grows_faster_than_linear(fn, str, 1)\n"
        "def test_the_old_one_grows_faster():\n"
        "    assert _timed(len, 'x') >= 0\n"
    )
    assert _measures_on_their_own(old) == ["_grows_faster_than_linear"]
