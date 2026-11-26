#!/usr/bin/env python
"""Fast, crash-tolerant full-suite test runner.

Why this exists (2026-08-15):
  - The monolithic serial run takes ~20 minutes and intermittently segfaults
    (a native library), killing the whole run with no results.
  - pytest-xdist parallelizes, but worker segfaults under ``--dist loadfile``
    silently drop that worker's remaining files (observed: 48 worker crashes
    losing ~half the suite) and can crash the scheduler itself.

Strategy: chunk the test FILES into N independent serial pytest processes
(balanced round-robin). A segfaulting chunk loses only itself; its files are
then retried one-by-one with a hard timeout, and any file that still crashes
is reported as POISON (needs a native fix; quarantine it like
tests/test_sqlite_search_backend.py). A chunk runs its files as consecutive
pytest processes of at most ``--files-per-process`` files, one after
another: a long-lived test process only grows (2026-11-23, CI's runner ran
out of memory), and each one that ends gives its memory back.

Usage:
    python scripts/fast_test.py                 # default: cpu-count chunks, at most 8
    python scripts/fast_test.py --chunks 8      # explicit chunk count
    python scripts/fast_test.py --chunk-timeout 900   # seconds per pytest process
    python scripts/fast_test.py --files-per-process 60   # 0: one process per chunk
    python scripts/fast_test.py --foreground    # normal priority (default: lowered)
    python scripts/fast_test.py --cores 8       # use at most 8 CPU cores (0: all)
    python scripts/fast_test.py --shard 2/6     # CI: one of six shards (scripts/ci_shards.py)
    python scripts/fast_test.py --files-from F  # only the test files F names, one a line

A schedule of quiet hours can be kept beside the checkout, in
``.kazma/fast_test.json`` (git ignores ``.kazma/``), so it applies to that
machine only; see :func:`scheduled_cores`.

Output: per-chunk summaries, aggregated totals, all FAILED test ids, and a
POISON list. Exit code: 0 only if zero failures and zero poison files.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import os
import re
import shutil
import subprocess
import sys
import time
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(Path(__file__).resolve().parent) not in sys.path:  # beside this file, whatever the working folder
    sys.path.append(str(Path(__file__).resolve().parent))

import ci_shards  # noqa: E402 - found by the line above

# Directories that contain test files — sourced from pyproject testpaths so
# the runner can never drift from what bare `pytest` collects.
def _test_dirs_from_pyproject() -> list[str]:
    try:
        import tomllib

        data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        tp = data.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("testpaths")
        if tp:
            return [str(p) for p in tp]
    except Exception:
        pass
    return ["tests"]


TEST_DIRS = _test_dirs_from_pyproject()

_FAILED_RE = re.compile(r"^(FAILED|ERROR)\s+(\S+::\S+)", re.M)

# Windows segfault exit code (0xC0000005) and POSIX SIGSEGV. The NTSTATUS
# arrives signed (-1073741819) via some capture paths and unsigned
# (3221225477 = 0xC0000005) via others — BOTH must classify as a crash, or a
# natively-crashed chunk is treated as finished and its files are never
# rerun (observed 2026-08-26: chunk 01 lost 114 files silently).
_CRASH_CODES = {139, -1073741819, 3221225477}


def discover_test_files() -> list[Path]:
    files: list[Path] = []
    for d in TEST_DIRS:
        base = REPO / d
        if not base.is_dir():
            continue
        files.extend(sorted(base.rglob("test_*.py")))
        files.extend(sorted(base.rglob("*_test.py")))
    # Playwright e2e is a separate CI job (polls /health/live). Do not boot
    # uvicorn inside the chunked suite.
    return sorted(
        set(f for f in files if f.is_file() and "e2e" not in f.parts)
    )


def chunk_files(files: list[Path], chunks: int) -> list[list[Path]]:
    """Round-robin so heavy/light files spread evenly (files sorted -> mixed)."""
    out: list[list[Path]] = [[] for _ in range(chunks)]
    for i, f in enumerate(files):
        out[i % chunks].append(f)
    return [c for c in out if c]


def chunk_by_duration(files: list[Path], chunks: int) -> list[list[Path]]:
    """Chunks of about the same measured length (``scripts/ci_shards.py``'s
    table; a file it does not know counts as the median one), each in the
    files' own order. A shard has too few files for the round robin to even
    out: one file of several minutes decided how long the shard took."""
    table = ci_shards.load_table("unit")
    known = sorted(table.values())
    median = known[len(known) // 2] if known else 1.0
    names = {f.relative_to(REPO).as_posix(): f for f in files}
    bins = ci_shards.balance({n: table.get(n, median) for n in names}, max(1, chunks))
    out: list[list[Path]] = [[] for _ in range(max(1, chunks))]
    for name, f in names.items():
        out[bins[name]].append(f)
    return [c for c in out if c]


def files_named_in(path: Path) -> list[Path]:
    """The test files *path* names, one repository path a line."""
    path = ci_shards.contained(path)
    wanted = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    files = [REPO / name for name in dict.fromkeys(wanted)]
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        raise SystemExit(f"[fast-test] no such test file: {', '.join(missing)}")
    return files


#: pytest's final tally, e.g. "==== 2526 passed, 12 skipped in 84.21s ====".
#: Anchored on the trailing "in <n>s" so it cannot match a count mentioned in
#: ordinary test output.
_SUMMARY_LINE_RE = re.compile(
    r"^.*?\b\d+ (?:passed|failed|error|errors|skipped|xfailed|xpassed)\b"
    r".*?\bin \d[\d.]*s.*$",
    re.M,
)


def _parse_summary(log: str) -> dict[str, int]:
    """Counts from pytest's summary line, wherever it is in the output.

    This used to read only ``log[-2500:]``. That is fine when the tally is the
    last thing printed and silently wrong when it is not: a long warnings
    block after it pushes the tally out of the window, nothing parses, and the
    chunk records 0 passed / 0 failed. The caller then sees empty counts,
    concludes the chunk "lost its output", and retries all ~155 of its files
    one process at a time.

    That is what chunk 00 did on every CI run up to 2026-09-16 -- reported
    "OK 0p/0f (155 files)" and then "crashed/timed out (exit=1)" on a suite
    that was entirely green, roughly doubling the job's wall clock and making
    a healthy chunk look like a crash. The existing comment blamed "heavy
    concurrency"; the cause was this 2500-character window.

    Locate the tally instead of hoping it is near the end, and read the LAST
    one (a chunk runs one pytest, but retries append).
    """
    matches = _SUMMARY_LINE_RE.findall(log)
    window = matches[-1] if matches else log[-4000:]
    counts: dict[str, int] = {}
    for kind in ("passed", "failed", "skipped", "error", "deselected", "xfailed", "xpassed"):
        mm = re.search(rf"(\d+) {kind}", window)
        if mm:
            counts[kind] = int(mm.group(1))
    return counts


#: Run by each pytest process before pytest imports anything: it lowers the
#: process below a normal program -- on Windows the CPU class (below normal)
#: and the memory priority (low: its pages are reused first); ``nice``
#: elsewhere. The live Kazma install runs on the machine the suite is run on:
#: two minutes into a 4-chunk run its event loop froze for 18.5 s
#: (2026-09-29 20:03 UTC, a stall dump at an arbitrary TLS read), and a CPU
#: benchmark there caused two health-gated restarts (2026-09-26). A test run
#: must lose that contest, not the server. ``--foreground`` opts out.
#:
#: The disk priority is left alone. Measured on 476 SQLite-heavy tests, one
#: process each (2026-09-30): normal 87 s, CPU + memory lowered 78-80 s, with
#: low I/O priority as well 147-199 s -- Windows throttles low-priority I/O
#: even on an idle disk, and every chunk of a full run hit its timeout.
#: Windows' background mode (``PROCESS_MODE_BACKGROUND_BEGIN``) is worse
#: still: its very-low I/O priority made ``import torch`` take 244 s, not 4.4.
_BACKGROUND_BOOTSTRAP = """\
import os, sys
if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.SetPriorityClass.restype = wintypes.BOOL
    k.SetProcessInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.SetProcessInformation.restype = wintypes.BOOL
    me = k.GetCurrentProcess()
    memory_low = wintypes.ULONG(2)
    if not (k.SetPriorityClass(me, 0x4000)                                   # BELOW_NORMAL_PRIORITY_CLASS
            and k.SetProcessInformation(me, 0, ctypes.byref(memory_low), 4)):  # ProcessMemoryPriority
        print("fast_test: could not lower this process's priority", file=sys.stderr)
