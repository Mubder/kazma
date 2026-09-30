"""The chunk runner must isolate a hang in two runs, not a hundred and sixty.

`--timeout-method=thread` kills the process when a test hangs, so the chunk's
tally dies with it. The runner then re-ran every one of that chunk's ~160 files
in its own process to find the culprit — and on CI the culprit is usually the
FOURTH file, so ~156 of those runs were pure waste. That doubled the job's wall
clock (1,034s green versus 1,693s when a chunk died) on every run where a chunk
hung, which was most of them.

pytest -q prints a progress line per file before it dies, so the culprit is
already in the output nobody was reading. `last_file_reached` recovers it; the
caller then re-runs the chunk MINUS that file as one process, plus the file
alone.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _runner():
    spec = importlib.util.spec_from_file_location(
        "fast_test_mod", REPO / "scripts" / "fast_test.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _FakeProc:
    """A finished pytest process, for tests that intercept ``_start``."""

    returncode = 0

    def communicate(self, timeout=None):
        return "1 passed in 0.1s", ""


class _FakeTree:
    def kill(self) -> None:
        pass

    def close(self) -> None:
        pass


#: The exact shape CI produced on 2026-09-20, trimmed.
_KILLED_CHUNK_LOG = """============================= test session starts ==============================
collected 2252 items / 2 skipped
kazma-core/kazma_core_tests/integration/test_multi_platform.py ......... [  0%]
kazma-core/kazma_core_tests/unit/test_reliability.py ................... [  1%]
kazma-core/tests/test_empty_answer_recovery.py ....                      [  2%]
kazma-core/tests/test_github_app_integration.py ....+++++++++ Timeout ++++++++++
~~~~~~~~~~~ Stack of MainThread (140366038940544) ~~~~~~~~~~~
  File "/x/_pytest/runner.py", line 184, in pytest_runtest_call
"""


def test_the_culprit_is_recovered_from_a_killed_chunk() -> None:
    """The hung file is named in the output; find it there."""
    mod = _runner()
    assert (
        mod.last_file_reached(_KILLED_CHUNK_LOG)
        == "kazma-core/tests/test_github_app_integration.py"
    )


def test_no_progress_lines_is_not_a_crash() -> None:
    """A chunk that printed nothing must yield None, not raise or guess."""
    mod = _runner()
    assert mod.last_file_reached("") is None
    assert mod.last_file_reached("collected 0 items\n") is None


def test_a_clean_run_names_its_last_file() -> None:
    """On a healthy chunk the last file is simply the last one printed."""
    mod = _runner()
    log = (
        "tests/test_a.py ....   [ 30%]\n"
        "tests/test_b.py ..     [ 70%]\n"
        "tests/test_c.py .      [100%]\n"
        "===== 7 passed in 1.20s =====\n"
    )
    assert mod.last_file_reached(log) == "tests/test_c.py"


@pytest.mark.parametrize(
    "line,expected",
    [
        ("kazma-ui/kazma_ui_tests/test_x.py ..  [ 5%]", "kazma-ui/kazma_ui_tests/test_x.py"),
        ("tests/e2e/test_smoke.py s            [ 9%]", "tests/e2e/test_smoke.py"),
    ],
)
def test_progress_line_shapes(line: str, expected: str) -> None:
    """Both nested package tests and skipped-first files parse."""
    assert _runner().last_file_reached(line + "\n") == expected


@pytest.mark.parametrize(
    ("cpus", "expected"),
    [(32, 8), (16, 8), (8, 8), (4, 4), (1, 2), (None, 4)],
)
def test_the_default_chunk_count_is_capped(cpus, expected) -> None:
    """One chunk per CPU started 32 torch-loading pytest processes on a
    32-thread box: 12 died in torch's embedding and the run took 23 minutes
    (2026-09-25). README's plain `python scripts/fast_test.py` must be safe."""
    assert _runner().default_chunk_count(cpus) == expected


def test_the_uncapped_default_would_start_one_chunk_per_cpu() -> None:
    """Negative control: the old default on the same box."""

    def old_default(cpus):
        return max(2, (cpus or 4))

    assert old_default(32) == 32 != _runner().default_chunk_count(32)


# ── a run never starves the server on the same machine ──────────────────

