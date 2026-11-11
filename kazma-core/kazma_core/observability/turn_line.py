"""The line ``close_turn`` writes once per finished turn, and its one reader.

``kazma_ui.turn_runtime`` logs ``[turn] Turn finished: platform=<p> key=<k>
failed=<yes|no>`` for every turn that ends, from every transport. The daily
digest and the weekly report's targets both count turns from it. They kept
a reader each, and the weekly one matched ``failed=True|False`` while the
line says ``yes``/``no``: its "Turns that succeed" target read "unknown"
every week it ran (the report of 2026-11-11, over a week of turns).

The writer keeps its literal format string (the emitted-lines gate reads the
logger calls), and every reader goes through ``read_turn_line``
(``tests/test_weekly_report_reads_turns.py``).
"""

from __future__ import annotations

import re

__all__ = ["TURN_MARKER", "read_turn_line"]

TURN_MARKER = "[turn] Turn finished:"
_LINE = re.compile(r"\[turn\] Turn finished: platform=(\S+) key=(\S+) failed=(\S+)")
#: Values of ``failed=`` that mean the turn failed. ``yes`` is what the
#: writer says; ``True`` is kept for a line written by hand in a test or a
#: log from a build that never shipped.
_FAILED = frozenset({"yes", "true"})


def read_turn_line(message: str) -> tuple[str, str, bool] | None:
    """``(platform, key, failed)`` of a finished-turn line, or None."""
    match = _LINE.search(message or "")
    if match is None:
        return None
    return match.group(1), match.group(2), match.group(3).strip().lower() in _FAILED