else:
    os.nice(10)
# A test that builds the app would raise this process back to normal
# (kazma_core.process_priority raises a lowered server at boot): keep it low.
os.environ["KAZMA_PROCESS_PRIORITY"] = "keep"
import pytest
sys.exit(pytest.main(sys.argv[1:]))
"""

#: Whether pytest processes run at lowered priority (``main`` sets it).
BACKGROUND = True

#: Math-library threads per pytest process (``main`` sets it: the CPUs over
#: the chunks). torch, MKL and OpenBLAS start one thread per CPU in EVERY
#: process, so four chunks on a 32-thread machine ask for 128 compute
#: threads on 32 CPUs -- oversubscription, and CPUs a Kazma server on the
#: same machine needs. The last green full run had them capped by hand
#: (2026-09-29). A cap the caller set in the environment wins.
THREADS_PER_PROCESS: int | None = None
_THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS")


# ── Quiet hours: how many CPU cores a run may use ───────────────────────────
#
# 2026-11-17, the owner: "My pc is whining because of the test suite". A run
# at lowered priority still takes every core, and the fans answer. The run
# can be held to fewer cores (``--cores``, or a schedule kept beside the
# checkout); the cap is the runner's own CPU affinity, which every process it
# starts inherits (pytest, a test's subprocesses, their children). Fewer
# cores take longer, so the chunks' time budget grows with it.

#: The machine's own schedule (git ignores ``.kazma/``), for example
#: ``{"time_zone": "Asia/Kuwait", "quiet": {"from": "01:00", "to": "13:00", "cores": 8}}``:
#: at most 8 cores from 01:00 to 13:00 Kuwait time, all cores the rest of the day.
LOCAL_SCHEDULE = REPO / ".kazma" / "fast_test.json"

#: How much longer a chunk may take than its default budget (``main`` sets it
#: when the run is held to fewer cores than the machine has).
BUDGET_FACTOR = 1.0


def _minutes(text: str) -> int:
    hours, minutes = str(text).split(":")
    value = int(hours) * 60 + int(minutes)
    if not 0 <= value <= 24 * 60:
        raise ValueError(f"not a time of day: {text!r}")
    return value % (24 * 60)


def scheduled_cores(schedule: dict | None, cpus: int, now=None) -> tuple[int, str]:
    """``(cores, why)``: how many cores a run may use now under *schedule*.

    *schedule* is ``{"time_zone": ..., "quiet": {"from": "HH:MM", "to":
    "HH:MM", "cores": N}}``; the quiet window may cross midnight (``from``
    after ``to``). No schedule, or one that cannot be read, is every core
    (the reason says why)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if not schedule:
        return cpus, ""
    try:
        quiet = schedule["quiet"]
        zone = ZoneInfo(str(schedule.get("time_zone") or "UTC"))
        start, end = _minutes(quiet["from"]), _minutes(quiet["to"])
        cores = int(quiet["cores"])
    except (KeyError, TypeError, ValueError) as exc:
        return cpus, f"the schedule in {LOCAL_SCHEDULE.name} cannot be read ({exc}): every core"
    local = (now or datetime.now(tz=zone)).astimezone(zone)
    minute = local.hour * 60 + local.minute
    inside = start <= minute < end if start < end else (minute >= start or minute < end)
    if not inside or cores <= 0 or cores >= cpus:
        return cpus, ""
    return cores, (f"quiet hours {quiet['from']}-{quiet['to']} {zone.key} "
                   f"({LOCAL_SCHEDULE.relative_to(REPO).as_posix()}; --cores 0 for every core)")


def read_schedule() -> dict | None:
    """The machine's schedule, or ``None`` when there is none."""
    import json

    try:
        data = json.loads(LOCAL_SCHEDULE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"quiet": None}  # read as broken: every core, said
    return data if isinstance(data, dict) else {"quiet": None}


def limit_to_cores(cores: int) -> bool:
    """Hold this process -- and so every process it starts -- to its first
    *cores* CPUs. ``False`` when the system refused."""
    cpus = os.cpu_count() or 1
    if cores <= 0 or cores >= cpus:
        return True
    if hasattr(os, "sched_setaffinity"):
        try:
            os.sched_setaffinity(0, set(range(cores)))
            return True
        except OSError:
            return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.SetProcessAffinityMask.argtypes = [wintypes.HANDLE, ctypes.c_size_t]
        k.SetProcessAffinityMask.restype = wintypes.BOOL
        return bool(k.SetProcessAffinityMask(k.GetCurrentProcess(), (1 << cores) - 1))
    return False


def pytest_command(args: list[str], *, background: bool | None = None) -> list[str]:
    """The command line of one serial pytest process."""
    tail = [*args, "-q", "-p", "no:cacheprovider"]
    if BACKGROUND if background is None else background:
        return [sys.executable, "-c", _BACKGROUND_BOOTSTRAP, *tail]
    return [sys.executable, "-m", "pytest", *tail]


