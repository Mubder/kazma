"""The Windows task ``--install`` registers, and what ``--status`` says about it.

Live 2026-09-30: KazmaAgent had been registered before the installer set a
priority, so the guard started at Task Scheduler's background priority 7.
Only ``--install`` run again from an elevated shell changes an existing task,
and nothing told the owner whether it had: ``--status`` printed the task's
name and state. Reading the installer for the answer found two ways a rerun
made things worse without a word:

* from a shell without admin rights it fell back to registering the
  user-level task with ``-Force`` over the one that starts at boot;
* from a second checkout on the same machine (the dev repo beside the live
  install) it moved the live task onto that checkout.

Both now refuse and say what to run instead, and ``--status`` compares every
setting ``--install`` registers with the registered task.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

_SERVICE_DIR = Path(__file__).resolve().parents[1] / "scripts" / "service"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_svc_install_service_task", _SERVICE_DIR / "install_service.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


installer = _load()

# 0x800710E0: what Task Scheduler records when the 5-minute trigger fires
# while the guard runs and IgnoreNew refuses the second instance.
_REFUSED_WHILE_RUNNING = 2147946720


def _task(**over) -> dict:
    """A task as ``registered_task`` reads it -- the live one's shape, with
    everything ``--install`` registers from this folder."""
    task = {
        "registered": True,
        "state": "Running",
        "user": "operator",
        "logon": "S4U",
        "level": "Highest",
        "priority": installer.TASK_PRIORITY,
        "instances": "IgnoreNew",
        "execute": installer.python_exe(),
        "arguments": f'"{installer.GUARD}"',
        "triggers": [
            {"kind": "MSFT_TaskBootTrigger", "every": "", "enabled": True},
            {"kind": "MSFT_TaskLogonTrigger", "every": "", "enabled": True},
            {"kind": "MSFT_TaskTimeTrigger", "every": "PT5M", "enabled": True},
        ],
        "last_run": "2026-09-30 23:39:24",
        "last_result": _REFUSED_WHILE_RUNNING,
    }
    task.update(over)
    return task


def _without(kind: str) -> list[dict]:
    return [t for t in _task()["triggers"] if t["kind"] != kind]


def _fixes(task: dict) -> list[str]:
    return [label for label, ok, _text in installer.task_checks(task) if not ok]


# -- --status --------------------------------------------------------------


def test_a_task_registered_by_install_matches():
    report, problems = installer.format_task_status(_task())
    assert problems == 0, report
    assert "The task matches what --install registers." in report
    assert "FIX" not in report


def test_the_live_task_is_told_its_priority():
    """The live task on 2026-09-30: everything but the priority."""
    report, problems = installer.format_task_status(_task(priority=7))
    assert problems == 1, report
    fix = [line for line in report.splitlines() if line.lstrip().startswith("FIX")]
    assert len(fix) == 1 and "priority" in fix[0], report
    assert "7, background" in fix[0]
    assert f"--install registers {installer.TASK_PRIORITY}" in fix[0]
    assert "1 setting differs from what --install registers." in report
    assert "Run as administrator" in report
    assert report.rstrip().endswith("install_service.py' --install"), report


@pytest.mark.parametrize("label, change", [
    ("state", {"state": "Disabled"}),
    ("account", {"logon": "Interactive", "level": "Limited"}),
    ("account", {"level": "Limited"}),
    ("triggers", {"triggers": _without("MSFT_TaskBootTrigger")}),
    ("triggers", {"triggers": _without("MSFT_TaskLogonTrigger")}),
    ("triggers", {"triggers": _without("MSFT_TaskTimeTrigger")}),
    ("triggers", {"triggers": _without("MSFT_TaskTimeTrigger")
                  + [{"kind": "MSFT_TaskTimeTrigger", "every": "PT1H", "enabled": True}]}),
    ("triggers", {"triggers": [{**t, "enabled": t["kind"] != "MSFT_TaskBootTrigger"}
                               for t in _task()["triggers"]]}),
    ("if running", {"instances": "Parallel"}),
    ("if running", {"instances": "Queue"}),
    ("if running", {"instances": "StopExisting"}),
    ("priority", {"priority": 7}),
    ("priority", {"priority": 5}),
    ("priority", {"priority": None}),
    ("runs", {"execute": r"C:\Python311\python.exe"}),
    ("runs", {"arguments": r'"D:\other\scripts\service\kazma_guard.py"'}),
])
def test_each_setting_that_differs_is_named(label, change):
    """Every check can fail, and fails alone: the negative controls of the
    test above."""
    assert _fixes(_task(**change)) == [label]


def test_a_password_task_also_starts_at_boot():
    """"Run whether the user is logged on or not" with a stored password is
    Password, not S4U; it starts at boot all the same."""
    assert _fixes(_task(logon="Password")) == []


@pytest.mark.parametrize("code, words", [
    (_REFUSED_WHILE_RUNNING, "0x800710E0: a trigger fired while the guard was running"),
    (0, "0x0: completed"),
    (1, "0x1: the guard exited with code 1"),
    (0x41306, "0x41306: ended from Task Scheduler (End)"),
    (0x8004131F, "already running"),
    (0xDEAD, "0xDEAD"),
    (None, "unknown"),
])
def test_last_run_results_are_explained(code, words):
    assert words in installer.explain_task_result(code)


def test_a_task_that_never_ran_shows_no_last_run():
    report, _ = installer.format_task_status(
        _task(last_run="1999-11-30 00:00:00", last_result=0x41303))
    assert "last run" not in report


def test_status_exit_code_says_whether_the_task_matches(monkeypatch, capsys):
    monkeypatch.setattr(installer, "registered_task", lambda name=None: _task())
    assert installer.do_status("windows") == 0
    monkeypatch.setattr(installer, "registered_task", lambda name=None: _task(priority=7))
    assert installer.do_status("windows") == 1
    monkeypatch.setattr(installer, "registered_task", lambda name=None: {"registered": False})
    assert installer.do_status("windows") == 1
    assert "is not registered" in capsys.readouterr().out
    monkeypatch.setattr(installer, "registered_task",
                        lambda name=None: {"registered": None, "error": "Access is denied."})
    assert installer.do_status("windows") == 1
    assert "could not read task 'KazmaAgent': Access is denied." in capsys.readouterr().out


def test_status_of_another_folders_task_points_at_that_folder():
    """Run from a second checkout, the fix is that folder's installer --
    this folder's would move the task here."""
    other = r"D:\kazma-live"
    report, problems = installer.format_task_status(_task(
        priority=7,
        execute=other + r"\.venv\Scripts\python.exe",
        arguments=f'"{other}\\scripts\\service\\kazma_guard.py"'))
    assert problems == 2, report
    assert f"starts the guard in {other}, not in this folder" in report
    assert (f"& '{other}\\.venv\\Scripts\\python.exe' "
            f"'{other}\\scripts\\service\\install_service.py' --install") in report
    assert "--install --move" in report


