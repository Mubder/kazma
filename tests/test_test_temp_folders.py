"""A test run removes the temp folders of runs that have ended (2026-11-14).

Every test process makes five folders (the root conftest: log, data, home,
skills, workspace) and nothing removed them: the temp folder held about
1,600 of each, 2.3 GB, from five days of runs. They now carry their process
id, and a run removes those whose process has ended. A folder whose process
still runs is kept (a soak under pytest runs for days). The runner
(scripts/fast_test.py) does the same with its own run folders.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRIVIAL = "tests/test_real_docker_marker.py::test_an_unmarked_test_gets_an_empty_docker_config"


def _root_conftest(config):
    """The root conftest's module, from pytest's own list of the conftests it
    loaded. ``sys.modules["conftest"]`` is whichever conftest was imported
    last: in a CI batch holding a package suite (kazma-core/tests has its
    own conftest.py) it was not the root one, and the lookup found nothing."""
    return next(
        plugin for plugin in config.pluginmanager.get_plugins()
        if callable(getattr(plugin, "_remove_ended_test_temp", None))
    )


def _ended_pid() -> int:
    proc = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    return int(proc.stdout.strip())


def test_only_the_folders_of_ended_processes_are_removed(tmp_path, monkeypatch, request):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    ended, live = _ended_pid(), os.getppid()
    names = {
        f"kazma-test-data-{ended}-abc": False,      # its process ended: removed
        f"kazma-test-log-{ended}-def": False,
        f"kazma-test-home-{live}-ghi": True,        # still running: kept
        f"kazma-test-workspace-{os.getpid()}-jk": True,  # this run's own
        "kazma-test-data-oldname": True,            # no process id: not judged
        f"kazma-other-{ended}-x": True,             # not a test folder
    }
    for name in names:
        (tmp_path / name).mkdir()
        (tmp_path / name / "f.txt").write_text("x", encoding="utf-8")
    assert _root_conftest(request.config)._remove_ended_test_temp() == 2
    assert {n: (tmp_path / n).exists() for n in names} == names


def _run_twice(tmp_path, **env) -> list[str]:
    temp = tmp_path / "temp"
    temp.mkdir()
    child_env = {**os.environ, "TMP": str(temp), "TEMP": str(temp), "TMPDIR": str(temp),
                 "KAZMA_TEST_PREIMPORT": "0", **env}
    for name in ("KAZMA_LOG_FILE", "KAZMA_DATA_DIR", "KAZMA_USER_HOME", "KAZMA_SKILLS_HOME", "KAZMA_WORKSPACE"):
        child_env.pop(name, None)
    for _ in range(2):
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", TRIVIAL],
                              cwd=str(REPO), env=child_env, capture_output=True, text=True, timeout=300)
        assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    return sorted(p.name for p in temp.iterdir() if p.name.startswith("kazma-test-"))


def test_two_runs_leave_one_runs_folders(tmp_path):
    left = _run_twice(tmp_path)
    assert len(left) == 5, left
    assert len({name.split("-")[-2] for name in left}) == 1, left  # one process id


def test_control_kept_they_pile_up(tmp_path):
    """Negative control: with the sweep off both runs' folders stay."""
    assert len(_run_twice(tmp_path, KAZMA_TEST_KEEP_TEMP="1")) == 10


def test_the_runner_removes_only_ended_runs_folders(tmp_path, monkeypatch):
    import tempfile

    spec = importlib.util.spec_from_file_location("fast_test_sweep", REPO / "scripts" / "fast_test.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    ended, live = _ended_pid(), os.getppid()
    names = {f"kazma-pytest-run-{ended}-a": False, f"kazma-pytest-run-{live}-b": True,
             "kazma-pytest-run-oldname": True}
    for name in names:
        (tmp_path / name / "t").mkdir(parents=True)
    assert runner.remove_ended_run_folders() == 1
    assert {n: (tmp_path / n).exists() for n in names} == names
