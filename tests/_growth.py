"""How a function's time grows with its input: the one measure the regex tests share.

``tests/test_regexes_stay_linear.py`` and ``tests/test_sonar_regexes_linear.py``
both claim "this old pattern grows faster than its input" (their negative
controls) and "this new one does not". The claim is timed, so it must hold
on a busy machine: a shared CI runner, or this PC while other suites run.

How it is measured:

* the small and the large input are timed in turns, three of each, and the
  fastest of each is kept. A burst of load slows the calls made during it;
  timed in turns, a burst as long as three calls covers at most two of
  either input's, so the fastest of each stays clean. (Five of each, the
  first form, took the regex controls past pytest's 300 s on a slow CI
  runner, 2026-11-26: the large inputs of quadratic patterns are the cost.) Timed in a block (three small,
  then three large) one burst could cover every small call, and a
  quadratic pattern read as linear (CI, 2026-11-26: the old ``dd``
  pattern, sixteen times slower on four times the input);
* the majority of three such comparisons decides, and two that agree end
  it: one noisy comparison decides neither way (2026-11-21).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

#: How many times each input is timed in one comparison.
TURNS = 3


def timed(fn: Callable[..., Any], *args: Any) -> float:
    """Seconds ``fn(*args)`` took."""
    start = time.perf_counter()
    fn(*args)
    return time.perf_counter() - start


def grows_faster_than_linear(
    fn: Callable[[Any], Any],
    make: Callable[[int], Any],
    small: int,
    factor: int = 4,
    *,
    clock: Callable[..., float] = timed,
) -> bool:
    """True when ``factor`` times the input takes more than 1.5 x ``factor`` times as long.

    ``clock`` times one call; tests of this measure pass a stand-in.
    """
    small_input, large_input = make(small), make(factor * small)
    fn(small_input)  # warm

    def once() -> bool:
        t_small = t_large = float("inf")
        for _ in range(TURNS):
            t_small = min(t_small, clock(fn, small_input))
            t_large = min(t_large, clock(fn, large_input))
        return t_large > 1.5 * factor * t_small

    first, second = once(), once()
    return first if first == second else once()