def test_the_task_script_uses_the_constants_the_status_checks():
    for elevated in (True, False):
        ps1 = installer.windows_task_ps1(elevated=elevated)
        assert f"-Priority {installer.TASK_PRIORITY}" in ps1
        assert f"-RepetitionInterval (New-TimeSpan -Minutes {installer.KEEP_ALIVE_MINUTES})" in ps1


# -- --install never replaces a task for the worse ---------------------------


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """This folder is a temporary one; ``_run`` (the registration) records."""
    (tmp_path / "scripts" / "service").mkdir(parents=True)
    monkeypatch.setattr(installer, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(installer, "GUARD", tmp_path / "scripts" / "service" / "kazma_guard.py")
    calls: list[list[str]] = []

    def _run(cmd):
        calls.append(cmd)
        return 0, "Registered scheduled task 'KazmaAgent'."

    monkeypatch.setattr(installer, "_run", _run)
    return calls


def _here(**over) -> dict:
    return _task(execute=installer.python_exe(), arguments=f'"{installer.GUARD}"', **over)


def _registers(monkeypatch, *tasks: dict):
    seq = list(tasks)
    monkeypatch.setattr(installer, "registered_task",
                        lambda name=None: seq.pop(0) if len(seq) > 1 else seq[0])


def test_without_admin_rights_a_task_that_starts_at_boot_is_never_replaced(
        folder, monkeypatch, capsys):
    monkeypatch.setattr(installer, "_is_elevated", lambda: False)
    _registers(monkeypatch, _here(priority=7))
    assert installer.install_windows() == 1
    assert folder == [], "nothing may be registered"
    err = capsys.readouterr().err
    assert "is registered to start at boot (operator, S4U, Highest)" in err
    assert "Nothing was changed." in err
    assert "Run as administrator" in err and "install_service.py' --install" in err


def test_without_admin_rights_and_no_task_the_user_level_task_is_registered(
        folder, monkeypatch):
    """The negative control: the fallback still works where it is the best
    there is."""
    monkeypatch.setattr(installer, "_is_elevated", lambda: False)
    _registers(monkeypatch, {"registered": False})
    assert installer.install_windows() == 0
    assert len(folder) == 1 and folder[0][-1].endswith("install_windows_task_userlevel.ps1")


def test_without_admin_rights_a_user_level_task_is_refreshed(folder, monkeypatch):
    monkeypatch.setattr(installer, "_is_elevated", lambda: False)
    _registers(monkeypatch, _here(logon="Interactive", level="Limited"))
    assert installer.install_windows() == 0
    assert len(folder) == 1 and folder[0][-1].endswith("install_windows_task_userlevel.ps1")


def test_a_task_that_could_not_be_read_is_not_replaced(folder, monkeypatch, capsys):
    """An unknown task is not a missing one."""
    monkeypatch.setattr(installer, "_is_elevated", lambda: False)
    _registers(monkeypatch, {"registered": None, "error": "Access is denied."})
    assert installer.install_windows() == 1
    assert folder == []
    assert "Could not read the registered task 'KazmaAgent' (Access is denied.)" in \
        capsys.readouterr().err


@pytest.mark.parametrize("elevated", [True, False])
def test_another_folders_task_is_not_moved_here_without_move(folder, monkeypatch, capsys,
                                                             elevated):
    monkeypatch.setattr(installer, "_is_elevated", lambda: elevated)
    other = r"D:\kazma-live"
    _registers(monkeypatch, _task(execute=other + r"\.venv\Scripts\python.exe",
                                  arguments=f'"{other}\\scripts\\service\\kazma_guard.py"'))
    assert installer.install_windows() == 1
    assert folder == []
    err = capsys.readouterr().err
    assert f"starts the guard in {other}, not in this folder" in err
    assert f"'{other}\\scripts\\service\\install_service.py' --install" in err


def test_move_moves_another_folders_task_here(folder, monkeypatch, capsys):
    monkeypatch.setattr(installer, "_is_elevated", lambda: True)
    other = r"D:\kazma-live"
    _registers(monkeypatch,
               _task(execute=other + r"\.venv\Scripts\python.exe",
                     arguments=f'"{other}\\scripts\\service\\kazma_guard.py"'),
               _here())
    assert installer.install_windows(move=True) == 0
    assert len(folder) == 1 and folder[0][-1].endswith("install_windows_task.ps1")
    assert "The task matches what --install registers." in capsys.readouterr().out


def test_an_elevated_install_updates_this_folders_task_and_shows_it(folder, monkeypatch, capsys):
    """What the owner runs to apply the priority: it registers, then reads
    the task back and shows every setting."""
    monkeypatch.setattr(installer, "_is_elevated", lambda: True)
    _registers(monkeypatch, _here(priority=7), _here())
    assert installer.install_windows() == 0
    assert len(folder) == 1 and folder[0][-1].endswith("install_windows_task.ps1")
    out = capsys.readouterr().out
    assert f"ok   priority    {installer.TASK_PRIORITY}, an interactive program's" in out
    assert "The task matches what --install registers." in out


def test_move_without_install_is_refused(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["install_service.py", "--status", "--move"])
    with pytest.raises(SystemExit) as exc:
        installer.main()
    assert exc.value.code == 2


# -- the query itself, on Windows --------------------------------------------

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="reads Task Scheduler")


