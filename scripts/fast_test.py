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
tests/test_sqlite_search_backend.py).

Usage:
    python scripts/fast_test.py                 # default: cpu-count chunks, at most 8
    python scripts/fast_test.py --chunks 8      # explicit chunk count
    python scripts/fast_test.py --chunk-timeout 900
    python scripts/fast_test.py --foreground    # normal priority (default: lowered)

Output: per-chunk summaries, aggregated totals, all FAILED test ids, and a
POISON list. Exit code: 0 only if zero failures and zero poison files.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

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


def run_pytest(args: list[str], timeout: float) -> tuple[int, str]:
    """Run pytest serially; return (exit_code, output). Crash-tolerant.

    A timeout kills the run's whole tree and returns within ``_DRAIN_S`` of
    it; a run that finished has anything it left running reaped.
    """
    cmd = pytest_command(args)
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


def run_chunk(idx: int, files: list[Path], timeout: float) -> dict:
    args = [
        *[str(f.relative_to(REPO)) for f in files],
        "-m", "not slow",
        "--timeout=120",
        "--continue-on-collection-errors",
    ]
    code, log = run_pytest(args, timeout)
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
) -> int:
    """Exit code for the aggregated run.

    0 = green and the suite actually ran. 1 = test failures or poison.
    2 = ran effectively nothing (or below the sanity floor).
    """
    passed = int(totals.get("passed", 0) or 0)
    if poison or failed:
        return 1
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
        m2 = re.search(r"=+\s*(short test summary|slowest\d*\s*test)", rest)
        section = rest[: m2.start()] if m2 else rest
        sections.append(section[:limit])
    joined = "\n".join(sections).strip()
    if joined:
        return joined[:limit]
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
    """The timeout of a chunk of ``n_files`` files; an explicit value wins."""
    if explicit is not None:
        return float(explicit)
    return max(_MIN_CHUNK_TIMEOUT, _SECONDS_PER_FILE * n_files)


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
    ap.add_argument("--chunks", type=int, default=default_chunk_count(os.cpu_count()))
    ap.add_argument("--chunk-timeout", type=float, default=None,
                    help="seconds per chunk (default: %.0f s per file, at least %.0f s)"
                         % (_SECONDS_PER_FILE, _MIN_CHUNK_TIMEOUT))
    ap.add_argument("--file-timeout", type=float, default=180.0,
                    help="per-file timeout during poison-file retry")
    ap.add_argument("--foreground", action="store_true",
                    help="run pytest at normal priority (default: below a normal program, "
                         "so a run never starves a Kazma server on the same machine)")
    args = ap.parse_args()
    global BACKGROUND, THREADS_PER_PROCESS
    BACKGROUND = not args.foreground
    THREADS_PER_PROCESS = max(1, (os.cpu_count() or 4) // max(1, args.chunks))

    files = discover_test_files()
    chunks = chunk_files(files, args.chunks)
    budget = max(chunk_timeout_for(len(c), args.chunk_timeout) for c in chunks)
    print(f"[fast-test] {len(files)} test files in {len(chunks)} chunks "
          f"({args.chunks} requested, timeout {budget:.0f}s/chunk)")
    t0 = time.time()

    totals: dict[str, int] = {}
    all_failed: list[str] = []
    crashed_chunks: list[dict] = []
    failure_logs: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futs = {pool.submit(run_chunk, i, c, chunk_timeout_for(len(c), args.chunk_timeout)): i
                for i, c in enumerate(chunks)}
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            status = "OK" if r["code"] in _BENIGN_EXIT_CODES else f"exit={r['code']}"
            print(f"[fast-test] chunk {r['idx']:02d}: {status} "
                  f"{r['counts'].get('passed', 0)}p/{r['counts'].get('failed', 0)}f "
                  f"({len(r['files'])} files)")
            for k, v in r["counts"].items():
                totals[k] = totals.get(k, 0) + v
            all_failed.extend(r["failed"])
            if r["failed"]:
                failure_logs.append(r["log"])
            # A chunk that produced NO summary line lost its output (observed
            # under heavy concurrency) — treat like a crash and retry per-file.
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
        print(f"[fast-test] chunk {r['idx']:02d} {why} "
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
            print(f"[fast-test] --- chunk {r['idx']:02d}: last {_HANG_DUMP_LINES} lines ---")
            for _ln in _tail:
                _safe_print(f"  | {_ln}")
        else:
            print(f"[fast-test] --- chunk {r['idx']:02d} produced NO output at all ---")
        if r["code"] == 124 and len(r["files"]) > 1:
            # Out of TIME is not a crash: the same work in one process under
            # the same budget times out again (see split_for_retry).
            pieces = split_for_retry(r["files"], args.chunks)
            print(f"[fast-test] chunk {r['idx']:02d} ran out of time — re-running its "
                  f"{len(r['files'])} files as {len(pieces)} parallel pieces")
            jobs = [
                (r["idx"], piece, chunk_timeout_for(len(piece), args.chunk_timeout))
                for piece in pieces
            ]
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                subs = list(pool.map(lambda job: run_chunk(*job), jobs))
            for s in subs:
                if needs_recovery(s):
                    pending.append(s)
                    continue
                for k, v in s["counts"].items():
                    totals[k] = totals.get(k, 0) + v
                all_failed.extend(s["failed"])
                if s["failed"]:
                    failure_logs.append(s["log"])
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
                    f"[fast-test] chunk {r['idx']:02d} died in "
                    f"{_culprit.relative_to(REPO).as_posix()} — re-running the "
                    f"other {len(_rest)} file(s) as one process"
                )
                _code, _log = run_pytest(
                    [*[str(f.relative_to(REPO)) for f in _rest], "-m", "not slow",
                     "--timeout=120", "--continue-on-collection-errors"],
                    timeout=chunk_timeout_for(len(_rest), args.chunk_timeout),
                )
                if _code in _BENIGN_EXIT_CODES and _parse_summary(_log):
                    for k, v in _parse_summary(_log).items():
                        totals[k] = totals.get(k, 0) + v
                    _fh = [m.group(2) for m in _FAILED_RE.finditer(_log)]
                    all_failed.extend(_fh)
                    if _fh:
                        failure_logs.append(_log)
                    # Only the suspect still needs the slow per-file path.
                    _retry_files = [_culprit]
                else:
                    print(
                        f"[fast-test] chunk {r['idx']:02d} minus the suspect "
                        "ALSO failed to report — falling back to per-file"
                    )

        for f in _retry_files:
            code, log = run_pytest(
                [str(f.relative_to(REPO)), "-m", "not slow", "--timeout=120",
                 "--continue-on-collection-errors"],
                args.file_timeout,
            )
            if code in _BENIGN_EXIT_CODES:
                for k, v in _parse_summary(log).items():
                    totals[k] = totals.get(k, 0) + v
                failed_here = [m.group(2) for m in _FAILED_RE.finditer(log)]
                all_failed.extend(failed_here)
                if failed_here:
                    failure_logs.append(log)
            elif code == 124:
                # Identify WHICH test hangs: rerun verbosely with a short
                # per-test timeout — the last line naming a test is the
                # best suspect (deep-audit 2026-08-19 CI triage).
                _, diag = run_pytest(
                    [str(f.relative_to(REPO)), "-m", "not slow", "--timeout=20",
                     "--continue-on-collection-errors", "-v"],
                    120.0,
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
                    args.file_timeout,
                )
                if code2 in _BENIGN_EXIT_CODES:
                    for k, v in _parse_summary(log2).items():
                        totals[k] = totals.get(k, 0) + v
                    failed2 = [m.group(2) for m in _FAILED_RE.finditer(log2)]
                    all_failed.extend(failed2)
                    if failed2:
                        failure_logs.append(log2)
                    print(f"[fast-test] {f.relative_to(REPO)}: crash was intermittent "
                          f"(exit={code}) — rerun {'clean' if not failed2 else 'had failures'}")
                else:
                    # Double crash — identify WHICH test crashes, mirroring
                    # the hang diagnostic (verbose rerun; the last line
                    # naming a test is the best suspect).
                    _, diag = run_pytest(
                        [str(f.relative_to(REPO)), "-m", "not slow", "--timeout=20",
                         "--continue-on-collection-errors", "-v"],
                        120.0,
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
    code = suite_exit_code(totals, failed=all_failed, poison=poison)
    passed = int(totals.get("passed", 0) or 0)
    if code == 2:
        print(
            f"\n[fast-test] suite ran no tests "
            f"(passed={passed} < MIN_EXPECTED={MIN_EXPECTED_PASSED})"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
