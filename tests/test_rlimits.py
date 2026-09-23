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


@pytest.mark.parametrize("limits", [{"memory_bytes": -5}, {"memory_bytes": 0}, {"cpu_seconds": -1}])
def test_a_limit_below_one_is_refused_at_the_call_site(limits):
    """setrlimit reads a negative value as unsigned — effectively unlimited.

    This test's first version passed memory_bytes=-5 expecting the launcher
    to refuse; on Linux the launcher applied it, ran the command unlimited,
    and the sandbox would have reported the limit enforced (CI, 2026-09-23).
    """
    with pytest.raises(ValueError):
        limited_command(["tool"], posix=True, **limits)


# Lowers this process's hard CPU limit, then execs the rest of argv: from
# there, asking for more CPU than that is a limit that genuinely cannot be set
# (an unprivileged process cannot raise its hard limit).
_UNDER_A_LOW_HARD_LIMIT = (
    "import os, resource, sys;"
    "resource.setrlimit(resource.RLIMIT_CPU, (5, 5));"
    "os.execv(sys.argv[1], sys.argv[1:])"
)


@posix_only
@pytest.mark.skipif(os.name != "nt" and os.geteuid() == 0, reason="root may raise a hard limit")
def test_a_strict_launcher_refuses_to_run_unlimited():
    argv = limited_command([sys.executable, "-c", "print('ran')"], cpu_seconds=60)
    out = subprocess.run(
        [sys.executable, "-c", _UNDER_A_LOW_HARD_LIMIT, *argv],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == LIMIT_FAILURE_EXIT, (out.returncode, out.stdout, out.stderr)
    assert "ran" not in out.stdout
    assert "could not be applied" in out.stderr


_UNDER_A_LOW_HARD_MEMORY_LIMIT = (
    "import os, resource, sys;"
    "resource.setrlimit(resource.RLIMIT_AS, (4 << 30, 4 << 30));"
    "os.execv(sys.argv[1], sys.argv[1:])"
)


@posix_only
@pytest.mark.skipif(os.name != "nt" and os.geteuid() == 0, reason="root may raise a hard limit")
def test_a_lenient_launcher_applies_the_limits_it_can():
    """A refused memory limit (tried first) must not skip the CPU limit.

    The launcher used one try around both, so the first refusal left the
    command with no CPU limit at all.
    """
    probe = "import resource; print(resource.getrlimit(resource.RLIMIT_CPU)[0])"
    argv = limited_command(
        [sys.executable, "-c", probe], memory_bytes=8 << 30, cpu_seconds=60, strict=False
    )
    out = subprocess.run(
        [sys.executable, "-c", _UNDER_A_LOW_HARD_MEMORY_LIMIT, *argv],
        capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["60"]