#: A test that prints the priority of the pytest process running it: on
#: Windows the CPU class, the memory priority (5 normal, 2 low) and the I/O
#: priority (2 normal, 1 low); elsewhere the nice value.
_PRIORITY_PROBE = '''
import os


def _priority():
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        nt = ctypes.WinDLL("ntdll")
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.GetPriorityClass.argtypes = [wintypes.HANDLE]
        k.GetProcessInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        nt.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p]
        me = k.GetCurrentProcess()
        memory, io = wintypes.ULONG(0), wintypes.ULONG(0)
        assert k.GetProcessInformation(me, 0, ctypes.byref(memory), 4)
        assert nt.NtQueryInformationProcess(me, 33, ctypes.byref(io), 4, None) == 0
        return hex(k.GetPriorityClass(me)), memory.value, io.value
    return "nice", os.nice(0), 0


def test_report_priority():
    print("PRIORITY", *_priority())
'''

#: Starts the command it is given from a parent at normal priority. A child
#: inherits its parent's memory and I/O priority (measured: even when it is
#: created at the NORMAL class), so under the runner itself -- where this test
#: runs lowered -- the old command would report the runner's priority, and
#: the negative control would prove nothing.
_NORMAL_PARENT = r'''
import os, subprocess, sys
if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32"); nt = ctypes.WinDLL("ntdll")
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    nt.NtSetInformationProcess.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG]
    me = k.GetCurrentProcess()
    memory, io = wintypes.ULONG(5), wintypes.ULONG(2)
    assert k.SetPriorityClass(me, 0x20)
    assert k.SetProcessInformation(me, 0, ctypes.byref(memory), 4)
    assert nt.NtSetInformationProcess(me, 33, ctypes.byref(io), 4) == 0
sys.exit(subprocess.run(sys.argv[1:]).returncode)
'''


def _priority_of(cmd: list[str], cwd: Path) -> tuple[str, int, int]:
    import re
    import subprocess
    import sys

    python = getattr(sys, "_base_executable", None) or sys.executable
    done = subprocess.run([python, "-c", _NORMAL_PARENT, *cmd, "-s"], cwd=str(cwd),
                          capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, check=False)
    found = re.search(r"PRIORITY (\S+) (\d+) (\d+)", done.stdout)
    assert done.returncode == 0 and found, done.stdout + done.stderr
    return found.group(1), int(found.group(2)), int(found.group(3))


def test_a_chunk_runs_below_a_normal_program(tmp_path) -> None:
    """2026-09-29: two minutes into a 4-chunk run on the machine that also
    runs the live install, the server's event loop froze for 18.5 s. Every
    pytest process the runner starts now lowers itself before pytest imports
    anything: CPU below normal, memory and I/O priority low."""
    probe = tmp_path / "test_priority_probe.py"
    probe.write_text(_PRIORITY_PROBE, encoding="utf-8")
    runner = _runner()

    lowered = _priority_of(runner.pytest_command([str(probe)], background=True), tmp_path)
    # Negative control: the old command, from the same normal parent.
    normal = _priority_of(runner.pytest_command([str(probe)], background=False), tmp_path)

    if lowered[0] == "nice":
        assert lowered[1] > normal[1] or lowered[1] == 19, (lowered, normal)
    else:
        assert lowered == ("0x4000", 2, 2), lowered     # BELOW_NORMAL, memory low, I/O as it was
        assert normal == ("0x20", 5, 2), normal         # NORMAL, memory normal, I/O normal


def test_the_lowered_priority_does_not_slow_the_tests() -> None:
    """Measured on 476 SQLite-heavy tests, one process each (2026-09-30):
    normal 87 s, CPU + memory lowered 78-80 s, low I/O priority as well
    147-199 s -- and a full run with it had every chunk hit its timeout.
    Windows' background mode was worse: ``import torch`` took 244 s, not 4.4.
    The bootstrap touches neither."""
    bootstrap = _runner()._BACKGROUND_BOOTSTRAP
    assert "0x00100000" not in bootstrap and "0x100000" not in bootstrap, "no background mode"
    assert "NtSetInformationProcess" not in bootstrap, "no I/O priority change"
    assert "0x4000" in bootstrap and "SetProcessInformation(me, 0," in bootstrap


def test_each_pytest_process_gets_its_share_of_math_threads(monkeypatch) -> None:
    """torch, MKL and OpenBLAS start a thread per CPU in every process: four
    chunks on 32 CPUs would ask for 128. Each gets the CPUs over the chunks,
    unless the caller set a cap."""
    runner = _runner()
    seen: list[dict] = []

    def fake_start(cmd, env):
        seen.append(env or {})
        return _FakeProc(), _FakeTree()

    monkeypatch.setattr(runner, "_start", fake_start)
    monkeypatch.setattr(runner, "THREADS_PER_PROCESS", 8)
    for var in runner._THREAD_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("MKL_NUM_THREADS", "2")          # the caller's cap wins
    runner.run_pytest(["tests/test_x.py"], 10)
    env = seen[0]
    assert (env["OMP_NUM_THREADS"], env["MKL_NUM_THREADS"], env["OPENBLAS_NUM_THREADS"]) == ("8", "2", "8")

    # Negative control: without a share (the old runner), nothing is capped.
    monkeypatch.setattr(runner, "THREADS_PER_PROCESS", None)
    runner.run_pytest(["tests/test_x.py"], 10)
    assert "OMP_NUM_THREADS" not in seen[1]


