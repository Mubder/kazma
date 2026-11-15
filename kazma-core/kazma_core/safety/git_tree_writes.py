"""Which files a git command that changes the work tree writes, and whether
any of them is Kazma's own.

Kazma's configuration, secret files and stores are never written by a tool,
approved or not (``store_registry.is_kazma_store(path, write=True)``, §62).
A git command writes files it does not name: ``git checkout <branch>`` and
``git merge <branch>`` write every file the other commit holds differently,
an ignored file included (git takes an ignored file as expendable), so on a
workspace that is Kazma's install folder a branch tracking
``kazma.local.yaml`` replaced the owner's configuration -- the approval gate
and hook commands with it (found by the 2026-11-15 defensive check).

:func:`git_command_refusal` is the one check, for every git command a tool
runs: ``shell_exec`` asks it through ``shell_paths.path_refusal`` (before the
card and where it runs) and the git tools ask :func:`switch_refusal` for the
commit they bring in (checkout, merge, pull). Each lists the files with
reads that start nothing the repository chose (``safety.git_read``) and
asks ``is_kazma_store`` of each. The lists are a superset of what git
writes (a file both sides changed alike is listed and not written). A list
that cannot be read refuses the command: nothing was run.

Gate: ``tests/test_git_tree_writes.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = [
    "TREE_SUBCOMMANDS",
    "git_command_refusal",
    "switch_refusal",
]

#: git subcommands ``shell_exec`` allows that write files of the work tree
#: they do not all name.
TREE_SUBCOMMANDS = frozenset({
    "checkout", "switch", "merge", "cherry-pick", "revert", "stash",
    "restore", "rm", "mv",
})

#: Options of each subcommand that take the next word as their value (an
#: option git reads as a flag in one subcommand takes a value in another:
#: ``checkout -m`` merges, ``merge -m`` is the message).
_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "checkout": frozenset({"-b", "-B", "--orphan", "--conflict", "--pathspec-from-file"}),
    "switch": frozenset({"-c", "-C", "--create", "--force-create", "--orphan", "--conflict"}),
    "merge": frozenset({"-m", "--message", "-s", "--strategy", "-X", "--strategy-option",
                        "-F", "--file", "--into-name"}),
    "cherry-pick": frozenset({"-m", "--mainline", "-X", "--strategy-option", "--strategy"}),
    "revert": frozenset({"-m", "--mainline", "-X", "--strategy-option", "--strategy"}),
    "stash": frozenset({"-m", "--message", "--pathspec-from-file"}),
    "restore": frozenset({"-s", "--source", "--conflict", "--pathspec-from-file"}),
    "rm": frozenset({"--pathspec-from-file"}),
    "mv": frozenset(),
}
_STASH_VERBS = frozenset({
    "push", "save", "apply", "pop", "branch", "drop", "clear", "list",
    "show", "create", "store",
})
#: More files than this in one command is not judged file by file.
_MAX_FILES = 50_000
_TIMEOUT_S = 15.0


class _UnknownFilesError(Exception):
    """The files a command writes could not be listed."""


def _read(location: Sequence[str], cwd: str, args: list[str]) -> str:
    from kazma_core.safety.git_read import run_read

    code, out, err = run_read(args, cwd, location=location, timeout=_TIMEOUT_S)
    if code != 0:
        raise _UnknownFilesError((err or out).strip()[:200] or f"git {args[0]} answered {code}")
    return out


def _names(location: Sequence[str], cwd: str, args: list[str]) -> list[str]:
    # "-z" right after the subcommand: after "--" git reads it as a path.
    return [n for n in _read(location, cwd, [args[0], "-z", *args[1:]]).split("\0") if n]


def _resolves(location: Sequence[str], cwd: str, word: str) -> bool:
    if not word or word.startswith("-"):
        return False
    from kazma_core.safety.git_read import run_read

    code, _, _ = run_read(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{word}^{{commit}}"],
        cwd, location=location, timeout=_TIMEOUT_S,
    )
    return code == 0


def _has_head(location: Sequence[str], cwd: str) -> bool:
    return _resolves(location, cwd, "HEAD")


def _split(sub: str, words: Sequence[str]) -> tuple[list[str], list[str], list[str]]:
    """``(options, operands, pathspecs after "--")`` of a subcommand's words.

    A pathspec read from a file (``--pathspec-from-file``) cannot be told
    from the words: the paths are then the whole tree (``.``).
    """
    takes_value = _VALUE_OPTIONS.get(sub, frozenset())
    options: list[str] = []
    operands: list[str] = []
    paths: list[str] = []
    i = 0
    after = False
    while i < len(words):
        word = words[i]
        if after:
            paths.append(word)
        elif word == "--":
            after = True
        elif word.startswith("-") and word != "-":
            options.append(word)
            if word in takes_value and i + 1 < len(words):
                i += 1
                options.append(words[i])
        else:
            operands.append(word)
        i += 1
    if any(o.split("=", 1)[0] == "--pathspec-from-file" for o in options):
        paths = ["."]
    return options, operands, paths


def _commit_of(location: Sequence[str], cwd: str, word: str, *, guess: bool) -> str | None:
    """The commit *word* names: itself, ``-`` (the branch before), or, with
    *guess*, the one remote branch of that name checkout and switch create a
    local branch from."""
    if word == "-":
        word = "@{-1}"
    if _resolves(location, cwd, word):
        return word
    if not guess or word.startswith("-"):
        return None
    refs = _read(location, cwd, ["for-each-ref", "--format=%(refname)", "refs/remotes"]).split()
    matches = [r for r in refs if r.endswith("/" + word) and r.count("/") == 3 + word.count("/")]
    return matches[0] if len(matches) == 1 else None


def _option_value(options: Sequence[str], *names: str) -> str | None:
    for i, word in enumerate(options):
        flag, eq, value = word.partition("=")
        if flag in names:
            if eq:
                return value
            if i + 1 < len(options):
                return options[i + 1]
        for name in names:
            if name.startswith("-") and not name.startswith("--") and word.startswith(name) and len(word) > len(name):
                return word[len(name):]
    return None


def _between(location: Sequence[str], cwd: str, old: str | None, new: str, paths: Sequence[str] = ()) -> list[str]:
    """Files that differ between commit *old* (None: an unborn branch) and *new*."""
    tail = ["--", *paths] if paths else []
    if old is None:
        return _names(location, cwd, ["ls-tree", "-r", "--name-only", new, *tail])
    return _names(location, cwd, ["diff", "--name-only", "--no-renames", old, new, *tail])


def _against_tree(location: Sequence[str], cwd: str, rev: str | None, paths: Sequence[str] = ()) -> list[str]:
    """Files of the work tree that differ from *rev* (None: from the index)."""
    tail = ["--", *paths] if paths else []
    return _names(location, cwd, ["diff", "--name-only", "--no-renames", *([rev] if rev else []), *tail])


class _Command:
    """One git command being judged: where it runs and its parsed words."""

    def __init__(self, location: Sequence[str], cwd: str, sub: str, words: Sequence[str]) -> None:
        self.location = location
        self.cwd = cwd
        self.options, self.operands, self.paths = _split(sub, words)
        self.head = "HEAD" if _has_head(location, cwd) else None

    def names(self, args: list[str]) -> list[str]:
        return _names(self.location, self.cwd, args)

    def against_tree(self, rev: str | None, paths: Sequence[str] = ()) -> list[str]:
        return _against_tree(self.location, self.cwd, rev, paths)

    def between(self, old: str | None, new: str) -> list[str]:
        return _between(self.location, self.cwd, old, new)

    def back_to_head(self) -> list[str]:
        """An abort's: the work tree goes back to HEAD."""
        return self.against_tree(self.head) if self.head else []


