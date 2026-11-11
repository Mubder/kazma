"""The weekly report against targets (resilience plan G3).

The report counted mechanisms and never said whether the week was good. Its
"Targets" section marks each target met or missed, with the week before
beside it, measured from the logs Kazma writes (the app's and the guard's)
and the observer's uptime. Built here from a recorded fortnight of log
lines, with a target missed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from kazma_core.observability.weekly_targets import WEEK_S, measure_targets, render_targets

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC).timestamp()


def _app(t: float, message: str) -> str:
    stamp = datetime.fromtimestamp(t, UTC).isoformat()
    return json.dumps({"timestamp": stamp, "level": "INFO", "logger": "x", "message": message})


def _guard(t: float, event: str) -> str:
    return json.dumps({"ts": datetime.fromtimestamp(t, UTC).isoformat(), "event": event})


def _fortnight():
    app, guard = [], []
    for week, start in (("before", NOW - 2 * WEEK_S), ("this", NOW - WEEK_S)):
        for day in range(7):
            app.append(_app(start + day * 86400 + 3600, "[universal-backup] complete: 12 dbs"))
        turns = 200
        failed = 1 if week == "before" else 6  # this week: 97 % -- missed
        for i in range(turns):
            # The writer's own words (kazma_ui.turn_runtime): failed=yes|no.
            # This fixture wrote failed=True|False, the form the reader
            # expected and no code writes (2026-11-11).
            outcome = "yes" if i < failed else "no"
            app.append(_app(start + 100 + i, f"[turn] Turn finished: platform=web key={week}{i} failed={outcome}"))
        for i in range(3 if week == "this" else 12):
            app.append(_app(start + 5000 + i, f"[ops_alert] key.{i} | an alert"))
        app.append(_app(start + 6000, "[ops_alert] key.0 | an alert (throttled)"))
    # This week: one outage, Kazma restarted twice, healthy 4 minutes later.
    t = NOW - WEEK_S + 86400
    guard += [_guard(t, "guard.restarting"), _guard(t + 60, "guard.restarting"),
              _guard(t + 240, "guard.supervising")]
    app.append(_app(NOW - 3600, "[shutdown] the stop ran over its 50 s budget: 61.2 s; slowest: x 30 s"))
    return app, guard


def _by_name(targets):
    return {t.name: t for t in targets}


def test_each_target_is_measured_with_the_week_before():
    app, guard = _fortnight()
    got = _by_name(measure_targets(app, guard, now=NOW, uptime_now=99.91, uptime_before=99.2))
    assert got["Uptime"].met is True and got["Uptime"].before == "99.2%"
    assert got["Turns that succeed"].shown == "97.0%" and got["Turns that succeed"].met is False
    assert got["Turns that succeed"].before == "99.5%"
    assert got["Restarts not asked for"].shown == "2" and got["Restarts not asked for"].met is False
    assert got["Restarts not asked for"].before == "0"
    assert got["Time to recover (median)"].shown == "4.0 min" and got["Time to recover (median)"].met is True
    assert got["Backups fresh"].shown == "7 of 7 days" and got["Backups fresh"].met is True
    assert got["Stops over budget"].shown == "1" and got["Stops over budget"].met is False
    assert got["Alerts per week"].shown == "3" and got["Alerts per week"].met is True
    assert got["Alerts per week"].before == "12", "a throttled repeat is no alert"


def test_the_section_names_what_was_missed_one_target_a_line():
    """Re-pointed 2026-11-11: one line a target, marked; it was one sentence
    of seven targets joined by semicolons, cut at the alert's 600 characters."""
    app, guard = _fortnight()
    lines = render_targets(measure_targets(app, guard, now=NOW, uptime_now=99.91))
    assert lines[0] == "Targets (3 missed):"
    assert "  ❌ Turns that succeed: 97.0% (goal 99% or better; week before 99.5%)" in lines
    assert "  ✅ Backups fresh: 7 of 7 days (goal every day; week before 7 of 7 days)" in lines
    assert len(lines) == 8 and render_targets([]) == []


def test_a_long_recovery_is_said_in_hours():
    """The report of 2026-11-11 said "643.4 min" for an eleven-hour outage."""
    t = NOW - WEEK_S + 86400
    guard = [_guard(t, "guard.restarting"), _guard(t + 38604, "guard.supervising")]
    got = _by_name(measure_targets([], guard, now=NOW))
    assert got["Time to recover (median)"].shown == "10.7 h"
    short = [_guard(t, "guard.restarting"), _guard(t + 204, "guard.supervising")]
    assert _by_name(measure_targets([], short, now=NOW))["Time to recover (median)"].shown == "3.4 min"


def test_nothing_to_measure_is_unknown_never_met():
    targets = _by_name(measure_targets([], [], now=NOW))
    assert targets["Uptime"].met is None and targets["Uptime"].shown == "unknown"
    assert targets["Turns that succeed"].met is None
    assert targets["Backups fresh"].met is False, "no backup in a week is a miss"


def test_the_weekly_report_carries_the_targets():
    from kazma_core.observability.firing_ledger import LedgerReport, render

    app, guard = _fortnight()
    report = LedgerReport(targets=measure_targets(app, guard, now=NOW, uptime_now=99.91))
    title, body = render(report)
    assert "\n\nTargets (3 missed):\n  ✅ Uptime: 99.9%" in body
    # A missed target is in the title, and makes the report a warning.
    assert title == "Weekly resilience report — 3 targets missed"
    # Negative control: a report without the section says nothing of targets.
    assert "Targets" not in render(LedgerReport())[1]