def test_the_runner_starts_every_pytest_through_the_command_builder(monkeypatch) -> None:
    """Every run -- chunks and the per-file retries -- goes through
    ``run_pytest``, which builds its command with ``pytest_command``."""
    import ast

    runner = _runner()
    seen: list[list[str]] = []

    def fake_start(cmd, env):
        seen.append(cmd)
        return _FakeProc(), _FakeTree()

    monkeypatch.setattr(runner, "_start", fake_start)
    runner.run_pytest(["tests/test_x.py"], 10)
    assert seen == [runner.pytest_command(["tests/test_x.py"])]
    assert seen[0][1:3] == ["-c", runner._BACKGROUND_BOOTSTRAP], "background is the default"

    # One process launch in the whole runner, inside ``_start`` (which only
    # ``run_pytest`` calls), so no pytest process escapes the command builder.
    tree = ast.parse((REPO / "scripts" / "fast_test.py").read_text(encoding="utf-8"))
    spawners = sorted(
        getattr(node.func, "attr", getattr(node.func, "id", ""))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", getattr(node.func, "id", "")) in {"run", "Popen", "call", "check_output"}
    )
    assert spawners == ["Popen"], f"one process launch, in _start: {spawners}"
    callers = sorted(
        fn.name
        for fn in ast.walk(tree)
        if isinstance(fn, ast.FunctionDef)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_start"
    )
    assert callers == ["run_pytest"], callers


# ── a timeout ends the whole run, and bounds the wait (2026-09-30) ──────
#
# A 4-chunk run took 1 h 22 min: ``subprocess.run(timeout=)`` killed only
# the direct child (on Windows the venv's launcher, not the interpreter
# running the tests), then waited with no timeout for the output pipe the
# interpreter still held; a test's own child was orphaned for over an hour.
# These run REAL processes: a pytest run whose test starts a grandchild.

_GRANDCHILD_TEST = '''
import os
import subprocess
import sys
import time


def test_leaves_a_grandchild():
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)  # kz-grandchild"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(os.environ["KZ_PID_FILE"], "w") as f:
        f.write(str(child.pid))
    if os.environ.get("KZ_HANG") == "1":
        time.sleep(600)
'''


def _grandchild_run(tmp_path: Path, monkeypatch, *, hang: bool) -> tuple[list[str], Path]:
    """pytest args for a run of the probe, isolated from the repo's config."""
    test = tmp_path / "test_grandchild_probe.py"
    test.write_text(_GRANDCHILD_TEST, encoding="utf-8")
    ini = tmp_path / "pytest.ini"
    ini.write_text("[pytest]\n", encoding="utf-8")
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setenv("KZ_PID_FILE", str(pid_file))
    monkeypatch.setenv("KZ_HANG", "1" if hang else "0")
    return [str(test), "-c", str(ini), "--rootdir", str(tmp_path), "--noconftest"], pid_file


