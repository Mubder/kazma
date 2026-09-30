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

    class _Done:
        returncode, stdout, stderr = 0, "1 passed in 0.1s", ""

    def fake_run(cmd, **kw):
        seen.append(kw.get("env") or {})
        return _Done()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
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

    class _Done:
        returncode, stdout, stderr = 0, "1 passed in 0.1s", ""

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return _Done()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.run_pytest(["tests/test_x.py"], 10)
    assert seen == [runner.pytest_command(["tests/test_x.py"])]
    assert seen[0][1:3] == ["-c", runner._BACKGROUND_BOOTSTRAP], "background is the default"

    tree = ast.parse((REPO / "scripts" / "fast_test.py").read_text(encoding="utf-8"))
    spawners = sorted(
        getattr(node.func, "attr", getattr(node.func, "id", ""))
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "attr", getattr(node.func, "id", "")) in {"run", "Popen", "call", "check_output"}
    )
    assert spawners == ["run"], f"one process launch, in run_pytest: {spawners}"