def pytest_env() -> dict[str, str]:
    """The environment of one pytest process: this one's, plus the thread cap."""
    env = dict(os.environ)
    if THREADS_PER_PROCESS:
        for var in _THREAD_VARS:
            env.setdefault(var, str(THREADS_PER_PROCESS))
    return env


# ── One pytest run = one killable process tree ────────────────────────────
#
# 2026-09-30, a 4-chunk run took 1 h 22 min and was still going. A chunk hit
# its timeout and ``subprocess.run(timeout=)`` killed only its DIRECT child --
# on Windows the venv's python.exe, a launcher that starts the real
# interpreter as ITS child. The interpreter kept running tests, and CPython
# then calls ``communicate()`` with no timeout on Windows, which waits for
# every holder of the output pipe: the "15-minute" timeout ran 28 minutes.
# A test's own child (the leaked-thread negative control) was orphaned for
# over an hour. Now every run is a unit: a Job Object that kills its members
# when closed (Windows; the launcher starts suspended and joins before it
# can start anything) or its own process group (POSIX). A timeout kills the
# whole tree and the drain after it is bounded; a run that finished is
# reaped too, so nothing a test left running outlives its chunk.

_CREATE_SUSPENDED = 0x00000004
_JOB_KILL_ON_CLOSE = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
_JOB_EXTENDED_LIMIT_INFO = 9     # JobObjectExtendedLimitInformation

#: How long the output drain may take after the tree was killed.
_DRAIN_S = 30.0


def _win_job_for(handle: int):
    """Put a suspended process in a kill-on-close job, then resume it.

    Returns the job handle, or None when the job could not be made (the
    process is resumed either way; the caller then falls back to killing
    the direct child). Membership is inherited, so everything the process
    starts is in the job -- no parent-PID walk that a reused PID could fool.
    """
    import ctypes
    from ctypes import wintypes

    class _Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _Extended(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _Basic),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]

    job = k.CreateJobObjectW(None, None)
    try:
        if job:
            info = _Extended()
            info.BasicLimitInformation.LimitFlags = _JOB_KILL_ON_CLOSE
            joined = k.SetInformationJobObject(
                job, _JOB_EXTENDED_LIMIT_INFO, ctypes.byref(info), ctypes.sizeof(info)
            ) and k.AssignProcessToJobObject(job, handle)
            if not joined:
                k.CloseHandle(job)
                job = None
    finally:
        ntdll.NtResumeProcess(handle)  # never leave a run suspended
    return job or None


def _win_close_job(job) -> None:
    """Close the job: kill-on-close ends every member still running."""
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle(job)


class _Tree:
    """The process tree of one pytest run, killable as a unit."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._job = _win_job_for(int(proc._handle)) if os.name == "nt" else None
        self._closed = False

    def _kill_group(self) -> None:
        import signal

        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass  # the group is already gone

    def kill(self) -> None:
        """Kill every process of the run (a timeout; the leader is alive, so
        on POSIX its process group is certainly this run's)."""
        if os.name == "nt":
            if self._job is not None:
                self.close()
            else:
                self.proc.kill()
        else:
            self._kill_group()

    def close(self) -> None:
        """Reap whatever a finished run left behind; idempotent.

        Windows only: closing the kill-on-close job ends its members, and job
        membership cannot be confused with another process. On POSIX the
        leader is reaped by now, so its group id could in principle belong
        to a new group -- a kill there could hit an unrelated process.
        """
        if self._closed:
            return
        self._closed = True
        if os.name == "nt" and self._job is not None:
            _win_close_job(self._job)


def _start(cmd: list[str], env: dict[str, str]) -> tuple[subprocess.Popen, _Tree]:
    """Launch one pytest process whose whole tree can be killed at once.

    The ONLY process launch in this runner (a test holds it to that): every
    pytest process comes through ``run_pytest``, so the priority bootstrap
    and the thread cap apply to all of them.
    """
    extra = {"creationflags": _CREATE_SUSPENDED} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **extra,
    )
    return proc, _Tree(proc)



def remove_ended_run_folders() -> int:
    """Remove the run folders of runners that have ended; the count.

    A run's folder is removed when the run ends, but not what a process it
    could not stop still held open (``ignore_cleanup_errors``): nine were
    left by 2026-11-14. Each folder names its runner's process id; one whose
    runner still runs (another run beside this one) is kept, and so is one
    whose id another process now has."""
    try:
        import psutil
    except ImportError:
        return 0
    named = re.compile(r"^kazma-pytest-run-(\d+)-")
    removed = 0
    try:
        entries = list(os.scandir(tempfile.gettempdir()))
    except OSError:
        return 0
    for entry in entries:
        match = named.match(entry.name)
        if not match or not entry.is_dir(follow_symlinks=False):
            continue
        pid = int(match.group(1))
        if pid == os.getpid() or psutil.pid_exists(pid):
            continue
        shutil.rmtree(entry.path, ignore_errors=True)
        removed += 1
    return removed

def run_pytest(args: list[str], timeout: float) -> tuple[int, str]:
    """Run pytest serially; return (exit_code, output). Crash-tolerant.

    A timeout kills the run's whole tree and returns within ``_DRAIN_S`` of
    it; a run that finished has anything it left running reaped.
    """
    with contextlib.ExitStack() as owned_arguments:
        # Every run's temporary folders live under a folder this run owns and
        # removes when it ends (2026-11-14). One day of chunked runs left 54
        # runs (23 GB) in the temp folder: a run its budget cut off keeps
        # pytest's lock for three days, so pytest's own cleanup skipped it.
        # Deleting each passed test's folder at once instead (pytest's
        # "failed" retention) broke later tests on Linux, where a store a test
        # opened still pointed into a folder deleted under it.
        run_temp = Path(owned_arguments.enter_context(
            tempfile.TemporaryDirectory(prefix=f'kazma-pytest-run-{os.getpid()}-', ignore_cleanup_errors=True)))
        args = [*args, f'--basetemp={run_temp / "t"}']
        cmd = pytest_command(args)
        # Windows has a 32K UTF-16 command-line ceiling. One serial full-suite
        # chunk can exceed it; pytest argument files keep the exact file set.
        if len(subprocess.list2cmdline(cmd).encode('utf-16-le')) > 48000:
            if any('\n' in arg or '\r' in arg for arg in args):
                raise ValueError('Multiline pytest arguments cannot use an argument file')
            folder = Path(owned_arguments.enter_context(tempfile.TemporaryDirectory(prefix='kazma-pytest-arguments-')))
            arguments = folder / 'arguments.txt'
            arguments.write_text('\n'.join(args) + '\n', encoding='utf8', newline='\n')
            cmd = pytest_command(['@' + str(arguments)])
        try:
            proc, tree = _start(cmd, pytest_env())
        except Exception as exc:  # noqa: BLE001 — report, never crash the runner
            return -1, f"RUNNER error: {exc}"
        try:
            try:
                out, err = proc.communicate(timeout=timeout)
                return proc.returncode, (out or "") + (err or "")
            except subprocess.TimeoutExpired:
                tree.kill()
                try:
                    out, err = proc.communicate(timeout=_DRAIN_S)
                except subprocess.TimeoutExpired:
                    out, err = "", ""
                return 124, (out or "") + (err or "") + "\nRUNNER: chunk timed out"
        finally:
            tree.close()


