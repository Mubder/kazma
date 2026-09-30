"""Kazma runs at the priority of an interactive program (2026-09-30).

The live KazmaAgent task was registered without a priority, so Task
Scheduler's default (7, "used for background tasks") started the guard below
normal, and a child inherits a below-normal class and its parent's memory
and I/O priority: the server ran below every normal program on the machine
(base priority 6, measured), held 4.7 GB with 60 MB of it resident, and froze
for 15-27 s whenever something heavy ran beside it. The server now raises
itself at boot, and says what it found.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE = REPO / "kazma-core" / "kazma_core" / "process_priority.py"

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows process priority")

#: A child that lowers its memory and I/O priority the way it would inherit
#: them from a guard a priority-7 task started (its CPU class comes from its
#: creation flags), then runs the module, loaded by path like the guard does.
_CHILD = r'''
import ctypes, importlib.util, sys
spec = importlib.util.spec_from_file_location("process_priority", sys.argv[1])
pp = importlib.util.module_from_spec(spec)
sys.modules["process_priority"] = pp
spec.loader.exec_module(pp)
_, wt, k, nt = pp._api()
me = k.GetCurrentProcess()
if sys.argv[2] == "lowered":
    low = wt.ULONG(2)
    assert k.SetProcessInformation(me, 0, ctypes.byref(low), 4)
    io = wt.ULONG(1)
    assert nt.NtSetInformationProcess(me, 33, ctypes.byref(io), 4) == 0
else:
    normal, io = wt.ULONG(5), wt.ULONG(2)
    assert k.SetProcessInformation(me, 0, ctypes.byref(normal), 4)
    assert nt.NtSetInformationProcess(me, 33, ctypes.byref(io), 4) == 0
r = pp.ensure_interactive_priority()
print("RESULT", r.before.get("class"), r.before.get("memory"), r.before.get("io"),
      r.after.get("class"), r.after.get("memory"), r.after.get("io"),
      ",".join(r.raised) or "-", "kept" if r.kept else "-", ";".join(r.errors) or "-")
'''

_BELOW, _NORMAL, _ABOVE = 0x4000, 0x20, 0x8000


def _run(flags: int, how: str, **env: str) -> list[str]:
    # The base interpreter, not the venv's launcher stub: the stub starts the
    # real interpreter as ITS child, and Windows hands down only idle and
    # below-normal classes (an above-normal child would read "normal").
    python = getattr(sys, "_base_executable", None) or sys.executable
    # The test runner sets KAZMA_PROCESS_PRIORITY=keep for itself; a child
    # here gets only what the test gives it.
    child_env = {k: v for k, v in os.environ.items() if k != "KAZMA_PROCESS_PRIORITY"}
    done = subprocess.run(
        [python, "-c", _CHILD, str(MODULE), how],
        env={**child_env, **env}, creationflags=flags,
        capture_output=True, text=True, timeout=60, check=False,
    )
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert done.returncode == 0 and line, done.stdout + done.stderr
    return line.split()[1:]


@windows
def test_a_process_started_like_a_background_task_is_raised() -> None:
    before_cls, before_mem, before_io, cls, mem, io, raised, kept, errors = _run(
        subprocess.BELOW_NORMAL_PRIORITY_CLASS, "lowered")
    assert (int(before_cls), before_mem, before_io) == (_BELOW, "2", "1"), "the instrument lowered it"
    assert (int(cls), mem, io) == (_NORMAL, "5", "2")
    assert raised == "CPU,memory,I/O" and kept == "-" and errors == "-"


@windows
def test_keep_leaves_the_process_as_it_was_started() -> None:
    """Negative control: with the switch the same child stays lowered."""
    *_, cls, mem, io, raised, kept, _errors = _run(
        subprocess.BELOW_NORMAL_PRIORITY_CLASS, "lowered", KAZMA_PROCESS_PRIORITY="keep")
    assert (int(cls), mem, io) == (_BELOW, "2", "1")
    assert raised == "-" and kept == "kept"


@windows
def test_a_higher_priority_is_never_lowered() -> None:
    before_cls, *_, cls, mem, io, raised, _kept, _errors = _run(
        subprocess.ABOVE_NORMAL_PRIORITY_CLASS, "normal")
    assert int(before_cls) == int(cls) == _ABOVE
    assert (mem, io, raised) == ("5", "2", "-")


def test_elsewhere_it_does_nothing(monkeypatch) -> None:
    from kazma_core import process_priority as pp

    monkeypatch.setattr(pp.sys, "platform", "linux")
    report = pp.ensure_interactive_priority()
    assert report.supported is False and report.raised == [] and report.errors == []
    assert pp._read_priority() == {}


def test_a_failure_is_reported_never_raised(monkeypatch) -> None:
    from kazma_core import process_priority as pp

    def broken():
        raise OSError("no kernel32 today")

    monkeypatch.setattr(pp, "_ensure", broken)
    report = pp.ensure_interactive_priority()
    assert report.errors == ["OSError: no kernel32 today"]


def test_the_module_is_standard_library_only() -> None:
    """The guard loads this file by path and must never import the app
    (AGENTS.md §33): a Kazma import here would put the app in the guard."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = {
        (node.module or "").split(".")[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported <= set(sys.stdlib_module_names) | {"__future__"}, imported


# ── the server says what it found ───────────────────────────────────────


def _adopt(monkeypatch, caplog, report) -> str:
    from kazma_core import path_refresh
    from kazma_core import process_priority as pp
    from kazma_ui.app import KazmaAppBuilder

    monkeypatch.setattr(path_refresh, "_os_path_settings", lambda: [])
    monkeypatch.setattr(pp, "ensure_interactive_priority", lambda: report)
    builder = KazmaAppBuilder()
    builder._env_files_loaded = []
    with caplog.at_level(logging.INFO, logger="kazma_ui.app"):
        builder._adopt_process_environment()
    return caplog.text


def test_the_server_raises_itself_at_boot_and_says_how_it_was_started(monkeypatch, caplog) -> None:
    from kazma_core.process_priority import PriorityReport

    text = _adopt(monkeypatch, caplog, PriorityReport(
        supported=True, before={"class": 0x4000, "memory": 2, "io": 1},
        after={"class": 0x20, "memory": 5, "io": 2}, raised=["CPU", "memory", "I/O"]))
    assert ("[startup] Raised the process to interactive priority (CPU, memory, I/O); "
            "it started at CPU below normal, memory 2, I/O 1.") in text
    assert "install_service.py --install" in text


def test_a_process_already_at_normal_priority_logs_nothing(monkeypatch, caplog) -> None:
    from kazma_core.process_priority import PriorityReport

    text = _adopt(monkeypatch, caplog, PriorityReport(
        supported=True, before={"class": 0x20, "memory": 5, "io": 2},
        after={"class": 0x20, "memory": 5, "io": 2}))
    assert "priority" not in text.lower()


def test_a_priority_that_could_not_be_raised_is_a_warning(monkeypatch, caplog) -> None:
    from kazma_core.process_priority import PriorityReport

    _adopt(monkeypatch, caplog, PriorityReport(supported=True, errors=["SetPriorityClass: error 5"]))
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warned == ["[startup] Could not raise the process priority: SetPriorityClass: error 5"]


def test_the_test_runner_keeps_its_own_lowered_priority() -> None:
    """A test that builds the app would otherwise raise the runner's lowered
    process back to normal for the rest of its chunk."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("fast_test_mod", REPO / "scripts" / "fast_test.py")
    runner = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(runner)
    assert 'os.environ["KAZMA_PROCESS_PRIORITY"] = "keep"' in runner._BACKGROUND_BOOTSTRAP
