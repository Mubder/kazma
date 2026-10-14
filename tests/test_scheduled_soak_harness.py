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
  twice, an occurrence that never fired and was not recorded missed, and a
  non-delivered job nobody was told about. These are the negative controls.
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
    assert report["alerts"]["total"] == report["alerts_expected"]
    # A broken series ran out of its failure budget and stopped, with its alert.
    assert any(f["status"] == "failed" and f["failures"] >= ledger["budget"] for f in ledger["fires"])
    # Restarts of every kind, and the long downtime missed jobs with one notice each.
    restarts = report["restarts"]["by_kind"]
    for kind in ("graceful", "abrupt_mid_turn", "abrupt_mid_send", "gap", "after_dead_loop"):
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