#: Match a pytest -q progress line, e.g. "path/to/test_x.py ....   [ 12%]".
_PROGRESS_FILE_RE = re.compile(r"^(\S+?\.py)\s", re.M)


def last_file_reached(log: str) -> str | None:
    """The last test FILE pytest printed progress for before it died.

    `--timeout-method=thread` kills the process, so a hung test takes the whole
    chunk's tally with it and the runner then re-runs every one of the chunk's
    ~160 files, one process each. That is the doubling seen on CI: one hung
    test, 160 serial reruns, and the hang is usually in the FOURTH file.

    pytest -q still prints a progress line per file before it dies, so the
    culprit is already in the output. Returning it lets the caller re-run the
    chunk MINUS that file as a single process, and the file on its own — two
    runs instead of a hundred and sixty.
    """
    names = _PROGRESS_FILE_RE.findall(log or "")
    return names[-1].replace("\\", "/") if names else None


#: Seconds per test file, gathered when ``--durations-json`` is given
#: (``main`` sets it): CI's shards upload it, and scripts/ci_shards.py makes
#: the next table of shards from it.
DURATIONS: dict[str, float] | None = None


def run_chunk(idx: int, files: list[Path], timeout: float) -> dict:
    args = [
        *[str(f.relative_to(REPO)) for f in files],
        "-m", "not slow",
        "--timeout=120",
        "--continue-on-collection-errors",
    ]
    if DURATIONS is None:
        code, log = run_pytest(args, timeout)
    else:
        with tempfile.TemporaryDirectory(prefix="kazma-durations-", ignore_cleanup_errors=True) as folder:
            report = Path(folder) / "junit.xml"
            code, log = run_pytest([*args, f"--junitxml={report}", *ci_shards.JUNIT_OPTIONS], timeout)
            if report.is_file():  # a process that crashed wrote none
                measured = ci_shards.durations_of(report)
                with _PRINT_LOCK:
                    DURATIONS.update(measured)
    counts = _parse_summary(log)
    fail_ids = sorted({m.group(2) for m in _FAILED_RE.finditer(log)})
    return {
        "idx": idx,
        "code": code,
        "counts": counts,
        "failed": fail_ids,
        "log": log,
        "files": files,
    }


# ── A pytest process runs a bounded number of files (2026-11-23) ─────────
#
# CI's Tests job (a 4-vCPU, 16 GB runner, ``--chunks 4``: ~470 files per
# pytest process) was ended from outside 30-40 minutes in, run after run
# ("The runner has received a shutdown signal", exit 143). Its memory monitor
# showed why: every chunk process grew for the whole run -- 3.8, 3.6, 3.5 and
# 2.8 GB at 40 minutes, 14.6 of 16 GB in use; 5.1, 4.3 and 4.1 GB at 52
# minutes on an earlier run. ``MALLOC_ARENA_MAX=2`` did not change it: what a
# test leaves reachable (a module cache, a parked singleton, a registry entry)
# stays until its process ends. So a process ends often: a chunk runs its
# files as consecutive pytest processes of at most ``--files-per-process``
# files, one after another, and the number of pytest processes at any moment
# is still the number of chunks. Each process is a run of its own: its own
# budget, its own tally, its own recovery when it crashes or runs out of time.

#: Most test files one pytest process runs when ``--files-per-process`` is not
#: given. Measured on 2026-11-23 (Windows, chunk 00 of the 4-chunk layout,
#: 471 files, private bytes): one process 5.53 GB at its end; at most 120
#: files a process 1.43-2.34 GB; at most 60 files 0.77-1.72 GB -- and the
#: same 7,033 passed, 54 skipped either way. The worst process holds
#: tests/test_settings_scope.py (~710 MB kept, even in a fresh process) and
#: tests/test_static_gates.py (~400 MB); no smaller N parts those two much
#: further. Each extra process costs ~6 s of start-up (the root conftest's
#: pre-import); 471 files as 8 processes took 2,966 s against 2,953 s as one.
DEFAULT_FILES_PER_PROCESS = 60


