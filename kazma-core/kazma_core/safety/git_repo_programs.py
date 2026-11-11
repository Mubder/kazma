"""What a repository makes git run, for ``shell_exec``'s git commands.

``shell_exec`` runs an approved git command, and git honours the
repository's own configuration: a hook file, a filter or diff driver, an
ssh command, a pager. The card is the gate (the owner's decision,
2026-11-11), so the card names them; nothing the owner uses (LFS, the
commit hooks they set up) is switched off. One thing is: every git command
``shell_exec`` runs gets ``-c core.fsmonitor=false`` before its subcommand
(:func:`shell_git_argv`), as the git read tools do (§10 E1b).

:func:`_repository_programs` reads the repository with ``git config`` and
``git rev-parse`` -- neither runs any of the programs -- with the tool child
environment (no secrets, §26 I), bounded, and only the repository's own
scopes (``local``, ``worktree``): a program the user's global configuration
sets is theirs and is on every repository. :func:`git_card_note` is the
card's note (``safety.card_notes``, code ``git_programs``), read off the loop.
Gate: ``tests/test_shell_git_programs.py``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "FSMONITOR_OFF",
    "git_card_note",
    "shell_git_argv",
]

#: Before every ``shell_exec`` git subcommand.
FSMONITOR_OFF = ("-c", "core.fsmonitor=false")

#: Keys (git's canonical lower-case names) whose value is a program git starts.
_PROGRAM_KEYS = (
    r"^(filter\..+\.(clean|smudge|process)"
    r"|diff\..+\.(textconv|command)|diff\.external"
    r"|merge\..+\.driver"
    r"|core\.(sshcommand|pager|editor|askpass|gitproxy|hookspath)"
    r"|pager\..+|gpg\.(.+\.)?program|credential\.(.+\.)?helper"
    r"|sequence\.editor|interactive\.difffilter|alias\..+)$"
)
#: The repository's own configuration scopes.
_REPO_SCOPES = ("local", "worktree")
#: Global options a reading command keeps, so it reads the repository the
#: command will (``git -C other status``).
_LOCATION_OPTIONS = ("-C", "--git-dir", "--work-tree")
#: Bounds: a card names at most this many, each value cut.
_MAX_ITEMS = 12
_MAX_VALUE = 120
_TIMEOUT_S = 5.0


def shell_git_argv(args: list[str]) -> list[str]:
    """*args* (git first) with the fsmonitor switched off before the subcommand."""
    return [args[0], *FSMONITOR_OFF, *args[1:]]


def _location_args(git_args: list[str]) -> tuple[list[str], str]:
    """``(the location options of a git command, its subcommand)``."""
    from kazma_core.safety.shell_arguments import split_subcommand

    try:
        index, sub = split_subcommand("git", git_args)
    except ValueError:
        return [], ""
    kept: list[str] = []
    i = 0
    while i < index:
        word = git_args[i]
        flag = word.split("=", 1)[0]
        if flag in _LOCATION_OPTIONS:
            if "=" in word:
                kept.append(word)
            elif i + 1 < index:
                kept += [word, git_args[i + 1]]
                i += 1
        i += 1
    return kept, sub


def _run(argv: list[str], cwd: str) -> str:
    from kazma_core.security.child_env import tool_child_env

    try:
        res = subprocess.run(  # noqa: S603 -- git, a fixed reading argv
            argv, cwd=cwd, env=tool_child_env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("[git-programs] %s could not run", argv[:4], exc_info=True)
        return ""
    return (res.stdout or "") if res.returncode == 0 else ""


def _cut(value: str) -> str:
    value = " ".join(value.split())
    return value if len(value) <= _MAX_VALUE else value[: _MAX_VALUE - 1] + "…"


def _repository_programs(git_args: list[str], cwd: str) -> list[str]:
    """What the repository a git command runs in makes git start.

    *git_args* are the command's words after ``git``. Each item is
    ``hook <name>`` or ``<key> = <value>``. Blocking: a caller on the event
    loop uses :func:`git_card_note`.
    """
    location, sub = _location_args(git_args)
    base = ["git", *location, *FSMONITOR_OFF]
    items: list[str] = []
    listing = _run([*base, "config", "--show-scope", "--includes", "--get-regexp", _PROGRAM_KEYS], cwd)
    for line in listing.splitlines():
        scope, _, rest = line.partition("\t")
        if scope not in _REPO_SCOPES:
            continue
        key, _, value = rest.partition(" ")
        if key.startswith("alias."):
            # An alias starts a program only as a shell alias it is called by.
            if not (value.startswith("!") and sub and key == f"alias.{sub.lower()}"):
                continue
        items.append(f"{key} = {_cut(value)}")
    hooks_dir = _run([*base, "rev-parse", "--path-format=absolute", "--git-path", "hooks"], cwd).strip()
    if hooks_dir:
        path = Path(hooks_dir)
        if not path.is_absolute():
            path = Path(cwd) / path
        try:
            names = sorted(
                p.name for p in path.iterdir()
                if p.is_file() and not p.name.endswith(".sample")
            )
        except OSError:
            names = []
        items = [f"hook {name}" for name in names] + items
    return items[:_MAX_ITEMS] + ([f"+{len(items) - _MAX_ITEMS}"] if len(items) > _MAX_ITEMS else [])


def _git_words(command: str) -> list[str] | None:
    """The words after ``git`` of a ``shell_exec`` git command, else None."""
    from kazma_core.safety.shell_arguments import split_command_line

    try:
        words = split_command_line(command)
    except ValueError:
        return None
    if not words:
        return None
    name = Path(words[0]).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return words[1:] if name == "git" else None


async def git_card_note(tool: str, args: Any, cwd: str | None = None) -> dict[str, Any] | None:
    """The card's note for a ``shell_exec`` git call, or None (another call,
    a plain repository, a repository that cannot be read). Off the loop."""
    if tool != "shell_exec" or not isinstance(args, dict):
        return None
    git_args = _git_words(str(args.get("command") or ""))
    if git_args is None:
        return None
    try:
        if cwd is None:
            from kazma_core.workspace.binding import resolve_active_root

            cwd = str(await asyncio.to_thread(resolve_active_root))
        if not os.path.isdir(cwd):
            return None
        items = await asyncio.to_thread(_repository_programs, git_args, cwd)
    except (OSError, ValueError, RuntimeError):  # a disclosure never breaks the card
        logger.warning("[git-programs] the card's repository note could not be read", exc_info=True)
        return None
    if not items:
        return None
    return {"code": "git_programs", "items": items}