def _checkout(c: _Command, sub: str) -> list[str]:
    new_branch = _option_value(c.options, "-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan")
    commits: list[str] = []
    rest: list[str] = []
    for word in c.operands:
        commit = None if word == new_branch else _commit_of(c.location, c.cwd, word, guess=not c.paths)
        (commits if commit else rest).append(commit or word)
    named = c.paths or (rest if sub == "checkout" else [])
    if commits:
        return [name for commit in commits for name in c.against_tree(commit, named)]
    return c.against_tree(None, named) if named else []


def _merge(c: _Command, _sub: str) -> list[str]:
    if any(o in ("--abort", "--quit") for o in c.options):
        return c.back_to_head()
    found: list[str] = []
    for word in c.operands + c.paths:  # after "--" a merge still names commits
        commit = _commit_of(c.location, c.cwd, word, guess=False)
        if commit:
            found += c.between(c.head, commit)
    return found


def _pick(c: _Command, _sub: str) -> list[str]:
    if "--abort" in c.options:
        return c.back_to_head()
    commits = c.operands + c.paths
    if not commits:
        return []
    walk = [] if any(".." in w or w.startswith("^") for w in commits) else ["--no-walk"]
    return c.names(["log", "--format=", "--name-only", "--no-renames", "-m", *walk, *commits, "--"])


def _stash(c: _Command, _sub: str) -> list[str]:
    named_verb = bool(c.operands) and c.operands[0] in _STASH_VERBS
    verb = c.operands[0] if named_verb else "push"
    rest = c.operands[1:] if named_verb else c.operands
    if verb in ("apply", "pop", "branch"):
        given = rest[1:] if verb == "branch" else rest
        ref = given[0] if given else "stash@{0}"
        if ref.isdigit():
            ref = f"stash@{{{ref}}}"
        found = c.between(f"{ref}^1", ref)
        if _resolves(c.location, c.cwd, f"{ref}^3"):
            found += c.between(None, f"{ref}^3")
        return found
    if verb in ("push", "save") and c.head:
        # The work tree goes back to HEAD: the tracked files changed since.
        return c.against_tree(c.head, c.paths or (rest if verb == "push" else []))
    return []