def split_into_pieces(files: list[Path], per_process: int | None) -> list[list[Path]]:
    """The consecutive pytest processes a chunk of ``files`` runs as.

    At most ``per_process`` files each, as even as possible (471 files at 60
    are eight processes of 59 and 58, not seven of 60 and one of 51), in the
    chunk's own order. ``0`` or ``None``: the whole chunk in one process.
    """
    files = list(files)
    if not files:
        return []
    if not per_process or per_process <= 0 or len(files) <= per_process:
        return [files]
    count = -(-len(files) // per_process)
    size, extra = divmod(len(files), count)
    pieces: list[list[Path]] = []
    start = 0
    for n in range(count):
        end = start + size + (1 if n < extra else 0)
        pieces.append(files[start:end])
        start = end
    return pieces


_PRINT_LOCK = threading.Lock()


def _label(r: dict) -> str:
    """How a result is named in the log: its chunk, and its process when the
    chunk runs as several."""
    return r.get("label") or f"chunk {r['idx']:02d}"


def _report(r: dict) -> None:
    """The line a finished pytest process gets, printed as it finishes."""
    status = "OK" if r["code"] in _BENIGN_EXIT_CODES else f"exit={r['code']}"
    took = f", {r['seconds']:.0f} s" if "seconds" in r else ""
    with _PRINT_LOCK:
        print(f"[fast-test] {_label(r)}: {status} "
              f"{r['counts'].get('passed', 0)}p/{r['counts'].get('failed', 0)}f "
              f"({len(r['files'])} files{took})")


def run_chunk_in_pieces(
    idx: int, files: list[Path], per_process: int | None, explicit_timeout: float | None
) -> list[dict]:
    """Run one chunk as its consecutive pytest processes; one result each.

    Each process ends before the next starts (``run_pytest`` returns only
    when its process has ended, its tree reaped on Windows), and each gets
    the budget of its own size. A process that crashes or runs out of time
    does not stop the ones after it: its result goes to recovery on its own
    and the others' tallies stand."""
    pieces = split_into_pieces(files, per_process)
    results: list[dict] = []
    for n, piece in enumerate(pieces, 1):
        started = time.monotonic()
        r = run_chunk(idx, piece, chunk_timeout_for(len(piece), explicit_timeout))
        r["seconds"] = time.monotonic() - started
        if len(pieces) > 1:
            r["label"] = f"chunk {idx:02d}, process {n}/{len(pieces)}"
        _report(r)
        results.append(r)
    return results


# pytest exit codes that are NOT failures/crashes:
#   0 = green, 1 = tests failed (reported normally), 5 = NO TESTS COLLECTED
# (module-level importorskip files, e.g. the Playwright e2e suite on a
# .[test]-only CI install). Treating 5 as poison kept CI permanently red
# for a benign skip (deep-audit 2026-08-19 CI triage).
_BENIGN_EXIT_CODES = (0, 1, 5)

_DIGEST_LIMIT = 6000

# A poisoned TEMP (WinError 5 junctions) made every chunk 0 passed / exit 1
# while this runner still returned 0 (audit H-14). Floor is a sanity check
# against "CI ran nothing and reported success."
MIN_EXPECTED_PASSED = 500


def suite_exit_code(
    totals: dict[str, int],
    *,
    failed: list[str],
    poison: list[str],
    named_files: bool = False,
) -> int:
    """Exit code for the aggregated run.

    0 = green and the suite actually ran. 1 = test failures or poison.
    2 = ran effectively nothing (or below the sanity floor). A run of named
    files (``--files-from``) has no floor but one: some test was collected.
    """
    passed = int(totals.get("passed", 0) or 0)
    if poison or failed:
        return 1
    if named_files:
        return 0 if sum(totals.values()) else 2
    if passed < MIN_EXPECTED_PASSED:
        return 2
    return 0


#: Lines of a hang/crash dump to keep in the CI log.
#:
#: `--timeout-method=thread` dumps EVERY thread's stack before killing the
#: process. For a hang that dump IS the diagnosis — the blocked thread is
#: rarely the last one printed. This was 25 (chunk) and 40 (per-file), which
#: kept exactly one stack, and twelve consecutive CI failures were investigated
#: against that single frame while the informative threads sat in the part
#: that had been discarded. A few hundred lines in a log nobody reads until
#: something breaks is a much better trade than a diagnosis nobody can make.
_HANG_DUMP_LINES = 400


def _safe_print(text: str) -> None:
    """Print a captured line without ever killing the runner on encoding.

    The dump is pytest's own output, which routinely carries characters the
    Windows console codepage (cp1252) cannot encode — a replacement char from a
    mangled traceback is enough. `print` then raises UnicodeEncodeError, and
    because this runs while REPORTING a failure it took the whole run down with
    it: exit 1, no totals, no diagnosis, after the suite had already finished.

    Found immediately after widening the dump from 25 lines to 400 — more lines
    is more chances to hit one. A diagnostic that can crash the thing it is
    diagnosing is worse than no diagnostic.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, "replace").decode(enc, "replace"))


def _shortened(text: str, limit: int) -> str:
    """*text* in about *limit* characters, its END kept whole: a traceback's
    last lines are the error (2026-11-25: 46 failures told only where the
    test client began)."""
    text = text.strip("\n")
    if len(text) <= limit:
        return text
    head, tail = text[: limit // 4], text[-(limit - limit // 4):]
    return f"{head.rsplit(chr(10), 1)[0]}\n  ... {len(text) - limit} characters left out ...\n{tail.split(chr(10), 1)[-1]}"


#: pytest's own banners: a test may print lines of "=" itself.
_NEXT_SECTION_RE = re.compile(
    r"^=+ (?:short test summary info|warnings summary|FAILURES|ERRORS|PASSES|slowest[^=]*|threads left running"
    r"|\d+ (?:passed|failed|error|errors|skipped)[^=]*) =+", re.M)


def _section_digest(section: str, limit: int) -> str:
    """One FAILURES or ERRORS section: each different error once, told to its
    end, and how many more failed with the same one."""
    parts = re.split(r"^(?=_{2,} .* _{2,}$)", section, flags=re.M)
    blocks = [part for part in parts[1:] if part.strip()]
    if not blocks:
        return _shortened(section, limit)
    groups: dict[str, list[tuple[str, str]]] = {}
    for block in blocks:
        # What a test printed follows its traceback and is not the error.
        found = re.search(r"^-+ Captured ", block, flags=re.M)
        traceback, printed = (block[: found.start()], block[found.start():]) if found else (block, "")
        errors = re.findall(r"^E .*$", traceback, flags=re.M)
        key = "\n".join(errors) or traceback.strip().splitlines()[-1]
        groups.setdefault(key, []).append((traceback, printed))
    shown = [parts[0].strip()]
    each = max(700, limit // len(groups))
    left = limit
    for n, same in enumerate(groups.values()):
        if left <= 0:
            shown.append(f"... {len(groups) - n} more different errors")
            break
        text = _shortened(same[0][0], min(each, max(700, left)))
        if same[0][1]:
            text += "\n" + _shortened(same[0][1], 400)
        if len(same) > 1:
            text += f"\n... and {len(same) - 1} more with this error"
        shown.append(text)
        left -= len(text)
    return "\n".join(shown)


def _failure_digest(log: str, limit: int = _DIGEST_LIMIT) -> str:
    """Extract the FAILURES/ERRORS sections (tracebacks) from pytest -q output.

    The runner previously captured pytest output but never printed it, so
    CI logs showed only the failing-test ids with no assertions/tracebacks
    — Linux-only failures were undiagnosable from the logs alone
    (deep-audit 2026-08-19 CI triage). ERRORS sections matter too: fixture
    / setup errors (e.g. the session_directory family) appear there, not
    under FAILURES.
    """
    sections: list[str] = []
    for name in ("FAILURES", "ERRORS"):
        m = re.search(rf"=+\s*{name}\s*=+", log)
        if m is None:
            continue
        rest = log[m.start():]
        m2 = _NEXT_SECTION_RE.search(rest, m.end() - m.start())
        section = rest[: m2.start()] if m2 else rest
        sections.append(_section_digest(section, limit))
    # The root conftest's report of a non-daemon thread left running names the
    # test it first appeared after; the failing-test list keeps only its id
    # (2026-11-14: two leaked threads, and no way to tell from which test).
    m = re.search(r"=+\s*threads left running\s*=+\n", log)
    if m is not None:
        rest = log[m.end():]
        m2 = re.search(r"^=+ ", rest, flags=re.M)
        sections.append("threads left running:\n" + (rest[: m2.start()] if m2 else rest)[:limit])
    joined = "\n".join(sections).strip()
    if joined:
        return joined[: 2 * limit]  # each section is within about its limit
    # Retried-chunk logs sometimes omit the FAILURES banner (audit Part 5).
    # Still show a tail so CI is not id-only.
    if "FAILED" in log or "ERROR" in log:
        return log[-limit:]
    return ""


def is_crash(code: int) -> bool:
    # Windows subprocess returns large negative codes for access violations;
    # POSIX returns -N when the process died from signal N (e.g. -11 =
    # SIGSEGV — previously unclassified, so a segfaulted chunk was never
    # retried and its partial log counted as final, silently dropping
    # ~1000 tests (deep-audit 2026-08-19 CI triage, round 3).
    return code < 0 or code in _CRASH_CODES or code == 139


#: Most chunks the runner starts unless told otherwise. Each chunk is a full
#: pytest process that may load the local embedding model (torch): at one
#: chunk per CPU a 32-thread box started 32 of them, 12 died with an access
#: violation inside torch's embedding (2026-09-25, a 23-minute run), and an
#: earlier run hung the whole machine (2026-09-11). Seven chunks were the
#: fastest measured there (9-11 min against 12-13 at four).
DEFAULT_MAX_CHUNKS = 8


def default_chunk_count(cpus: int | None) -> int:
    """One chunk per CPU, at least two and at most ``DEFAULT_MAX_CHUNKS``."""
    return max(2, min(cpus or 4, DEFAULT_MAX_CHUNKS))


#: A chunk's budget when ``--chunk-timeout`` is not given: so much per file,
#: never under the floor. A fixed 900 s was sized for the default 8 chunks
#: (~115 files each); ``--chunks 4`` doubled the files and kept 900 s, and
#: every chunk timed out with all its tests passing (2026-09-30: 45% through
#: at the limit). A chunk's budget is a backstop -- a hung TEST is caught by
#: pytest-timeout (``--timeout=120``) long before it.
_SECONDS_PER_FILE = 10.0
_MIN_CHUNK_TIMEOUT = 900.0


def chunk_timeout_for(n_files: int, explicit: float | None) -> float:
    """The timeout of one pytest process running ``n_files`` files (a whole
    chunk, or one of a chunk's consecutive processes); an explicit value
    wins, and is then every process's."""
    if explicit is not None:
        return float(explicit)
    return max(_MIN_CHUNK_TIMEOUT, _SECONDS_PER_FILE * n_files) * BUDGET_FACTOR


def file_timeout_for(explicit_file: float | None, explicit_chunk: float | None) -> float:
    """The budget of ONE file run alone while a chunk is recovered.

    The backstop a one-file chunk gets: a hung TEST is pytest-timeout's to
    catch, so this only has to outlast a long file. A fixed 180 s did not:
    on a slow CI runner (2026-11-11) a chunk went to per-file recovery and
    tests/test_guard_integration.py -- 25 tests against real guard
    processes, several minutes when healthy -- was killed at its tenth test
    and reported POISON. An explicit ``--file-timeout`` wins."""
    if explicit_file is not None:
        return float(explicit_file)
    return chunk_timeout_for(1, explicit_chunk)


def needs_recovery(r: dict) -> bool:
    """A chunk result whose tally cannot be trusted: it crashed, ran out of
    time, or produced no parseable summary."""
    return is_crash(r["code"]) or r["code"] == 124 or (
        r["code"] in (0, 1) and not r["counts"]
    )


def split_for_retry(files: list[Path], parallelism: int) -> list[list[Path]]:
    """Pieces a timed-out chunk is re-run as, in parallel.

    A chunk that ran out of TIME had more work than its budget. Re-running
    the same work in one process under the same budget times out again, and
    one process per file, serially, is what made a 10-minute suite take over
    an hour (2026-09-30: 210 serial processes). Smaller pieces in parallel.
    """
    return chunk_files(files, max(2, min(len(files), parallelism)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chunks", type=int, default=None,
                    help="independent pytest processes (default: one per core in use, at most %d)"
                         % DEFAULT_MAX_CHUNKS)
    ap.add_argument("--cores", type=int, default=None,
                    help="use at most this many CPU cores, 0 for all (default: the machine's "
                         "schedule in .kazma/fast_test.json, else all)")
    ap.add_argument("--chunk-timeout", type=float, default=None,
                    help="seconds per pytest process (default: %.0f s per file, at least %.0f s)"
                         % (_SECONDS_PER_FILE, _MIN_CHUNK_TIMEOUT))
    ap.add_argument("--files-per-process", type=int, default=DEFAULT_FILES_PER_PROCESS,
                    help="most test files one pytest process runs: a chunk runs its files as "
                         "consecutive processes of at most this many, one after another, so no "
                         f"process lives long enough to grow without bound (default: "
                         f"{DEFAULT_FILES_PER_PROCESS}; 0: the whole chunk in one process)")
    ap.add_argument("--file-timeout", type=float, default=None,
                    help="seconds for one file run alone during poison-file retry "
                         "(default: a one-file chunk's budget)")
    ap.add_argument("--foreground", action="store_true",
                    help="run pytest at normal priority (default: below a normal program, "
                         "so a run never starves a Kazma server on the same machine)")
    ap.add_argument("--shard", type=ci_shards.parse_shard, default=None, metavar="K/N",
                    help="run only shard K of N of the suite (scripts/ci_shards.py decides which files)")
    ap.add_argument("--files-from", type=Path, default=None, metavar="FILE",
                    help="run only the test files FILE names, one repository path a line")
    ap.add_argument("--durations-json", type=Path, default=None, metavar="FILE",
                    help="write the seconds each test file took to FILE")
    ap.add_argument("--list", action="store_true", help="print the test files of the run, and run nothing")
    args = ap.parse_args()
    if args.shard and args.files_from:
        ap.error("--shard and --files-from are two answers to one question")
    if args.files_per_process < 0:
        ap.error("--files-per-process must be 0 (one process per chunk) or more")
    global BACKGROUND, THREADS_PER_PROCESS, BUDGET_FACTOR, DURATIONS
    if args.files_from:
        files = files_named_in(args.files_from)
    else:
        files = discover_test_files()
    if args.shard:
        by_name = {f.relative_to(REPO).as_posix(): f for f in files}
        which, count = args.shard
        files = [by_name[name] for name in ci_shards.assign(by_name, count, "unit")[which - 1]]
    if args.list:
        for f in files:
            print(f.relative_to(REPO).as_posix())
        return 0
    if not files:
        print("[fast-test] no test files")
        return 2
    DURATIONS = {} if args.durations_json else None
    BACKGROUND = not args.foreground
    cpus = os.cpu_count() or 4
    if args.cores is not None:
        cores, why = (args.cores if 0 < args.cores < cpus else cpus), "--cores"
    else:
        cores, why = scheduled_cores(read_schedule(), cpus)
    if cores < cpus:
        if limit_to_cores(cores):
            BUDGET_FACTOR = min(4.0, cpus / cores) ** 0.5
            print(f"[fast-test] using {cores} of {cpus} cores: {why}")
        else:
            print(f"[fast-test] could not hold the run to {cores} cores; using all {cpus}")
            cores = cpus
    elif why:
        print(f"[fast-test] {why}")
    if args.chunks is None:
        args.chunks = default_chunk_count(cores)
    THREADS_PER_PROCESS = max(1, cores // max(1, args.chunks))

    removed = remove_ended_run_folders()
    if removed:
        print(f"[fast-test] removed {removed} run folder(s) earlier runs left")
    balanced = args.shard or args.files_from
    chunks = chunk_by_duration(files, args.chunks) if balanced else chunk_files(files, args.chunks)
    plan = [split_into_pieces(c, args.files_per_process) for c in chunks]
    processes = sum(len(p) for p in plan)
    budget = max(chunk_timeout_for(len(piece), args.chunk_timeout) for p in plan for piece in p)
    each = (f"at most {args.files_per_process} files each, one after another in its chunk"
            if processes > len(chunks) else "one per chunk")
    print(f"[fast-test] {len(files)} test files in {len(chunks)} chunks "
          f"({args.chunks} requested): {processes} pytest processes ({each}), "
          f"timeout {budget:.0f}s/process")
    t0 = time.time()

    totals: dict[str, int] = {}
    all_failed: list[str] = []
    crashed_chunks: list[dict] = []
    failure_logs: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futs = {pool.submit(run_chunk_in_pieces, i, c, args.files_per_process, args.chunk_timeout): i
                for i, c in enumerate(chunks)}
        for fut in concurrent.futures.as_completed(futs):
            # One result per pytest process the chunk ran (its line was
            # printed as that process ended); each is counted, or recovered,
            # on its own: a process that crashed costs the others nothing.
            for r in fut.result():
                # A timed-out process can print pytest's terminal summary and
                # then hang during interpreter shutdown. Its files are rerun
                # below; counting both receipts inflates every aggregate.
                if not needs_recovery(r):
                    for k, v in r["counts"].items():
                        totals[k] = totals.get(k, 0) + v
                all_failed.extend(r["failed"])
                if r["failed"]:
                    failure_logs.append(r["log"])
                # A process that produced NO summary line lost its output
                # (observed under heavy concurrency) — treat like a crash.
                if needs_recovery(r):
                    crashed_chunks.append(r)

    # ── Retry crashed chunks file-by-file to isolate poison ────────────────
    poison: list[str] = []
    # Full verbose rerun output per poison file, keyed by relative path.
    #
    # The diagnostic rerun below already captures this — including the
    # faulthandler stack that Python prints on a fatal signal — and the code
    # used to extract one line from it ("last test line") and discard the
    # rest. For a segfault that discarded part IS the diagnosis, which made
    # every native crash un-actionable from a CI log: on 2026-09-16
    # tests/test_reply_sink.py was reported as `exit=-11` with nothing to act
    # on. A tail of it is printed with the POISON list now.
    poison_diag: dict[str, str] = {}
    file_budget = file_timeout_for(args.file_timeout, args.chunk_timeout)
    # A work queue: a timed-out chunk is re-run as smaller pieces in parallel,
    # and a piece that still needs recovery comes back here (it splits again,
    # or goes to the crash isolation below). Each split shrinks the pieces,
    # and a single file never splits, so the queue drains.
    pending = list(crashed_chunks)
    while pending:
        r = pending.pop(0)
        # Name the actual reason. These three arrive here for different
        # causes and need different first hypotheses, and calling all of them
        # "crashed/timed out" sent a 2026-09-20 audit to the wrong diagnosis:
        # it read the label and reported that ORDINARY test failures were
        # being mislabelled as crashes. They are not — an exit-1 chunk whose
        # tally parsed is reported as `OK` (exit 1 is a benign code) and is
        # never retried. It only
        # lands here when the tally did NOT parse, which is a different and
        # much more confusing problem: the chunk ran, something is wrong with
        # our reading of its output, and re-running 155 files one at a time
        # will not tell you what.
        if r["code"] == 124:
            why = "timed out"
        elif is_crash(r["code"]):
            why = "crashed"
        else:
            why = "produced no parseable test tally"
        print(f"[fast-test] {_label(r)} {why} "
              f"(exit={r['code']}) — recovering its {len(r['files'])} files")
        # Show WHY. The chunk's own log was captured and then dropped on the
        # floor, so "crashed/timed out (exit=1)" arrived with no evidence
        # whatsoever — and a chunk that genuinely died looked identical to one
        # whose tally simply failed to parse. Chunks 00 and 03 have reported
        # `0p/0f` on every run observed to date and nobody could say why,
        # because this is the only place that ever held the answer
        # (2026-09-16). Same lesson as the POISON diagnostics below: the
        # runner already has the evidence; print it.
        # 25 lines was not enough to diagnose anything. `--timeout-method=thread`
        # (pyproject addopts) dumps EVERY thread's stack before killing the
        # process — that dump is the whole diagnosis for a hang, and the tail
        # kept only the last thread of it. Twelve CI failures were investigated
        # against one stack because the other threads had been thrown away here.
        _tail = [ln for ln in (r["log"] or "").splitlines() if ln.strip()][-_HANG_DUMP_LINES:]
        if _tail:
            print(f"[fast-test] --- {_label(r)}: last {_HANG_DUMP_LINES} lines ---")
            for _ln in _tail:
                _safe_print(f"  | {_ln}")
        else:
            print(f"[fast-test] --- {_label(r)} produced NO output at all ---")
        if r["code"] == 124 and len(r["files"]) > 1:
            # Out of TIME is not a crash: the same work in one process under
            # the same budget times out again (see split_for_retry).
            pieces = split_for_retry(r["files"], args.chunks)
            print(f"[fast-test] {_label(r)} ran out of time — re-running its "
                  f"{len(r['files'])} files as {len(pieces)} parallel pieces")
            jobs = [
                (r["idx"], piece, chunk_timeout_for(len(piece), args.chunk_timeout))
                for piece in pieces
            ]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                subs = list(pool.map(lambda job: run_chunk(*job), jobs))
            for n, s in enumerate(subs, 1):
                s["label"] = f"{_label(r)}, part {n}/{len(subs)}"
                all_failed.extend(s["failed"])
                if s["failed"]:
                    failure_logs.append(s["log"])
                if needs_recovery(s):
                    pending.append(s)
                    continue
                for k, v in s["counts"].items():
                    totals[k] = totals.get(k, 0) + v
            continue
        # Re-run the chunk MINUS the file it died in, as ONE process, then
        # that file alone. Falling straight to per-file reruns costs ~160
        # processes to isolate a single hang.
        _suspect = last_file_reached(r["log"] or "")
        _retry_files = list(r["files"])
        if _suspect:
            _match = [
                f for f in _retry_files
                if f.relative_to(REPO).as_posix().endswith(_suspect.split("/")[-1])
            ]
            if _match and len(_retry_files) > 1:
                _culprit = _match[0]
                _rest = [f for f in _retry_files if f is not _culprit]
                print(
                    f"[fast-test] {_label(r)} died in "
                    f"{_culprit.relative_to(REPO).as_posix()} — re-running the "
                    f"other {len(_rest)} file(s) as one process"
                )
                _code, _log = run_pytest(
                    [*[str(f.relative_to(REPO)) for f in _rest], "-m", "not slow",
                     "--timeout=120", "--continue-on-collection-errors"],
                    timeout=chunk_timeout_for(len(_rest), args.chunk_timeout),
                )
                _fh = [m.group(2) for m in _FAILED_RE.finditer(_log)]
                all_failed.extend(_fh)
                if _fh:
                    failure_logs.append(_log)
                if _code in _BENIGN_EXIT_CODES and _parse_summary(_log):
                    for k, v in _parse_summary(_log).items():
                        totals[k] = totals.get(k, 0) + v
                    # Only the suspect still needs the slow per-file path.
                    _retry_files = [_culprit]
                else:
                    print(
                        f"[fast-test] {_label(r)} minus the suspect "
                        "ALSO failed to report — falling back to per-file"
                    )

        for f in _retry_files:
            code, log = run_pytest(
                [str(f.relative_to(REPO)), "-m", "not slow", "--timeout=120",
                 "--continue-on-collection-errors"],
                file_budget,
            )
            failed_here = [m.group(2) for m in _FAILED_RE.finditer(log)]
            all_failed.extend(failed_here)
            if failed_here:
                failure_logs.append(log)
            if code in _BENIGN_EXIT_CODES and (code == 5 or _parse_summary(log)):
                for k, v in _parse_summary(log).items():
                    totals[k] = totals.get(k, 0) + v
            elif code == 124:
                # Identify WHICH test hangs: rerun verbosely with a short
                # per-test timeout — the last line naming a test is the
                # best suspect (deep-audit 2026-08-19 CI triage).
                _, diag = run_pytest(
                    [str(f.relative_to(REPO)), "-m", "not slow", "--timeout=20",
                     "--continue-on-collection-errors", "-v"],
                    # The file's own budget: with 120 s the "last test line"
                    # named whatever ran at 120 s, a hang or not.
                    file_budget,
                )
                last = next(
                    (ln.strip() for ln in reversed(diag.splitlines())
                     if "::" in ln),
                    "unknown",
                )
                poison.append(f"{f.relative_to(REPO)} (hang; last test line: {last})")
                poison_diag[str(f.relative_to(REPO))] = diag
            else:
                # One extra chance for the crash class: the native-lib
                # segfaults are INTERMITTENT — a file that crashed standalone
                # often passes an immediate rerun (observed with
                # tests/test_mcp_bridge.py). Only a second consecutive crash
                # is declared POISON (deep-audit 2026-08-19 CI triage).
                code2, log2 = run_pytest(
                    [str(f.relative_to(REPO)), "-m", "not slow", "--timeout=120",
                     "--continue-on-collection-errors"],
                    file_budget,
                )
                failed2 = [m.group(2) for m in _FAILED_RE.finditer(log2)]
                all_failed.extend(failed2)
                if failed2:
                    failure_logs.append(log2)
                if code2 in _BENIGN_EXIT_CODES and (code2 == 5 or _parse_summary(log2)):
                    for k, v in _parse_summary(log2).items():
                        totals[k] = totals.get(k, 0) + v
                    print(f"[fast-test] {f.relative_to(REPO)}: crash was intermittent "
                          f"(exit={code}) — rerun {'clean' if not failed2 else 'had failures'}")
                else:
                    # Double crash — identify WHICH test crashes, mirroring
                    # the hang diagnostic (verbose rerun; the last line
                    # naming a test is the best suspect).
                    _, diag = run_pytest(
                        [str(f.relative_to(REPO)), "-m", "not slow", "--timeout=20",
                         "--continue-on-collection-errors", "-v"],
                        file_budget,
                    )
                    last = next(
                        (ln.strip() for ln in reversed(diag.splitlines())
                         if "::" in ln),
                        "unknown",
                    )
                    poison.append(
                        f"{f.relative_to(REPO)} (exit={code}, rerun exit={code2}; "
                        f"last test line: {last})"
                    )
                    poison_diag[str(f.relative_to(REPO))] = diag

    wall = time.time() - t0
    print(f"\n[fast-test] TOTALS in {wall:.0f}s: " +
          ", ".join(f"{v} {k}" for k, v in sorted(totals.items())))
    if all_failed:
        print(f"\n[fast-test] {len(all_failed)} failing tests:")
        for t in sorted(set(all_failed)):
            print(f"  FAILED {t}")
        # Per-log digest budget: a single verbose diff (e.g. a full manifest
        # comparison) previously ate the whole global cap and starved the
        # other chunks' tracebacks (deep-audit 2026-08-19 CI triage).
        digest = "\n\n".join(
            _failure_digest(log, limit=3000) for log in failure_logs
        ).strip()
        print("\n[fast-test] failure tracebacks (per-chunk FAILURES sections):")
        payload = digest or "(no FAILURES/ERRORS section parsed — see chunk logs)"
        # Windows consoles default to cp1252 — tracebacks can contain
        # replacement chars from crashed-chunk output. Never let the
        # REPORTER crash after the suite already ran.
        sys.stdout.buffer.write(payload[:24000].encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    if poison:
        print(f"\n[fast-test] POISON files (crash/hang even standalone):")
        for p in poison:
            print(f"  POISON {p}")
        # Tail of each poison file's diagnostic rerun. For a segfault this is
        # the faulthandler stack — the only thing that makes `exit=-11`
        # actionable from a CI log (2026-09-16). Bounded so a chatty hang
        # cannot flood the run log.
        for rel, diag in poison_diag.items():
            tail = [ln for ln in diag.splitlines() if ln.strip()][-_HANG_DUMP_LINES:]
            if not tail:
                continue
            print(f"\n[fast-test] --- {rel}: last 40 lines of the diagnostic rerun ---")
            for ln in tail:
                _safe_print(f"  | {ln}")
    if args.durations_json:
        ci_shards.write_durations(args.durations_json, "unit", DURATIONS or {})
    code = suite_exit_code(totals, failed=all_failed, poison=poison, named_files=bool(args.files_from))
    passed = int(totals.get("passed", 0) or 0)
    if code == 2:
        print(
            f"\n[fast-test] suite ran no tests "
            f"(passed={passed} < MIN_EXPECTED={1 if args.files_from else MIN_EXPECTED_PASSED})"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