@windows_only
def test_the_query_says_when_there_is_no_such_task():
    assert installer.registered_task(f"KazmaNoSuchTask-{uuid.uuid4().hex[:8]}") == \
        {"registered": False}


@windows_only
def test_the_query_reads_a_registered_task():
    """Read-only: any task in the root folder whose name is plain letters."""
    names = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-ScheduledTask -TaskPath '\\' | ForEach-Object { $_.TaskName }"],
        capture_output=True, text=True, timeout=60, check=False,
    ).stdout.splitlines()
    # A name is a LIKE pattern to the query: plain letters, digits, dashes only.
    plain = [n.strip() for n in names if n.strip().replace("-", "").isalnum()]
    if not plain:
        pytest.skip("no readable task in the root folder")
    name = installer.TASK_NAME if installer.TASK_NAME in plain else plain[0]
    task = installer.registered_task(name)
    if task.get("registered") is None and "denied" in str(task.get("error", "")).lower():
        pytest.skip(f"{name} is not readable from this shell")
    assert task["registered"] is True, task
    assert isinstance(task["priority"], int) and 0 <= task["priority"] <= 10
    assert isinstance(task["triggers"], list)
    for key in ("state", "user", "logon", "level", "instances", "execute", "last_run"):
        assert isinstance(task[key], str), key
    assert installer.task_checks(task), "the checks run on a real task"