def _alive(pid: int) -> bool:
    """Whether the probe's grandchild (by its marker, not a reused PID) runs."""
    import psutil

    try:
        proc = psutil.Process(pid)
        return (
            proc.is_running()
            and proc.status() != psutil.STATUS_ZOMBIE
            and "kz-grandchild" in " ".join(proc.cmdline())
        )
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def _stays_alive(pid: int, seconds: float) -> bool:
    """Alive at every check across the whole window (not just at one instant)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return False
        time.sleep(0.1)
    return True


def _gone(pid: int, within: float = 15.0) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.2)
    return False


def _reap(tmp_path: Path) -> None:
    """Kill anything a probe run left behind (the negative control does)."""
    import psutil

    marker = str(tmp_path)
    for proc in psutil.process_iter(["cmdline"]):
        cmd = " ".join(proc.info.get("cmdline") or [])
        if "kz-grandchild" in cmd or marker in cmd:
            try:
                proc.kill()
            except psutil.Error:
                pass


def test_a_timeout_kills_the_whole_tree_and_bounds_the_wait(tmp_path, monkeypatch) -> None:
    runner = _runner()
    args, pid_file = _grandchild_run(tmp_path, monkeypatch, hang=True)
    try:
        started = time.monotonic()
        code, log = runner.run_pytest(args, timeout=20)
        elapsed = time.monotonic() - started
        assert code == 124, log[-2000:]
        assert pid_file.exists(), "the probe never ran: " + log[-2000:]
        assert elapsed < 20 + runner._DRAIN_S, f"the timeout did not bound the wait: {elapsed:.0f}s"
        assert _gone(int(pid_file.read_text())), "a test's child outlived its timed-out run"
    finally:
        _reap(tmp_path)


def test_killing_only_the_direct_child_leaves_the_tree_running(tmp_path, monkeypatch) -> None:
    """Negative control: the old kill (the direct child only) orphans the rest
    -- the grandchild the test started keeps running."""
    runner = _runner()
    args, pid_file = _grandchild_run(tmp_path, monkeypatch, hang=True)
    proc = subprocess.Popen(
        runner.pytest_command(args), cwd=str(REPO), env=runner.pytest_env(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert pid_file.exists(), "the probe never ran"
        proc.kill()
        proc.wait(timeout=10)
        assert _stays_alive(int(pid_file.read_text()), 1.5), "expected the orphan the old kill leaves"
    finally:
        _reap(tmp_path)


@pytest.mark.skipif(
    os.name != "nt",
    reason="POSIX reaps only on timeout: after a finished run the group id may be reused",
)
def test_a_finished_run_reaps_what_its_tests_left_running(tmp_path, monkeypatch) -> None:
    runner = _runner()
    args, pid_file = _grandchild_run(tmp_path, monkeypatch, hang=False)
    try:
        code, log = runner.run_pytest(args, timeout=120)
        assert code == 0, log[-2000:]
        assert _gone(int(pid_file.read_text())), "a finished run left its test's child running"
    finally:
        _reap(tmp_path)


# ── a chunk's budget follows its size; running out of time is not a crash ─

def test_the_chunk_timeout_scales_with_the_chunk() -> None:
    runner = _runner()
    assert runner.chunk_timeout_for(50, None) == runner._MIN_CHUNK_TIMEOUT
    four_chunk = 230  # files per chunk of a 4-chunk run
    assert runner.chunk_timeout_for(four_chunk, None) == four_chunk * runner._SECONDS_PER_FILE
    assert runner.chunk_timeout_for(four_chunk, 1500.0) == 1500.0  # an explicit value wins (CI)
    # Negative control: the old fixed budget gave a 4-chunk run the 8-chunk one.
    assert runner.chunk_timeout_for(four_chunk, None) > 900.0


def _drive_main(runner, monkeypatch, code_for) -> list[int]:
    """Run ``main`` over 8 fake files in 2 chunks; ``code_for(n_files)`` is the
    exit code a chunk of that size gets. Returns the sizes it was run at."""
    files = [REPO / "tests" / f"test_fake_{i}.py" for i in range(8)]
    sizes: list[int] = []

    def fake_run_chunk(idx, chunk, timeout):
        sizes.append(len(chunk))
        code = code_for(len(chunk))
        counts = {"passed": len(chunk)} if code == 0 else {}
        return {"idx": idx, "code": code, "counts": counts, "failed": [], "log": "", "files": chunk}

    monkeypatch.setattr(runner, "discover_test_files", lambda: files)
    monkeypatch.setattr(runner, "run_chunk", fake_run_chunk)
    monkeypatch.setattr(runner, "MIN_EXPECTED_PASSED", 1)
    monkeypatch.setattr(runner.sys, "argv", ["fast_test.py", "--chunks", "2"])
    return sizes


def test_a_timed_out_chunk_is_split_into_parallel_pieces(monkeypatch, capsys) -> None:
    runner = _runner()
    sizes = _drive_main(runner, monkeypatch, lambda n: 124 if n > 2 else 0)

    def serial(*a, **kw):
        raise AssertionError("a timed-out chunk fell back to one process per file")

    monkeypatch.setattr(runner, "run_pytest", serial)
    assert runner.main() == 0
    out = capsys.readouterr().out
    assert "8 passed" in out and "parallel pieces" in out
    assert sizes[:2] == [4, 4] and max(sizes[2:]) <= 2, sizes


def test_a_crashed_chunk_still_goes_to_isolation(monkeypatch, capsys) -> None:
    """The split is for running out of TIME. A crash still isolates its
    culprit file by file (a segfault is not fixed by smaller pieces)."""
    runner = _runner()
    _drive_main(runner, monkeypatch, lambda n: -11)
    isolated: list[list[str]] = []

    def per_file(args, timeout):
        isolated.append(args)
        return 0, "1 passed in 0.1s"

    monkeypatch.setattr(runner, "run_pytest", per_file)
    assert runner.main() == 0
    assert isolated, "a crash must still be isolated through run_pytest"
    assert "parallel pieces" not in capsys.readouterr().out
