"""Whether a git content read with no approval card may run in a repository.

Two doors read a repository with git and show no card: the IDE's read-only
git (``/api/ide/git``, the chat command ``/ide git``) and the agent's
``git_diff`` tool. A content read (``diff``, ``show``, ``log``, ``blame``,
``stash``) runs only when both hold:

- **git itself accepts the workspace as a work tree**
  (``rev-parse --is-inside-work-tree`` prints ``true``, asked with the
  read's own argv and environment). A folder git turns down -- a broken
  ``HEAD``, an extension it does not know, an owner it does not trust --
  makes ``diff`` compare its operands as files of the disk.
- **the repository holds none of Kazma's own files**: no path its index
  lists, and no path in the tree of any ref tip (``HEAD``, every branch,
  remote branch and tag, the stash), is one ``store_registry.is_kazma_store``
  accepts. Git prints a file it holds by forms that never name it (a
  commit, a folder, a pattern, an object id), so naming cannot be the
  rule. The tips' listing is kept per set of tips and re-read when a ref
  moves.

The answer is a sentence from the catalog, in the reader's language.
History only: a file a repository held once and holds in no tip now is
not looked for -- this repository's own public history holds early
development databases under ``kazma-data/``, and a history-wide rule
refused every read on an install whose workspace is a clone of it.
Gate: ``tests/test_ide_reads_refuse_kazma_files.py``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["CONTENT_VERBS", "RunGit", "read_refusal"]

#: The read verbs that print what a repository holds (not only its names).
CONTENT_VERBS = frozenset({"diff", "show", "log", "blame", "stash"})

#: Runs ``git <args>`` the way the door runs its read (its argv, its
#: environment) and answers ``(exit code, stdout)``.
RunGit = Callable[[Sequence[str]], tuple[int, str]]

_TIPS_KEPT = 64
_tips: dict[tuple[str, ...], tuple[str, ...]] = {}
_tips_lock = threading.Lock()


def _said(key: str, **values: str) -> str:
    """*key*'s sentence from the catalog in the reader's language; the key
    itself where the UI's catalog is not loaded (a code a page can word)."""
    try:
        from kazma_ui.i18n import current_language, t
    except ImportError:
        return key
    return t(key, current_language(), **values)


def _in_tips(root: Path, run: RunGit, tips: str) -> tuple[str, ...] | None:
    """Kazma's files in the tree of any ref tip, kept per set of tips."""
    key = (
        os.path.normcase(str(root)),
        hashlib.sha256(tips.encode("utf-8", "replace")).hexdigest(),
        os.environ.get("KAZMA_DATA_DIR", ""),
        os.environ.get("KAZMA_ENV_FILE", ""),
    )
    with _tips_lock:
        kept = _tips.get(key)
    if kept is not None:
        return kept
    from kazma_core.store_registry import kazma_paths_among

    found: tuple[str, ...] = ()
    if tips.strip():
        code, objects = run(["rev-list", "--objects", "--no-walk", "--all"])
        if code != 0:
            return None
        paths = {line.split(" ", 1)[1] for line in objects.splitlines() if " " in line}
        found = tuple(kazma_paths_among(root, paths))
    with _tips_lock:
        if len(_tips) >= _TIPS_KEPT:
            _tips.pop(next(iter(_tips)))
        _tips[key] = found
    return found


def _held_kazma_files(root: Path, run: RunGit) -> list[str] | None:
    """The paths of Kazma's own files the repository at *root* holds (its
    index, the tree of every ref tip), sorted; None when git cannot list
    them (the caller refuses)."""
    from kazma_core.store_registry import kazma_paths_among

    # The whole index by names from the top, wherever the read runs from.
    code, index = run(["ls-files", "-z", "--cached", "--full-name", "--", ":/"])
    if code != 0:
        return None
    # Exit 1 with nothing printed: a repository with no commit yet.
    code, tips = run(["show-ref", "--head"])
    if code not in (0, 1):
        return None
    in_tips = _in_tips(root, run, tips)
    if in_tips is None:
        return None
    staged = kazma_paths_among(root, [p for p in index.split("\0") if p])
    return sorted(set(staged) | set(in_tips))


def read_refusal(root: Path, run: RunGit) -> str | None:
    """Why a content read in the repository at *root* is refused, or None
    when it may run (module docstring)."""
    code, answer = run(["rev-parse", "--is-inside-work-tree", "--show-toplevel"])
    lines = answer.splitlines()
    if code != 0 or not lines or lines[0].strip() != "true":
        return _said("ide.git_not_a_repository")
    # Paths git lists are the top's: a read from a folder inside a repository
    # reads the whole repository.
    top = Path(lines[1].strip()) if len(lines) > 1 and lines[1].strip() else root
    held = _held_kazma_files(top, run)
    if held is None:
        return _said("ide.git_unlisted")
    if held:
        logger.info("[git_read] refused a content read: the repository holds %d of Kazma's files",
                    len(held))
        shown = ", ".join(held[:3]) + (", ..." if len(held) > 3 else "")
        return _said("ide.git_holds_kazma_files", files=shown)
    return None
