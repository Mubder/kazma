"""The report every soak harness writes (``scripts/soak_report.py``).

A soak runs for days and rewrites its report as it goes. On Windows a reader
holding the report open (an editor, a script reading it) refuses the rename
that replaces it; the Swarm soak's first trial found that the shared writer
then raised, which would have ended a multi-day run. The writer retries, and
a write that still fails leaves the previous report and never raises.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import soak_report  # noqa: E402

windows_only = pytest.mark.skipif(os.name != "nt", reason="only Windows refuses a rename over an open file")


def _hold_open(path: Path, seconds: float) -> threading.Thread:
    """A reader that keeps *path* open (Python's open shares no delete right)."""
    ready = threading.Event()

    def hold() -> None:
        with open(path, encoding="utf-8"):
            ready.set()
            time.sleep(seconds)

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    ready.wait(5)
    return thread


def test_a_report_is_replaced_whole(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    assert soak_report.write_report(target, {"status": soak_report.RUNNING, "n": 1})
    assert soak_report.write_report(target, soak_report.finished({"n": 2}, healthy=True))
    assert json.loads(target.read_text(encoding="utf-8")) == {
        "n": 2, "healthy": True, "status": "passed", "updated_at": json.loads(
            target.read_text(encoding="utf-8"))["updated_at"]}
    assert not (tmp_path / "report.json.tmp").exists()


def test_no_path_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "cwd"
    folder.mkdir()
    monkeypatch.chdir(folder)
    assert soak_report.write_report(None, {"n": 1}) is True
    assert soak_report.write_report("", {"n": 1}) is True
    assert list(folder.iterdir()) == []


@windows_only
def test_a_reader_holding_the_file_briefly_delays_the_write(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    soak_report.write_report(target, {"n": 1})
    reader = _hold_open(target, 0.4)
    assert soak_report.write_report(target, {"n": 2}) is True
    reader.join()
    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 2}


@windows_only
def test_a_reader_holding_it_past_the_retries_never_ends_the_soak(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(soak_report, "_REPLACE_RETRY_S", 0.2)
    target = tmp_path / "report.json"
    soak_report.write_report(target, {"n": 1})
    reader = _hold_open(target, 1.0)
    assert soak_report.write_report(target, {"n": 2}) is False  # answered, not raised
    reader.join()
    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 1}, "the previous report stays"
    assert "could not write" in capsys.readouterr().err
    assert soak_report.write_report(target, {"n": 3}) is True, "the next write catches up"


@windows_only
def test_negative_control_a_plain_rename_raises_while_a_reader_holds_the_file(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    target.write_text("{}", encoding="utf-8")
    tmp = tmp_path / "report.json.tmp"
    tmp.write_text('{"n": 2}', encoding="utf-8")
    reader = _hold_open(target, 0.5)
    with pytest.raises(PermissionError):
        os.replace(tmp, target)
    reader.join()


def test_a_running_report_names_its_process(tmp_path: Path) -> None:
    target = tmp_path / "report.json"
    soak_report.write_report(target, {"status": soak_report.RUNNING, "n": 1})
    running = json.loads(target.read_text(encoding="utf-8"))
    assert running["pid"] == os.getpid() and running["pid_started"] <= soak_report.utc_stamp()
    soak_report.write_report(target, soak_report.finished({"n": 2}, healthy=True))
    assert "pid" not in json.loads(target.read_text(encoding="utf-8")), "only a running report names it"


def test_a_soak_that_stopped_without_its_report_can_be_told_apart(tmp_path: Path) -> None:
    """The drill VM's case (2026-11-08): the soak's process ended with its
    report still "running". The pid it names is no running process now."""
    import subprocess

    import psutil

    target = tmp_path / "report.json"
    script = (
        "import sys; sys.path.insert(0, sys.argv[1]); import soak_report; "
        "soak_report.write_report(sys.argv[2], {'status': soak_report.RUNNING})"
    )
    scripts = str(Path(__file__).resolve().parents[1] / "scripts")
    subprocess.run([sys.executable, "-I", "-c", script, scripts, str(target)], check=True, timeout=60)
    report = json.loads(target.read_text(encoding="utf-8"))
    assert report["status"] == "running"
    pid = report["pid"]
    gone = not psutil.pid_exists(pid) or time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(psutil.Process(pid).create_time())) > report["pid_started"]
    assert gone, "the report's process ended, and the report says which it was"


def test_control_a_report_without_its_process_cannot_be_checked(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "report.json"
    monkeypatch.setattr(soak_report, "RUNNING", "no such status")  # the writer before 2026-11-08
    soak_report.write_report(target, {"status": "running"})
    assert "pid" not in json.loads(target.read_text(encoding="utf-8"))


def test_percentiles_and_the_latency_summary() -> None:
    values = [float(v) for v in range(1, 101)]
    assert soak_report.percentile([], 0.5) is None
    assert soak_report.percentile(values, 0.5) == 51.0
    summary = soak_report.latency_summary(values)
    assert summary["n"] == 100 and summary["max"] == 100.0 and summary["p99"] == 99.0
    assert soak_report.latency_summary([]) == {"p50": None, "p95": None, "p99": None, "max": None, "n": 0}
