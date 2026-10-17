"""The memory soak harness works, and sees a loss (Memory audit Stage 4).

``scripts/memory_soak.py`` is the harness for the multi-day soak with
restarts the audit asks for; a session cannot hold that run, so this keeps
the harness honest: a short run with killed cycles, backups and restores
from older backups ends healthy, having done the work it reports; and its
check reports a memory taken from the store (negative control).
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import memory_soak  # noqa: E402

pytestmark = pytest.mark.timeout(300)

#: Each status a reader of the report file saw, in order, during the run.
_WRITTEN: list[str] = []


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    data = tmp_path_factory.mktemp("soak") / "data"
    report = tmp_path_factory.mktemp("soak-report") / "report.json"
    real_write = memory_soak._write_report

    def record(path, summary):
        real_write(path, summary)
        # What a reader of the file sees at that moment, not what was passed.
        _WRITTEN.append(json.loads(Path(path).read_text(encoding="utf-8"))["status"])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(memory_soak, "_write_report", record)
        code = memory_soak.main([
            "--duration", "240", "--cycle-seconds", "0", "--max-cycles", "10", "--backup-every", "2",
            "--restore-every", "3", "--crash-rate", "0.25", "--keep-out-rate", "0.2", "--seed", "7",
            "--data-dir", str(data), "--report", str(report),
        ])
    return code, json.loads(report.read_text(encoding="utf-8")), data


def test_a_short_soak_with_crashes_and_restores_is_healthy(run):
    code, report, _ = run
    assert code == 0 and report["healthy"], report["violations"]
    assert report["cycles"] == 10
    assert report["crashes"] >= 1 and report["restores"] >= 1 and report["backups"] >= 1, report
    held = report["held"]
    assert held["chat_turns"] > 10 and held["forgotten_turns"] >= 1 and held["facts"] >= 1, held
    assert report["recall_ms"]["n"] >= 10
    assert report["status"] == "passed"


def test_the_report_is_on_disk_while_the_soak_runs(run):
    """A multi-day run a reboot ends early must still leave its evidence: the
    report is rewritten, whole, after every cycle, then marked finished."""
    assert _WRITTEN.count("running") >= 10, _WRITTEN
    assert _WRITTEN[-1] == "passed" and "passed" not in _WRITTEN[:-1], _WRITTEN


def test_the_check_sees_a_memory_taken_from_the_store(run):
    """Negative control: the same stores with one remembered turn deleted."""
    _, _, data = run
    assert memory_soak._check(data, {}) == []
    conn = sqlite3.connect(str(data / "memory_state.db"))
    try:
        eid = conn.execute("SELECT id FROM episodes WHERE tier != 'forgotten' AND session_id LIKE 'soak-%' "
                           "LIMIT 1").fetchone()[0]
        conn.execute("DELETE FROM episodes WHERE id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()
    assert any(p.startswith("lost:") for p in memory_soak._check(data, {}))


def test_a_restore_left_unfinished_is_rebuilt_before_the_check():
    """2026-10-16: a restore's rebuild ran out of its budget under load
    (``more``), the check ran at once and called 799 turns lost."""
    passes = iter([{"turns_written": 300, "done": False}, {"turns_written": 499, "done": True}])
    done = memory_soak._finish_rebuild({"more": True}, lambda: next(passes))
    assert done["finished"] and done["passes"] == 2 and done["turns_written"] == 799


def test_a_finished_restore_runs_no_extra_pass():
    def boom():
        raise AssertionError("no pass needed")

    assert memory_soak._finish_rebuild({"more": False}, boom) == {
        "passes": 0, "turns_written": 0, "seconds": 0.0, "finished": True}


def test_a_rebuild_that_never_ends_is_reported_unfinished():
    """Negative control: the bound holds, and says so (never a silent pass)."""
    done = memory_soak._finish_rebuild({"more": True}, lambda: {"done": False}, max_passes=3)
    assert done["passes"] == 3 and not done["finished"]


def test_the_short_soak_recorded_each_restores_rebuild(run):
    _code, report, _data = run
    assert report["restore_rebuild_seconds"]["n"] == report["restores"] > 0
