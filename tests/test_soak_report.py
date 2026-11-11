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
import subprocess
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


# ── a process's own peak memory (CI, 2026-11-10) ─────────────────────────

#: A parent that holds this much, then starts a child that measures itself.
_PARENT_MIB = 256

_PARENT = r"""
import json, subprocess, sys
held = bytearray({mib} * 2**20)
for i in range(0, len(held), 4096):
    held[i] = 1  # resident, not only reserved
child = '''
import json, os, sys
sys.path.insert(0, os.environ["KAZMA_TEST_SOAK_SCRIPTS"])  # a path in source breaks on Windows' backslashes
import soak_report
try:
    import resource
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
except ImportError:
    maxrss = None
print(json.dumps({{"peak": soak_report.peak_rss_bytes(), "ru_maxrss": maxrss}}))
'''
out = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True, check=True).stdout
print(out.strip().splitlines()[-1])
"""


def _child_started_by_a_big_parent() -> dict:
    script = _PARENT.format(mib=_PARENT_MIB)
    env = {**os.environ, "KAZMA_TEST_SOAK_SCRIPTS": str(Path(soak_report.__file__).resolve().parent)}
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True,
                         timeout=120, env=env).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_a_child_reports_its_own_peak_not_its_parents() -> None:
    """A soak's cycle child is started by the harness (a pytest process of a
    gigabyte in a CI chunk). Its peak is its own: the Time Travel soak's
    first cycle read "RSS grew 964 MiB" on Linux CI from the parent's size."""
    measured = _child_started_by_a_big_parent()
    assert measured["peak"] and measured["peak"] < 128 * 2**20, measured


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the inherited high-water mark is Linux's")
def test_control_ru_maxrss_carries_the_parents_size_into_the_child() -> None:
    """Negative control: what the soaks read before -- ``ru_maxrss`` (kB on
    Linux) -- holds the parent's resident size from before the child's exec."""
    measured = _child_started_by_a_big_parent()
    assert measured["ru_maxrss"] * 1024 >= (_PARENT_MIB - 16) * 2**20, measured


def _ru_maxrss_readers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob("*.py")
                  if p.name != "soak_report.py" and "ru_maxrss" in p.read_text(encoding="utf-8"))


def test_no_soak_reads_ru_maxrss_but_the_shared_helper(tmp_path: Path) -> None:
    """The class gate: every peak a soak reports comes from
    :func:`soak_report.peak_rss_bytes`, never ``ru_maxrss`` read in place
    (the Time Travel, Knowledge and Memory soaks each read it)."""
    assert _ru_maxrss_readers(Path(soak_report.__file__).resolve().parent) == []
    # Negative control: a planted reader is found.
    (tmp_path / "planted_soak.py").write_text(
        "import resource\npeak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss\n", encoding="utf-8")
    assert _ru_maxrss_readers(tmp_path) == ["planted_soak.py"]
