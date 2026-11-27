"""A git command that reads a repository, starts no program it chose and
reads no file its settings name.

A repository's configuration can make git start programs: its fsmonitor, a
pager, hooks (a status may refresh the index, which runs post-index-change),
the signature programs (a log format, a ref sort or ``log.showSignature``
makes git verify), external diff and textconv drivers, filter drivers (a
submodule's own among them), the transport of a lazy fetch. It can also
name files git reads and quotes: an attributes file, a mailmap. A read
Kazma runs on its own -- the IDE's git and the git read tools (no approval
card), the check of which files a checkout would write
(``safety.git_tree_writes``) -- switches every one of them off:
:func:`read_settings` holds the ``-c`` settings, :func:`read_argv` builds
the argv and :func:`read_env` the environment (the tool child environment
without the variables that name a program). :func:`run_read` runs one,
blocking.

Moved here from the git skill's tools (2026-11-16), which keep their names
for these: one hardening for every read, in the package both can import.
Gates: ``tests/test_git_read_tools_start_no_repo_program.py``,
``tests/test_git_settings_start_nothing_and_quote_no_file.py`` (git's own
list of settings, swept).
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
    "READ_GLOBALS",
    "NO_PROGRAM_KEYS",
    "filter_driver_overrides",
    "no_hooks_dir",
    "read_argv",
    "read_env",
    "read_settings",
    "run_read",
]

#: Configuration a read must not take from the repository.
#:
#: - ``core.fsmonitor``, ``core.pager``: a program git would start.
#: - ``log.showSignature``: log/show would run the signature program.
#: - ``core.attributesFile``, ``mailmap.file``, ``mailmap.blob``: a file git
#:   reads and quotes -- an attribute it cannot parse is printed with its
#:   text, a mailmap line becomes an author's name in log, shortlog and blame.
#:   Empty is none; the work tree's own ``.gitattributes`` and ``.mailmap``
#:   still apply.
#: - ``diff.ignoreSubmodules=dirty``: status and diff do not look inside a
#:   submodule's work tree, where its own configuration's filter drivers
#:   would run (the overrides below name only the repository's own drivers).
READ_CONFIG = (
    "core.fsmonitor=false",
    "core.pager=cat",
    "log.showSignature=false",
    "core.attributesFile=",
    "mailmap.file=",
    "mailmap.blob=",
    "diff.ignoreSubmodules=dirty",
)
#: The signature programs: a ``%G`` placeholder in a log format
#: (``format.pretty``, ``pretty.<name>``) and a ref sort by signature
#: (``tag.sort``, ``branch.sort``) make a read verify a signed commit or tag.
#: Each is set to :func:`_no_program`. ``gpg.openpgp.program`` is the newer
#: name of ``gpg.program``: one setting in git, the last value read wins.
NO_PROGRAM_KEYS = ("gpg.program", "gpg.ssh.program", "gpg.x509.program")
#: Git's own options before the verb of every read: no lazy fetch (a
#: partial clone would start the remote's transport -- its upload-pack, ssh
#: command or credential helper -- for an object it lacks), and a replace
#: ref (``refs/replace/``) never makes git print one object's bytes under
#: another's name, a blob of ``.env`` as ``src/app.py``. A git too old for
#: ``--no-lazy-fetch`` refuses the read rather than ignoring it. (A pager
#: starts only on a terminal; a read's output is a pipe.)
READ_GLOBALS = ("--no-lazy-fetch", "--no-replace-objects")
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


def _no_program() -> str:
    """A program path that cannot start: a folder never made, inside the
    empty folder :func:`no_hooks_dir` keeps."""
    return os.path.join(no_hooks_dir(), "absent", "no-program")


def read_settings() -> list[str]:
    """Every ``-c`` setting of a read: :data:`READ_CONFIG`, no hooks, and
    each signature program one that cannot start."""
    return [
        *READ_CONFIG,
        f"core.hooksPath={no_hooks_dir()}",
        *(f"{key}={_no_program()}" for key in NO_PROGRAM_KEYS),
    ]


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
    repository that command will; :data:`READ_GLOBALS` come before the verb.
    """
    argv = ["git", *location, "--no-optional-locks", *READ_GLOBALS]
    for setting in read_settings():
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
