"""A folder's files as its git repository keeps them -- the one answer.

The Workspace page's recent files, the agent's ``file_search`` and the code
index all list a project's files. Only the Workspace page asked git; the
search and the index walked every folder that was not on a hand-kept skip
list. On the live install -- the agent's workspace IS the install folder --
that meant a 4.7 GB repository someone had cloned into it (``core/``,
ignored by the checkout's ``.gitignore``): searches spent seconds there
(13.9 s for one) and returned its matches, and the code index's 4,000-file
budget went to it before it reached Kazma's own code (2026-10-02).

:func:`git_project_files` is that list: tracked files plus untracked ones
the repository's ignore rules do not exclude. ``None`` means "walk it
yourself": the folder is not in a git work tree, git is missing or failed,
or the folder is itself ignored (asked for by name, an ignored folder is
searched as it is -- the default sandbox ``<data dir>/workspace`` sits inside
the install's checkout, which ignores it).

Git runs without the server's secrets (it runs the repository's configured
programs, AGENTS.md §26I) and with ``core.fsmonitor`` off, so none starts.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

__all__ = ["GENERATED_DIRS", "git_project_files", "iter_project_files", "skipped_dir"]

logger = logging.getLogger(__name__)

#: Folders no walk of a project enters: environments, caches, build output,
#: Kazma's own data, and Windows user-data trees. Those last never belong in
#: a project; they appeared when a command ran with HOME pointed at the
#: workspace (the install had ``AppData/Local/uv/cache``, 2,453 indexable
#: files, 2026-09-12; the cause was fixed 2026-10-02). The one list for the
#: agent's search, the code index and the Workspace page, which kept three
#: different copies until 2026-10-02.
GENERATED_DIRS: frozenset[str] = frozenset({
    ".venv", "venv", "site-packages", ".tox", ".eggs",
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", "build", "dist", "target", "coverage", ".next", ".turbo",
    ".idea", ".vs",
    ".kazma", "kazma-data", "vector_memory",
    "AppData", "Application Data", "Local Settings",
})

#: Seconds one git call may take before the caller walks instead.
_GIT_TIMEOUT_S = 15.0


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    from kazma_core.security.child_env import tool_child_env

    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args],
            cwd=str(cwd),
            env=tool_child_env(),
            capture_output=True,
            timeout=_GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("[project_files] git %s failed in %s", args[:1], cwd, exc_info=True)
        return None


def _ignored_inside_a_repository(root: Path) -> bool | None:
    """Whether *root* is ignored by the repository around it; None: no repository."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0:
        return None
    top_path = Path(top.stdout.decode("utf-8", "replace").strip())
    try:
        rel = root.resolve().relative_to(top_path.resolve())
    except (OSError, ValueError):
        return None
    if not rel.parts:
        return False
    check = _git(top_path, "check-ignore", "-q", "--", rel.as_posix())
    if check is None or check.returncode not in (0, 1):
        return None
    return check.returncode == 0


def git_project_files(root: Path) -> list[Path] | None:
    """The files under *root* its repository keeps, or None to walk instead."""
    root = Path(root)
    if not (root / ".git").exists():
        ignored = _ignored_inside_a_repository(root)
        if ignored is None or ignored:
            return None
    res = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if res is None or res.returncode != 0:
        return None
    return [root / name for name in res.stdout.decode("utf-8", "replace").split("\0") if name]


def skipped_dir(name: str) -> bool:
    """True for a folder a walk of the project's files never enters.

    A generated folder, or tool state: a name starting with a dot (task
    worktrees under ``.claude``, editor and cache folders).
    """
    return name in GENERATED_DIRS or name.startswith(".")


def iter_project_files(root: Path) -> Iterator[Path]:
    """The project's own files under *root*, never inside a skipped folder.

    Git's list when git knows the folder (:func:`git_project_files`), in a
    stable order; otherwise a walk that never descends into a skipped
    folder. Git also lists a tracked file the disk no longer holds, so a
    reader handles ``OSError``. The code index and the security report read
    this; the agent's ``file_search`` keeps its own walk (it searches dot
    folders such as ``.github``).
    """
    root = Path(root)
    listed = git_project_files(root)
    if listed is None:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not skipped_dir(d))
            for name in sorted(filenames):
                yield Path(dirpath) / name
        return
    for path in sorted(listed):
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if not any(skipped_dir(part) for part in parts[:-1]):
            yield path
