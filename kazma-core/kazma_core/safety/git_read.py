"""A git command that reads a repository and starts no program it chose.

A repository's configuration can make git start programs: its fsmonitor, a
pager, hooks (a status may refresh the index, which runs post-index-change),
a signature program, external diff and textconv drivers, filter drivers. A
read Kazma runs on its own -- the git read tools (no approval card), the
check of which files a checkout would write (``safety.git_tree_writes``) --
switches every one of them off: :func:`read_argv` builds the argv and
:func:`read_env` the environment (the tool child environment without the
variables that name a program). :func:`run_read` runs one, blocking.

Moved here from the git skill's tools (2026-11-16), which keep their names
for these: one hardening for every read, in the package both can import.
Gate: ``tests/test_git_read_tools_start_no_repo_program.py``.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Sequence

logger = logging.getLogger(__name__)

__all__ = [
    "NO_DIFF_PROGRAMS",
    "PROGRAM_ENV",
    "READ_CONFIG",
    "filter_driver_overrides",
    "no_hooks_dir",
    "read_argv",
    "read_env",
    "run_read",
]

#: Configuration a read must not take from the repository: each names a
#: program git would start. ``log.showSignature`` makes log/show run
#: ``gpg.program``.
READ_CONFIG = (
    "core.fsmonitor=false",
    "core.pager=cat",
    "log.showSignature=false",
)
#: Diff options that keep external diff drivers (``diff.external``,
#: ``diff.<driver>.command``) and textconv drivers from running.
NO_DIFF_PROGRAMS = ("--no-ext-diff", "--no-textconv")
_DIFF_SUBCOMMANDS = frozenset({"diff", "show", "log"})
#: Environment a parent may set that names a program for git to run.
PROGRAM_ENV = ("GIT_EXTERNAL_DIFF", "GIT_PAGER", "PAGER", "GIT_EDITOR", "GIT_SEQUENCE_EDITOR")

_NO_HOOKS_DIR: str | None = None


def no_hooks_dir() -> str:
    """An empty directory for ``core.hooksPath``: no hook is found in it."""
    global _NO_HOOKS_DIR
    if _NO_HOOKS_DIR is None or not os.path.isdir(_NO_HOOKS_DIR):
        import atexit
        import tempfile

        _NO_HOOKS_DIR = tempfile.mkdtemp(prefix="kazma-git-no-hooks-")
        # Removed when the process ends: one was left per server start and
        # per test process (2026-11-14: 63 in the temp folder).
        atexit.register(_remove_no_hooks_dir, _NO_HOOKS_DIR)
    return _NO_HOOKS_DIR


def _remove_no_hooks_dir(path: str) -> None:
    try:
        os.rmdir(path)  # empty by design; a folder holding anything is kept
    except OSError:
        pass


def filter_driver_overrides(listing: str) -> list[str]:
    """``-c`` overrides that empty every filter driver the configuration
    defines (``git config --get-regexp`` output).

    A clean filter runs on ``git status`` and ``git diff`` for a modified
    file the attributes assign it to, and only a driver defined in the
    configuration can run. Git treats an empty command as no filter.
    """
    names: list[str] = []
    for line in listing.splitlines():
        key = line.split(" ", 1)[0].strip()
        if not key.startswith("filter."):
            continue
        name = key[len("filter."):].rsplit(".", 1)[0]
        if name and name not in names:
            names.append(name)
    overrides: list[str] = []
    for name in names:
        for kind in ("clean=", "smudge=", "process=", "required=false"):
            overrides += ["-c", f"filter.{name}.{kind}"]
    return overrides


def read_argv(args: Sequence[str], filter_listing: str = "", *, location: Sequence[str] = ()) -> list[str]:
    """The argv of a read-only git command.

    *location* holds git's own location options (``-C``, ``--git-dir``,
    ``--work-tree``) of the command being judged, so the read sees the
    repository that command will.
    """
    argv = ["git", *location, "--no-optional-locks"]
    for setting in (*READ_CONFIG, f"core.hooksPath={no_hooks_dir()}"):
        argv += ["-c", setting]
    argv += filter_driver_overrides(filter_listing)
    args = list(args)
    if args and args[0] in _DIFF_SUBCOMMANDS:
        return [*argv, args[0], *NO_DIFF_PROGRAMS, *args[1:]]
    return [*argv, *args]


def read_env() -> dict[str, str]:
    """The tool child environment without the variables that name a program."""
    from kazma_core.security.child_env import tool_child_env

    env = tool_child_env()
    for name in PROGRAM_ENV:
        env.pop(name, None)
    return env


def run_read(
    args: Sequence[str], cwd: str, *, location: Sequence[str] = (), timeout: float = 15,
) -> tuple[int, str, str]:
    """Run a read-only git command in *cwd*; ``(returncode, stdout, stderr)``.

    Blocking (async code awaits it in a thread). A git that cannot be
    started is ``(127, "", why)``.
    """
    env = read_env()
    try:
        listing = subprocess.run(  # noqa: S603 -- git, reading its configuration
            ["git", *location, "config", "--get-regexp", r"^filter\..*\.(clean|smudge|process)$"],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        )
        res = subprocess.run(  # noqa: S603 -- git, a hardened reading argv
            read_argv(args, listing.stdout or "", location=location),
            cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("[git-read] %s could not run", list(args)[:3], exc_info=True)
        return 127, "", type(exc).__name__
    return res.returncode, res.stdout or "", res.stderr or ""
