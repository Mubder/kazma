"""A git command that reads a repository, starts no program it chose and
reads no file its settings name.

A repository's configuration can make git start programs: its fsmonitor, a
pager, hooks (a status may refresh the index, which runs post-index-change),
the signature programs (a log format, a ref sort or ``log.showSignature``
makes git verify), external diff and textconv drivers, filter drivers, the
transport of a lazy fetch. It can also name files git reads and quotes: an
attributes file, a mailmap. A read Kazma runs on its own -- the IDE's git
and the git read tools (no approval card), the check of which files a git
command would write before its card (``safety.git_tree_writes``) -- switches
those off: :func:`read_settings` lists the settings and
:func:`read_config_env` puts them, with every filter driver the
configuration defines emptied, in the environment; :func:`read_argv` builds
the argv (git's own options, no external diff or textconv, no look into a
submodule's work tree) and :func:`read_env` the environment (the tool child
environment without the variables that name a program). :func:`run_git`
runs one command with a bound that holds for the programs it starts;
:func:`run_read` runs one read, blocking.

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
    "FILTER_KEYS",
    "NO_DIFF_PROGRAMS",
    "NO_PROGRAM_KEYS",
    "NO_SUBMODULE_TREES",
    "PROGRAM_ENV",
    "READ_CONFIG",
    "READ_GLOBALS",
    "no_hooks_dir",
    "read_argv",
    "read_config_env",
    "read_env",
    "read_settings",
    "run_git",
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
#: - ``diff.ignoreSubmodules=dirty``: a second layer behind
#:   :data:`NO_SUBMODULE_TREES` (a submodule's own ``ignore`` setting wins
#:   over this default; only the command-line option overrides it).
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
#: For status and the diff family: a submodule's work tree is not looked
#: into, so the filter drivers its own configuration defines never run.
#: On the command line it wins over every ``ignore`` setting (the
#: repository's, an included file's, ``.gitmodules``).
NO_SUBMODULE_TREES = ("--ignore-submodules=dirty",)
#: The question that lists every filter driver key the configuration
#: defines, whole and NUL-separated, for :func:`read_config_env`.
FILTER_KEYS = ("config", "--null", "--name-only", "--get-regexp",
               r"^filter\..*\.(clean|smudge|process|required)$")
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
    """Every setting of a read, as ``key=value``: :data:`READ_CONFIG`, no
    hooks, and each signature program one that cannot start. Passed by
    :func:`read_config_env`, never as ``-c``."""
    return [
        *READ_CONFIG,
        f"core.hooksPath={no_hooks_dir()}",
        *(f"{key}={_no_program()}" for key in NO_PROGRAM_KEYS),
    ]


def read_config_env(env: dict[str, str], listed: str = "") -> dict[str, str]:
    """*env*, in place, with every override of a read in git's environment
    form (``GIT_CONFIG_COUNT``, ``GIT_CONFIG_KEY_<n>``, ``GIT_CONFIG_VALUE_<n>``):
    :func:`read_settings`, and each filter key *listed* emptied (*listed*:
    git's answer to :data:`FILTER_KEYS`; a ``required`` key is ``false``).

    Key and value stay apart. ``-c key=value`` is split at the first ``=``,
    so a key holding one never reached its setting. Each filter key is taken
    whole from git's answer: no driver name is parsed or skipped. Overrides
    already in *env* are kept, these after them; the last value read wins.
    Every read of the IDE's git, the git read tools and the check before a
    git command's card (``safety.git_tree_writes``) takes its settings here.
    """
    pairs = [setting.split("=", 1) for setting in read_settings()]
    pairs += [[key, "false" if key.lower().endswith(".required") else ""]
              for key in listed.split("\0") if key]
    count = int(env.get("GIT_CONFIG_COUNT") or 0)
    for key, value in pairs:
        env[f"GIT_CONFIG_KEY_{count}"] = key
        env[f"GIT_CONFIG_VALUE_{count}"] = value
        count += 1
    env["GIT_CONFIG_COUNT"] = str(count)
    return env


def read_argv(args: Sequence[str], *, location: Sequence[str] = ()) -> list[str]:
    """The argv of a read-only git command (its settings are in the
    environment: :func:`read_config_env`).

    *location* holds git's own location options (``-C``, ``--git-dir``,
    ``--work-tree``) of the command being judged, so the read sees the
    repository that command will; :data:`READ_GLOBALS` come before the verb,
    a diff, show, log or blame runs no diff or textconv program, and a verb
    that compares the work tree looks into no submodule's
    (:data:`NO_SUBMODULE_TREES`).
    """
    argv = ["git", *location, "--no-optional-locks", *READ_GLOBALS]
    args = list(args)
    if args and args[0] in _DIFF_SUBCOMMANDS:
        return [*argv, args[0], *NO_DIFF_PROGRAMS, *NO_SUBMODULE_TREES, *args[1:]]
    if args and args[0] == "status":
        return [*argv, "status", *NO_SUBMODULE_TREES, *args[1:]]
    if args and args[0] == "blame":
        return [*argv, "blame", "--no-textconv", *args[1:]]
    return [*argv, *args]


def read_env() -> dict[str, str]:
    """The tool child environment without the variables that name a program."""
    from kazma_core.security.child_env import tool_child_env

    env = tool_child_env()
    for name in PROGRAM_ENV:
        env.pop(name, None)
    return env


#: How long the pipes of a timed-out read are drained once its tree is killed.
_DRAIN_S = 2.0


def run_git(argv: Sequence[str], *, cwd: str, env: dict[str, str], timeout: float) -> tuple[int, str, str]:
    """Run one git command of a read: ``(returncode, stdout, stderr)``.

    Bounded. ``subprocess.run(timeout=)`` kills only git and then waits with
    no limit for its pipes, which a program git started -- a filter that
    never answers -- still holds: a read hung past its timeout. Here the
    whole tree is killed and the last wait is bounded;
    :class:`subprocess.TimeoutExpired` is raised. Blocking: async code awaits
    it in a thread.
    """
    process = subprocess.Popen(  # noqa: S603 -- git, a hardened reading argv
        list(argv), cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
    )
    finished = False
    try:
        out, err = process.communicate(timeout=max(0.001, timeout))
        finished = True
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.communicate(timeout=_DRAIN_S)
        except subprocess.TimeoutExpired:
            logger.warning("[git-read] a program git started still holds the pipes of a timed-out read")
        raise subprocess.TimeoutExpired(list(argv)[:4], timeout) from None
    finally:
        if not finished and process.poll() is None:
            _kill_tree(process)  # interrupted: never leave the tree running
    return process.returncode, out or "", err or ""


def _kill_tree(process: subprocess.Popen[str]) -> None:
    """Kill *process* and every program it started (best effort)."""
    import psutil

    try:
        children = psutil.Process(process.pid).children(recursive=True)
    except psutil.Error:
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.Error:
            logger.debug("[git-read] a program git started had already ended")
    try:
        process.kill()
    except OSError:
        logger.debug("[git-read] git had already ended")


def run_read(
    args: Sequence[str], cwd: str, *, location: Sequence[str] = (), timeout: float = 15,
) -> tuple[int, str, str]:
    """Run a read-only git command in *cwd*; ``(returncode, stdout, stderr)``.

    Blocking (async code awaits it in a thread). A git that cannot be
    started is ``(127, "", why)``.
    """
    try:
        _, listed, _ = run_git(["git", *location, *FILTER_KEYS], cwd=cwd,
                               env=read_config_env(read_env()), timeout=timeout)
        return run_git(read_argv(args, location=location), cwd=cwd,
                       env=read_config_env(read_env(), listed), timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("[git-read] %s could not run", list(args)[:3], exc_info=True)
        return 127, "", type(exc).__name__
