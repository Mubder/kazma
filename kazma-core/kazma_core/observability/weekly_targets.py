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
"""

from __future__ import annotations

import json
import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["Target", "measure_targets", "render_targets"]

WEEK_S = 7 * 86400.0
_TURN = re.compile(r"\[turn\] Turn finished: platform=\S* key=(\S+) failed=(True|False)")
_BACKUP = "[universal-backup] complete:"
_OVER_BUDGET = "[shutdown] the stop ran over"
_ALERT = re.compile(r"\[ops_alert\] \S+ \|(?!.*\(throttled\))")


@dataclass
class Target:
    name: str
    goal: str
    value: float | None
    met: bool | None
    shown: str
    before: str = ""


def _epoch(ts: str) -> float | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _app_line(line: str) -> tuple[float | None, str]:
    """(time, message) of one app log line (JSON; a plain line is matched whole)."""
    if line.lstrip().startswith("{"):
        try:
            obj = json.loads(line)
        except ValueError:
            return None, ""
        return _epoch(str(obj.get("timestamp") or obj.get("ts") or "")), str(obj.get("message") or "")
    match = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})", line)
    return (_epoch(match.group(1).replace(" ", "T")) if match else None), line


def _week(app: list[tuple[float, str]], guard: list[tuple[float, dict]], start: float, end: float,
          uptime: float | None) -> dict[str, Any]:
    msgs = [m for t, m in app if start <= t < end]
    turns: dict[str, bool] = {}
    for m in msgs:
        for key, failed in _TURN.findall(m):
            turns[key] = failed == "True"
    events = [(t, e) for t, e in guard if start <= t < end]
    restarts = [t for t, e in events if e.get("event") == "guard.restarting"]
    outages: list[float] = []
    began: float | None = None
    for t, e in sorted(guard, key=lambda row: row[0]):
        if e.get("event") == "guard.restarting" and began is None:
            began = t
        elif e.get("event") == "guard.supervising" and began is not None:
            if start <= began < end:
                outages.append(t - began)
            began = None
    backup_days = {int((t - start) // 86400) for t, m in app if start <= t < end and _BACKUP in m}
    return {
        "uptime": uptime,
        "turns": (sum(1 for f in turns.values() if not f) / len(turns) * 100.0) if turns else None,
        "turn_count": len(turns),
        "restarts": len(restarts),
        "recover_s": statistics.median(outages) if outages else (0.0 if not restarts else None),
        "backup_days": len(backup_days),
        "over_budget": sum(1 for m in msgs if _OVER_BUDGET in m),
        "alerts": sum(1 for m in msgs if _ALERT.search(m)),
    }


def _targets(now: dict[str, Any], before: dict[str, Any]) -> list[Target]:
    def pct(v: float | None) -> str:
        return "unknown" if v is None else f"{v:.1f}%"

    def mins(v: float | None) -> str:
        return "unknown" if v is None else ("none" if v == 0 else f"{v / 60:.1f} min")

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
    app = [(t, m) for t, m in (_app_line(line) for line in app_lines) if t is not None]
    guard: list[tuple[float, dict]] = []
    for line in guard_lines:
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        t = _epoch(str(obj.get("ts") or ""))
        if t is not None and isinstance(obj, dict):
            guard.append((t, obj))
    return _targets(_week(app, guard, now - WEEK_S, now, uptime_now),
                    _week(app, guard, now - 2 * WEEK_S, now - WEEK_S, uptime_before))


def render_targets(targets: list[Target]) -> str:
    """The report's "Targets" sentence."""
    parts = []
    for t in targets:
        state = "met" if t.met else ("missed" if t.met is False else "unknown")
        text = f"{t.name} {t.shown} (goal {t.goal}): {state}"
        if t.before:
            text += f", week before {t.before}"
        parts.append(text)
    missed = sum(1 for t in targets if t.met is False)
    head = f"Targets ({missed} missed)" if missed else "Targets (all met)" if all(t.met for t in targets) \
        else "Targets"
    return head + ": " + "; ".join(parts) + "."
