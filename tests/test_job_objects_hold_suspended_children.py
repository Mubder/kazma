"""A Windows Job Object is assigned to a child that has not run yet.

2026-10-13 (found while measuring DOC-A12, the document sandbox's CPU cap).
A venv's ``python.exe`` is a launcher that starts the real interpreter at
once. The document sandbox assigned its job after starting the worker, so
whenever the assignment was a moment late -- the first run of every process
-- the job held only the launcher: the interpreter and the parser ran with no
memory, CPU or process cap, and the result still said "enforced". Measured:
3.93-3.99 cores used under a one-core cap on every first run, 1.03-1.15 on
later ones. ``code_exec`` had the same shape and was winning the race only
because its assignment happened to be quick.

Every product module that calls ``AssignProcessToJobObject`` now starts the
process suspended and resumes it after the assignment (``process_budget``
always did). The behaviour is tested with an assignment made slow on purpose;
the old running launch is the negative control.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_ASSIGNS = re.compile(r"\bAssignProcessToJobObject\b")
_SUSPENDED = re.compile(r"_CREATE_SUSPENDED|\"creationflags\"\s*:\s*0x4\b|creationflags\s*=\s*0x4\b")
_RESUMES = re.compile(r"\bNtResumeProcess\b|\bResumeThread\b")


def _problems(source: str) -> list[str]:
    if not _ASSIGNS.search(source):
        return []
    found = []
    if not _SUSPENDED.search(source):
        found.append("starts the process running (no CREATE_SUSPENDED)")
    if not _RESUMES.search(source):
        found.append("never resumes it after the assignment")
    return found


def test_every_job_object_assignment_holds_a_suspended_child():
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "kazma-*/kazma_*/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    assigning = {}
    for rel in sorted(set(listed)):
        source = (ROOT / rel).read_text(encoding="utf-8")
        if _ASSIGNS.search(source):
            assigning[rel] = _problems(source)
    assert assigning, "the gate found no Job Object assignment at all"
    bad = {rel: issues for rel, issues in assigning.items() if issues}
    assert not bad, bad


def test_the_gate_sees_a_running_launch():
    # Negative control: the document sandbox's old shape.
    old = (
        "creationflags = 0x200\n"
        "process = subprocess.Popen(cmd, creationflags=creationflags)\n"
        "kernel32.AssignProcessToJobObject(job, process._handle)\n"
    )
    assert _problems(old) == [
        "starts the process running (no CREATE_SUSPENDED)",
        "never resumes it after the assignment",
    ]


_HOG = "b = bytearray(800 * 1024 * 1024)\nprint('allocated', len(b))\n"


def _code_exec_hog(tmp_path, monkeypatch) -> str:
    from kazma_core.tools import code_exec

    real_assign = code_exec._assign_to_job_object

    def slow_assign(proc):
        time.sleep(0.5)  # the late assignment, made certain
        return real_assign(proc)

    monkeypatch.setattr(code_exec, "_assign_to_job_object", slow_assign)
    script = tmp_path / "hog.py"
    script.write_text(_HOG, encoding="utf-8")
    return asyncio.run(code_exec._run_local_subprocess(script, str(tmp_path), 30))


def test_a_late_job_still_holds_the_python_exec_snippet(tmp_path, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows Job Object")
    out = _code_exec_hog(tmp_path, monkeypatch)
    assert "MemoryError" in out and "allocated" not in out, out


def test_a_snippet_started_running_escapes_a_late_job(tmp_path, monkeypatch):
    # Negative control: the old launch allocates 800 MiB under a 512 MiB job.
    from kazma_core.tools import code_exec

    if os.name != "nt":
        pytest.skip("Windows Job Object")
    monkeypatch.setattr(code_exec, "_CREATE_SUSPENDED", 0)
    monkeypatch.setattr(code_exec, "_resume_process", lambda proc: True)
    out = _code_exec_hog(tmp_path, monkeypatch)
    assert "allocated 838860800" in out, out
