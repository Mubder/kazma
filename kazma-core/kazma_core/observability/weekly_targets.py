"""The weekly report against targets (resilience plan G3).

The weekly resilience report counted mechanisms; it did not say whether the
week was good. It now has a "Targets" section, each target met or missed
with the week before beside it, measured from the logs Kazma already writes
(the app log and the guard's) and the outside observer's uptime:

| Target | Goal | Measured from |
|---|---|---|
| Uptime | 99.5 % or better | the observer (``up_7d_pct``) |
| Turns that succeed | 99 % or better | ``[turn] Turn finished: ... failed=`` |
| Restarts not asked for | 0 | the guard's ``guard.restarting`` |
| Time to recover (median) | under 5 minutes | ``guard.restarting`` to ``guard.supervising`` |
| Backups fresh | every day | ``[universal-backup] complete:`` |
| Stops over budget | 0 | ``[shutdown] the stop ran over`` |
| Alerts per week | 10 or fewer | ``[ops_alert]`` lines not held back |

A target with nothing to measure (no observer paired, no turn this week) is
"unknown", never met.

Each line is read through ``log_lines``: an app line's marker counts only at
the start of its message, a guard event only in a guard record's ``event``
-- a message quoting a marker (a session id, a file name) is not a backup, a
turn or an alert (2026-11-16, tests/test_log_readers_read_markers_at_the_start.py).
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from kazma_core.observability.log_lines import opens_with, read_app_line, read_guard_line
from kazma_core.observability.turn_line import read_turn_line

__all__ = ["Target", "measure_targets", "render_targets"]

WEEK_S = 7 * 86400.0
_BACKUP = "[universal-backup] complete:"
_OVER_BUDGET = "[shutdown] the stop ran over"
#: An alert sent, not held back by its cooldown ("... (throttled)").
_ALERT = re.compile(r"\[ops_alert\] \S+ \|(?!.*\(throttled\))")


@dataclass
class Target:
    name: str
    goal: str
    value: float | None
    met: bool | None
    shown: str
    before: str = ""


def _week(app: list[tuple[float, str]], guard: list[tuple[float, str]], start: float, end: float,
          uptime: float | None) -> dict[str, Any]:
    """The week's values; *app* is (time, message), *guard* (time, event)."""
    messages = [message for t, message in app if start <= t < end]
    turns: dict[str, bool] = {}
    for message in messages:
        turn = read_turn_line(message)
        if turn is not None:
            _platform, key, failed = turn
            turns[key] = failed
    restarts = [t for t, event in guard if start <= t < end and event == "guard.restarting"]
    outages: list[float] = []
    began: float | None = None
    for t, event in sorted(guard, key=lambda row: row[0]):
        if event == "guard.restarting" and began is None:
            began = t
        elif event == "guard.supervising" and began is not None:
            if start <= began < end:
                outages.append(t - began)
            began = None
    backup_days = {int((t - start) // 86400) for t, message in app
                   if start <= t < end and opens_with(message, _BACKUP)}
    return {
        "uptime": uptime,
        "turns": (sum(1 for f in turns.values() if not f) / len(turns) * 100.0) if turns else None,
        "turn_count": len(turns),
        "restarts": len(restarts),
        "recover_s": statistics.median(outages) if outages else (0.0 if not restarts else None),
        "backup_days": len(backup_days),
        "over_budget": sum(1 for message in messages if opens_with(message, _OVER_BUDGET)),
        "alerts": sum(1 for message in messages if opens_with(message, _ALERT)),
    }


def _targets(now: dict[str, Any], before: dict[str, Any]) -> list[Target]:
    def pct(v: float | None) -> str:
        return "unknown" if v is None else f"{v:.1f}%"

    def mins(v: float | None) -> str:
        # Hours past two: "643.4 min" was the report of 2026-11-11.
        if v is None:
            return "unknown"
        if v == 0:
            return "none"
        return f"{v / 3600:.1f} h" if v >= 2 * 3600 else f"{v / 60:.1f} min"

    rows = [
        ("Uptime", "99.5% or better", now["uptime"], lambda v: v >= 99.5, pct, before["uptime"]),
        ("Turns that succeed", "99% or better", now["turns"], lambda v: v >= 99.0, pct, before["turns"]),
        ("Restarts not asked for", "0", now["restarts"], lambda v: v == 0, str, before["restarts"]),
        ("Time to recover (median)", "under 5 minutes", now["recover_s"], lambda v: v < 300, mins,
         before["recover_s"]),
        ("Backups fresh", "every day", now["backup_days"], lambda v: v >= 7, lambda v: f"{v} of 7 days",
         before["backup_days"]),
        ("Stops over budget", "0", now["over_budget"], lambda v: v == 0, str, before["over_budget"]),
        ("Alerts per week", "10 or fewer", now["alerts"], lambda v: v <= 10, str, before["alerts"]),
    ]
    out = []
    for name, goal, value, rule, fmt, prev in rows:
        out.append(Target(name, goal, value, None if value is None else bool(rule(value)), fmt(value),
                          "" if prev is None else fmt(prev)))
    return out


def measure_targets(app_lines: Iterable[str], guard_lines: Iterable[str], *, now: float,
                    uptime_now: float | None = None, uptime_before: float | None = None) -> list[Target]:
    """The targets of the week ending at *now*, with the week before's values."""
    app = [(record.ts, record.message) for record in map(read_app_line, app_lines) if record is not None]
    guard = [(record.ts, record.event) for record in map(read_guard_line, guard_lines) if record is not None]
    return _targets(_week(app, guard, now - WEEK_S, now, uptime_now),
                    _week(app, guard, now - 2 * WEEK_S, now - WEEK_S, uptime_before))


#: A target's mark: met, missed, nothing to measure.
_MARKS = {True: "✅", False: "❌", None: "❔"}


def render_targets(targets: list[Target]) -> list[str]:
    """The report's "Targets" section: a heading, then one line a target::

        Targets (2 missed):
          ❌ Uptime: 98.9% (goal 99.5% or better)
          ✅ Backups fresh: 7 of 7 days (goal every day; week before 1 of 7 days)

    It was one sentence of seven targets joined by semicolons, cut off at
    the alert's 600 characters (2026-11-11). Empty when nothing was measured.
    """
    if not targets:
        return []
    missed = sum(1 for t in targets if t.met is False)
    if missed:
        head = f"Targets ({missed} missed):"
    elif all(t.met for t in targets):
        head = "Targets (all met):"
    else:
        head = "Targets:"
    lines = [head]
    for t in targets:
        aside = f"goal {t.goal}" + (f"; week before {t.before}" if t.before else "")
        lines.append(f"  {_MARKS[t.met]} {t.name}: {t.shown} ({aside})")
    return lines