def _restore(c: _Command, _sub: str) -> list[str]:
    staged_only = "--staged" in c.options or "-S" in c.options
    if staged_only and not any(o in ("--worktree", "-W") for o in c.options):
        return []
    return c.against_tree(_option_value(c.options, "--source", "-s"), c.paths or c.operands)


def _rm(c: _Command, _sub: str) -> list[str]:
    if "--cached" in c.options:
        return []
    return c.names(["ls-files", "--", *(c.paths or c.operands)])


def _mv(c: _Command, _sub: str) -> list[str]:
    moved = c.paths or c.operands
    if len(moved) < 2:
        return []
    *sources, dest = moved
    found = c.names(["ls-files", "--", *sources])
    dest_path = Path(dest)
    if (Path(c.cwd) / dest_path).is_dir():
        return found + [str(dest_path / Path(s).name) for s in sources]
    return [*found, dest]


#: What each tree-changing subcommand writes (:data:`TREE_SUBCOMMANDS`).
_WRITERS = {
    "checkout": _checkout, "switch": _checkout, "merge": _merge,
    "cherry-pick": _pick, "revert": _pick, "stash": _stash,
    "restore": _restore, "rm": _rm, "mv": _mv,
}


def _files_written(location: Sequence[str], cwd: str, sub: str, words: Sequence[str]) -> list[str]:
    """Paths (relative to the work tree) *sub* with *words* may write or remove."""
    writer = _WRITERS.get(sub)
    return writer(_Command(location, cwd, sub, words), sub) if writer else []


def _top(location: Sequence[str], cwd: str) -> Path | None:
    try:
        out = _read(location, cwd, ["rev-parse", "--show-toplevel"]).strip()
    except _UnknownFilesError:
        return None
    return Path(out) if out else None


def _first_kazma_file(top: Path, names: Sequence[str]) -> Path | None:
    from kazma_core.store_registry import is_kazma_store

    for name in names:
        path = top / name
        try:
            if is_kazma_store(path, write=True):
                return path
        except (OSError, ValueError):
            return path  # a path the check cannot judge is not written
    return None


def _refusal_for(what: str, path: Path, door: str) -> str:
    from kazma_core.store_registry import store_refusal

    return (
        f"{what} would write '{path.name}' ({path}), which is Kazma's own file; "
        f"nothing was run. " + store_refusal(path, door=door)
    )


def git_command_refusal(git_args: Sequence[str], cwd: str | Path) -> str | None:
    """Why the git command *git_args* (its words after ``git``) may not run, or None.

    Only a subcommand of :data:`TREE_SUBCOMMANDS` is judged. Blocking: it
    runs git reads (async code calls it in a thread).
    """
    from kazma_core.safety.git_repo_programs import _location_args
    from kazma_core.safety.shell_arguments import split_subcommand

    try:
        index, sub = split_subcommand("git", list(git_args))
    except ValueError:
        return None  # the shell policy says why
    if sub not in TREE_SUBCOMMANDS:
        return None
    location, _ = _location_args(list(git_args))
    cwd_text = str(cwd)
    top = _top(location, cwd_text)
    if top is None:
        return None  # not a repository: git refuses it itself
    try:
        names = _files_written(location, cwd_text, sub, list(git_args)[index + 1:])
    except _UnknownFilesError as exc:
        return (
            f"Kazma could not tell which files 'git {sub}' would write ({exc}); "
            "nothing was run."
        )
    if len(names) > _MAX_FILES:
        return (
            f"'git {sub}' would write {len(names):,} files, more than Kazma checks "
            "one by one; nothing was run. Name the files or the commit more narrowly."
        )
    found = _first_kazma_file(top, names)
    return _refusal_for(f"git {sub}", found, "shell_exec") if found else None


def switch_refusal(cwd: str | Path, target: str, *, tool: str, rebase: bool = False) -> str | None:
    """Why bringing commit *target* into the work tree at *cwd* is refused, or None.

    For the git tools: checkout and merge of *target*; ``rebase`` also
    counts the files the local commits rewrite when replayed on it.
    Blocking.
    """
    cwd_text = str(cwd)
    top = _top((), cwd_text)
    if top is None:
        return None
    try:
        head = "HEAD" if _has_head((), cwd_text) else None
        names = _between((), cwd_text, head, target)
        if rebase and head:
            base = _read((), cwd_text, ["merge-base", head, target]).strip()
            if base:
                names += _between((), cwd_text, base, head)
    except _UnknownFilesError as exc:
        return f"Kazma could not tell which files {tool} would write ({exc}); nothing was changed."
    if len(names) > _MAX_FILES:
        return (
            f"{tool} would write {len(names):,} files, more than Kazma checks one by "
            "one; nothing was changed."
        )
    found = _first_kazma_file(top, names)
    return _refusal_for(tool, found, tool) if found else None
