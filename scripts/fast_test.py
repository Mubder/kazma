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
    python scripts/fast_test.py                 # default: cpu-count chunks
    python scripts/fast_test.py --chunks 8      # explicit chunk count
    python scripts/fast_test.py --chunk-timeout 900

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


def _parse_summary(log: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    tail = log[-2500:]
    for kind in ("passed", "failed", "skipped", "error", "deselected", "xfailed", "xpassed"):
        mm = re.search(rf"(\d+) {kind}", tail)
        if mm:
            counts[kind] = int(mm.group(1))
    return counts


def run_pytest(args: list[str], timeout: float) -> tuple[int, str]:
    """Run pytest serially; return (exit_code, output). Crash-tolerant."""
    cmd = [sys.executable, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider"]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, out + "\nRUNNER: chunk timed out"
    except Exception as exc:  # noqa: BLE001 — report, never crash the runner
        return -1, f"RUNNER error: {exc}"


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
        m = re.search(rf"^=+ {name} =+\s*$", log, re.M)
        if m is None:
            continue
        rest = log[m.start():]
        m2 = re.search(r"^=+ (short test summary|slowest\d* test) ", rest, re.M)
        section = rest[: m2.start()] if m2 else rest
        sections.append(section[:limit])
    return "\n".join(sections)[:limit]


def is_crash(code: int) -> bool:
    # Windows subprocess returns large negative codes for access violations;
    # POSIX returns -N when the process died from signal N (e.g. -11 =
    # SIGSEGV — previously unclassified, so a segfaulted chunk was never
    # retried and its partial log counted as final, silently dropping
    # ~1000 tests (deep-audit 2026-08-19 CI triage, round 3).
    return code < 0 or code in _CRASH_CODES or code == 139


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chunks", type=int, default=max(2, (os.cpu_count() or 4)))
    ap.add_argument("--chunk-timeout", type=float, default=900.0)
    ap.add_argument("--file-timeout", type=float, default=180.0,
                    help="per-file timeout during poison-file retry")
    args = ap.parse_args()

    files = discover_test_files()
    chunks = chunk_files(files, args.chunks)
    print(f"[fast-test] {len(files)} test files in {len(chunks)} chunks "
          f"({args.chunks} requested, timeout {args.chunk_timeout:.0f}s/chunk)")
    t0 = time.time()

    totals: dict[str, int] = {}
    all_failed: list[str] = []
    crashed_chunks: list[dict] = []
    failure_logs: list[str] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(chunks)) as pool:
        futs = {pool.submit(run_chunk, i, c, args.chunk_timeout): i
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
            if is_crash(r["code"]) or r["code"] == 124 or (
                r["code"] in (0, 1) and not r["counts"]
            ):
                crashed_chunks.append(r)

    # ── Retry crashed chunks file-by-file to isolate poison ────────────────
    poison: list[str] = []
    for r in crashed_chunks:
        print(f"[fast-test] chunk {r['idx']:02d} crashed/timed out "
              f"(exit={r['code']}) — retrying {len(r['files'])} files individually")
        for f in r["files"]:
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
        if digest:
            print("\n[fast-test] failure tracebacks (per-chunk FAILURES sections):")
            # Windows consoles default to cp1252 — tracebacks can contain
            # replacement chars from crashed-chunk output. Never let the
            # REPORTER crash after the suite already ran.
            sys.stdout.buffer.write(digest[:24000].encode("utf-8", errors="replace"))
    if poison:
        print(f"\n[fast-test] POISON files (crash/hang even standalone):")
        for p in poison:
            print(f"  POISON {p}")
    return 0 if (not all_failed and not poison) else 1


if __name__ == "__main__":
    raise SystemExit(main())
