"""The Scheduled soak harness works, and sees a loss (Scheduled audit, Stage 5 gate).

``scripts/scheduled_soak.py`` is the harness for the 7-day fake-clock soak
that the Stage 5 exit gate of ``SCHEDULED_INDUSTRIAL_HARDENING_GOAL.md`` asks
for. A session cannot hold that run, so this test keeps the harness honest
in three ways.

* A short run ends healthy, and it did the work it reports: three simulated
  days with 240 jobs, both kinds of job fired, every kind of failure
  injected, restarts of every kind, a missed job with one notice, the New
  York clock change crossed, and a dead loop seen by readiness.
* The report is on disk, whole, while the run goes.
* The run's own check reports a planted violation: a message delivered
  twice, an occurrence that never fired and was not recorded missed, a
  non-delivered job nobody was told about, and an alert raised twice with no
  kill recorded between raising it and marking it sent (or a recorded one
  that was not raised again). These are the negative controls.
* The check does not misjudge a legitimate case: a daily job edited to a
  later time on a day it already fired fires again that day (the 28-day
  soak's two false violations, 2026-10-15), while a series that fires twice
  in one day, and an old series firing after the edit, are still caught.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import scheduled_soak  # noqa: E402

pytestmark = pytest.mark.timeout(300)

#: Each status a reader of the report file saw, in order, during the run.
_WRITTEN: list[str] = []


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    data = tmp_path_factory.mktemp("scheduled-soak") / "data"
    out = tmp_path_factory.mktemp("scheduled-soak-report")
    report = out / "report.json"
    real_write = scheduled_soak._write_report

    def record(path, summary):
        real_write(path, summary)
        if path:
            # What a reader of the file sees at that moment, not what was passed.
            _WRITTEN.append(json.loads(Path(path).read_text(encoding="utf-8"))["status"])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(scheduled_soak, "_write_report", record)
        code = scheduled_soak.main([
            "--days", "3", "--jobs", "240", "--seed", "7",
            # Rates raised so a short run injects every kind at least once.
            "--fail-rate", "0.15", "--broken-rate", "0.12", "--raise-rate", "0.05",
            "--dead-target-rate", "0.02", "--no-target-rate", "0.02", "--heal-target-rate", "0.02",
            "--db-locks-per-day", "0.7",
            "--data-dir", str(data), "--report", str(report),
        ])
    ledger = json.loads((out / "report.ledger.json").read_text(encoding="utf-8"))
    return code, json.loads(report.read_text(encoding="utf-8")), ledger


def test_a_short_soak_with_failures_and_restarts_is_healthy(run):
    code, report, _ = run
    assert code == 0 and report["healthy"], report["violations"]
    assert report["status"] == "passed" and report["violation_count"] == 0


def test_the_run_did_the_work_it_reports(run):
    """A soak that booked nothing or injected nothing passes nothing."""
    _, report, ledger = run
    jobs = report["jobs"]
    assert set(jobs["by_kind"]) == {"reminder", "task"}
    assert {"once", "daily", "weekly"} <= set(jobs["by_timing"])
    assert {"z", "offset", "naive", "relative", "friendly", "rrule"} <= set(jobs["by_form"])
    assert {"America/New_York", "Asia/Kuwait", "UTC"} <= set(jobs["by_zone"])
    assert jobs["own_catchup"] and jobs["booked_mid_run"] and jobs["broken"]
    assert {"none", "dead", "heal"} <= set(jobs["by_target"])
    assert jobs["edits"].get("timing:rescheduled") and jobs["edits"].get("cancel:cancelled")
    assert jobs["edits"].get("timing:conflict") or jobs["edits"].get("prompt:conflict"), jobs["edits"]
    assert jobs["edits"].get("cancel:conflict"), jobs["edits"]
    # Every kind of failure was injected, and the database's did fail writes.
    injected = report["injected"]
    for kind in ("turn_raise", "turn_failed", "turn_interrupt", "provider_outage_fires", "send_refused",
                 "send_raised", "send_died", "send_refused_dead_target", "db_locks_poll", "db_locks_mid_turn"):
        assert injected.get(kind), (kind, injected)
    # Each lock hold failed a write of the scheduler; one, a fire's own finish.
    assert injected["db_locked_writes"] >= 2 and injected["db_locked_fire_finishes"] >= 1, injected
    assert not [k for k in injected if k.endswith("_without_a_failed_write")], injected
    # Each kind of job fired and ended each way.
    outcomes = report["occurrences"]["by_kind_outcome"]
    assert outcomes.get("reminder:delivered") and outcomes.get("task:delivered"), outcomes
    fired = report["fires"]["by_outcome"]
    assert fired.get("delivering") and fired.get("turn_failed") and fired.get("error"), fired
    states = report["deliveries"]["by_state"]
    assert states.get("delivered") and states.get("undeliverable") and states.get("unknown"), states
    alerts = report["alerts"]["by_key"]
    for key in ("cron.job_failure", "cron.job_failed", "cron.delivery_failed", "cron.delivery_unknown"):
        assert alerts.get(key), (key, alerts)
    # Killed around an alert both ways: after the change that queued it
    # committed (still exactly one), and after it was raised, before its mark
    # (raised once more, recorded): the only duplicates, and each was raised.
    assert injected.get("alert_kill_after_commit") and injected.get("alert_kill_before_mark"), injected
    assert report["alerts_redelivered_after_kill"] == len(ledger["alert_redelivered"]) >= 1
    assert report["alerts"]["total"] == report["alerts_expected"] + report["alerts_redelivered_after_kill"]
    # A broken series ran out of its failure budget and stopped, with its alert.
    assert any(f["status"] == "failed" and f["failures"] >= ledger["budget"] for f in ledger["fires"])
    # Restarts of every kind, and the long downtime missed jobs with one notice each.
    restarts = report["restarts"]["by_kind"]
    for kind in ("graceful", "abrupt_mid_turn", "abrupt_mid_send", "abrupt_after_alert_commit",
                 "abrupt_before_alert_mark", "gap", "after_dead_loop"):
        assert restarts.get(kind), (kind, restarts)
    assert report["fires"]["run_again_after_interruption"] >= 1
    assert ledger["missed"]
    notices = [d for d in ledger["deliveries"].values() if d.get("purpose") == "missed"]
    assert len(notices) == len(ledger["missed"])
    assert {(d["job"], d["due"]) for d in notices} == {(m["job"], m["due"]) for m in ledger["missed"]}
    # The New York fall-back was crossed by series that fired on both sides.
    assert report["clock_change"]["at"] == "2026-11-01T06:00:00+00:00"
    assert report["clock_change"]["series_across_change"] >= 1
    # Readiness saw the dead loop at once; lateness stayed inside its bound.
    assert report["dead_loop"]["detected_after_s"] == 0.0 and report["dead_loop"]["before"] == "ok"
    late = report["lateness_s"]
    assert late["p95_within_bound"] and late["while_up"]["n"] >= 100 and late["catch_up"]["n"] >= 1
    assert report["visibility_checks"] >= 1 and report["second_opinions"] >= 1


def test_the_report_is_on_disk_while_the_soak_runs(run):
    """A seven-day run that a reboot ends early must still leave its evidence:
    the report is rewritten whole as it goes, then marked finished."""
    assert _WRITTEN.count("running") >= 10, _WRITTEN
    assert _WRITTEN[-1] == "passed" and "passed" not in _WRITTEN[:-1], _WRITTEN


def test_the_recorded_run_checks_clean_again(run):
    _, _, ledger = run
    problems, _ = scheduled_soak.analyse(ledger)
    assert problems == []


def test_the_check_sees_a_message_delivered_twice(run):
    """Negative control: one delivered message that the platform took twice."""
    _, _, ledger = run
    planted = copy.deepcopy(ledger)
    did, row = next((k, d) for k, d in planted["deliveries"].items() if d["state"] == "delivered")
    row["accepted"] = 2
    problems, _ = scheduled_soak.analyse(planted)
    assert any(f"delivery {did} " in p and "delivered 2 times" in p for p in problems), problems


def test_the_check_sees_an_occurrence_that_never_fired(run):
    """Negative control: one fire taken out of the record, nothing missed in its place."""
    _, _, ledger = run
    planted = copy.deepcopy(ledger)
    end = planted["run_end"]
    gone = next(f for f in planted["fires"]
                if f["outcome"] == scheduled_soak.SUCCESS
                and scheduled_soak._dt(f["due"]).timestamp() < end)
    planted["fires"].remove(gone)
    problems, _ = scheduled_soak.analyse(planted)
    assert any(p.startswith("lost:") and gone["job"] in p and gone["due"] in p for p in problems), problems


def test_the_check_sees_a_failure_nobody_was_told_about(run):
    """Negative control: an undeliverable message, and a failed fire, with
    their alerts taken out."""
    _, _, ledger = run
    for key in ("cron.delivery_failed", "cron.job_failure"):
        planted = copy.deepcopy(ledger)
        alert = next(a for a in planted["alerts"] if a["key"] == key)
        planted["alerts"].remove(alert)
        problems, _ = scheduled_soak.analyse(planted)
        assert any(alert["job"] in p and key in p and "expected" in p for p in problems), (key, problems)


def test_the_check_tells_a_recorded_redelivery_from_any_other_duplicate(run):
    """Negative controls: an alert raised twice with no kill recorded is a
    violation; so is a recorded kill whose alert was not raised again."""
    _, _, ledger = run
    planted = copy.deepcopy(ledger)
    alert = next(a for a in planted["alerts"] if a["key"] == "cron.delivery_failed"
                 and not any(r["job"] == a["job"] for r in planted["alert_redelivered"]))
    planted["alerts"].append(dict(alert))
    problems, _ = scheduled_soak.analyse(planted)
    assert any(alert["job"] in p and "cron.delivery_failed alert(s)" in p and "expected" in p
               and "raised again" not in p for p in problems), problems
    # Recorded, so allowed -- but only when the second alert was raised.
    planted["alert_redelivered"].append({"row": 0, "job": alert["job"], "key": alert["key"], "at": ""})
    assert scheduled_soak.analyse(planted)[0] == []
    planted["alerts"].remove(alert)
    problems, _ = scheduled_soak.analyse(planted)
    assert any(alert["job"] in p and "raised again after a kill" in p for p in problems), problems


# ── once per local day, per series ───────────────────────────────────────


def _one_job_ledger(segments: list[dict], dues: list[str], *, until: str) -> dict:
    """A whole, healthy ledger of one New York daily job: *segments* (its
    timing between edits), a delivered fire at each of *dues*; the run ends
    at *until*."""
    end = scheduled_soak._dt(until).timestamp()
    fires, deliveries = [], {}
    for n, due in enumerate(dues, start=1):
        at = scheduled_soak._dt(due).timestamp() + 5
        fires.append({"job": "cron-0000000000aa", "due": due, "outcome": scheduled_soak.SUCCESS,
                      "status": "pending", "delivery": n, "failures": 0, "lateness": 5.0,
                      "claimed_at": at, "at": at, "seq": n})
        deliveries[str(n)] = {"state": "delivered", "accepted": 1, "attempts": 1, "job": "cron-0000000000aa",
                              "purpose": "result", "due": due, "created": at}
    return {
        "version": 1, "seed": 1, "t0": end - 5 * 86400, "run_end": end, "drain_end": end, "poll_s": 30.0,
        "turn_s": 20.0, "default_window_s": 3600, "budget": 3, "operator_zone": scheduled_soak.OPERATOR_ZONE,
        "change_at": "2026-11-01T06:00:00+00:00", "up": [[end - 5 * 86400, None]],
        "jobs": {"cron-0000000000aa": {"key": "j00001", "kind": "reminder", "catchup_s": 0,
                                       "segments": segments, "cancel_ok": []}},
        "fires": fires, "missed": [], "deliveries": deliveries, "alerts": [], "alert_redelivered": [],
        "visibility": [], "dead_loop": {}, "dead_loop_planned": False,
    }


def _daily(h: int, m: int, text: str, first: str, end: str | None) -> dict:
    return {"spec": {"type": "daily", "days": [], "h": h, "m": m, "zone": scheduled_soak.OPERATOR_ZONE,
                     "count": None, "until": None}, "text": text, "first": first, "end": end}


#: j04319 of the 28-day soak: daily at 07:49, fired 2026-11-02 07:49 local,
#: edited at 13:31 local that day to 20:37, fired 20:37 the same day.
_MOVED_LATER = [_daily(7, 49, "daily at 07:49", "2026-11-01T12:49:00+00:00", "2026-11-02T18:31:12+00:00"),
                _daily(20, 37, "every day at 20:37", "2026-11-03T01:37:00+00:00", None)]
_MOVED_LATER_DUES = ["2026-11-01T12:49:00+00:00", "2026-11-02T12:49:00+00:00", "2026-11-03T01:37:00+00:00"]


def test_an_edit_that_moves_a_daily_time_later_the_same_day_is_not_a_double_fire():
    ledger = _one_job_ledger(_MOVED_LATER, _MOVED_LATER_DUES, until="2026-11-03T12:00:00+00:00")
    assert scheduled_soak.analyse(ledger)[0] == []
    # What the check counted until 2026-10-15: every fire of the job by local
    # day, across its edits -- two on 2026-11-02, the false violation.
    zone = scheduled_soak.ZoneInfo(scheduled_soak.OPERATOR_ZONE)
    days = [scheduled_soak._dt(d).astimezone(zone).date().isoformat() for d in _MOVED_LATER_DUES]
    assert days.count("2026-11-02") == 2


def test_negative_control_a_series_that_fires_twice_in_one_day_is_still_caught():
    """Daily at 01:30 on the New York fall-back day: fired at 01:30 EDT and
    again at 01:30 EST -- one series, twice on one local day."""
    segments = [_daily(1, 30, "daily at 01:30", "2026-11-01T05:30:00+00:00", None)]
    ledger = _one_job_ledger(segments, ["2026-11-01T05:30:00+00:00", "2026-11-01T06:30:00+00:00"],
                             until="2026-11-01T20:00:00+00:00")
    problems, _ = scheduled_soak.analyse(ledger)
    assert any("fired 2 times on local day 2026-11-01" in p for p in problems), problems
    assert any("2026-11-01T06:30:00+00:00, a time it was not due" in p for p in problems), problems


def test_negative_control_the_old_series_firing_after_the_edit_is_still_caught():
    """Edited at 05:00 local, before that day's 07:49: the old series firing
    at 07:49 anyway, then the new one at 20:37 -- two fires, two series, and
    the first a time the job was no longer due."""
    segments = [_daily(7, 49, "daily at 07:49", "2026-11-01T12:49:00+00:00", "2026-11-02T10:00:00+00:00"),
                _daily(20, 37, "every day at 20:37", "2026-11-03T01:37:00+00:00", None)]
    ledger = _one_job_ledger(segments, _MOVED_LATER_DUES, until="2026-11-03T12:00:00+00:00")
    problems, _ = scheduled_soak.analyse(ledger)
    assert any("fired at 2026-11-02T12:49:00+00:00, a time it was not due" in p for p in problems), problems
    # It counts against the series in force at its time: twice on that day.
    assert any("fired 2 times on local day 2026-11-02" in p for p in problems), problems
