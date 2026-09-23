"""Child resource limits without preexec_fn (audit 2026-09-22).

preexec_fn runs Python between fork and exec; in a multi-threaded process the
child can deadlock on a lock another thread held at the fork. The document
sandbox and python_exec now wrap their command in a launcher that sets the
limits on itself and execs the command.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest
from kazma_core.security.rlimits import LIMIT_FAILURE_EXIT, limited_command

posix_only = pytest.mark.skipif(os.name == "nt", reason="rlimits are POSIX; Windows uses Job Objects")


def test_no_limits_or_windows_returns_the_command_unchanged():
    cmd = ["tool", "--flag"]
    assert limited_command(cmd, posix=True) == cmd
    assert limited_command(cmd, memory_bytes=1, cpu_seconds=1, posix=False) == cmd


def test_the_launcher_carries_the_limits_and_the_command():
    argv = limited_command(["tool", "a b"], memory_bytes=1024, cpu_seconds=5, posix=True)
    assert argv[:3] == [sys.executable, "-I", "-c"]
    assert argv[4:] == ["1", "1024", "5", "--", "tool", "a b"]
    lenient = limited_command(["tool"], cpu_seconds=5, strict=False, posix=True)
    assert lenient[4:8] == ["0", "-", "5", "--"]


@posix_only
def test_the_command_really_runs_under_the_limits():
    probe = (
        "import resource;"
        "print(resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_CPU)[0])"
    )
    memory = 2 * 1024 * 1024 * 1024
    argv = limited_command([sys.executable, "-c", probe], memory_bytes=memory, cpu_seconds=7)
    out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [str(memory), "7"]


@posix_only
def test_a_strict_launcher_refuses_to_run_unlimited():
    # A soft limit above the hard limit cannot be set: strict must not exec.
    argv = limited_command([sys.executable, "-c", "print('ran')"], memory_bytes=-5)
    out = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    assert out.returncode == LIMIT_FAILURE_EXIT
    assert "ran" not in out.stdout
