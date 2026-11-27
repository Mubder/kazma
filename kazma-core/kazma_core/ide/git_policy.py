"""Argument policy for IDE Git reads; mutations keep the tool-registry gate."""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

_READ_VERBS = frozenset({
    "status", "log", "diff", "show", "rev-parse", "blame", "ls-files",
    "describe", "rev-list", "shortlog", "version",
})
_DIFF_FLAGS = frozenset({
    "--stat", "--numstat", "--shortstat", "--name-only", "--name-status",
    "--summary", "--patch", "-p", "-s", "--no-patch", "--raw", "--binary",
    "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
    "--ignore-space-at-eol", "--ignore-all-space", "--ignore-space-change",
    "--check", "-w", "-b", "--exit-code", "--quiet",
})
_FLAGS: dict[str, frozenset[str]] = {
    "status": frozenset({"-s", "--short", "-b", "--branch", "-sb", "-bs", "--porcelain", "-z", "--ignored"}),
    # A read compares the repository's versions: never --no-index, which
    # makes diff compare two files or folders of the disk (2026-11-27).
    "diff": _DIFF_FLAGS | {"--cached", "--staged"},
    "show": _DIFF_FLAGS | {"--oneline", "--pretty", "--format"},
    "log": _DIFF_FLAGS | {"--oneline", "--all", "--graph", "--decorate", "--no-decorate", "--reverse", "--first-parent", "--no-merges", "--merges", "--date-order", "--topo-order"},
    "rev-parse": frozenset({"--abbrev-ref", "--show-toplevel", "--show-prefix", "--verify", "--quiet", "--short", "--is-inside-work-tree", "--is-bare-repository", "--symbolic", "--symbolic-full-name"}),
    "blame": frozenset({"--porcelain", "--line-porcelain", "--incremental", "--no-textconv", "-w", "-l", "-s", "-e", "--show-email", "--show-number"}),
    "ls-files": frozenset({"--cached", "--others", "--modified", "--deleted", "--stage", "--unmerged", "--exclude-standard", "-z", "-c", "-m", "-d", "-s", "-o"}),
    "describe": frozenset({"--always", "--tags", "--all", "--long", "--exact-match", "--first-parent"}),
    "rev-list": frozenset({"--count", "--all", "--reverse", "--first-parent", "--no-merges", "--merges", "--left-right", "--boundary"}),
    "shortlog": frozenset({"-s", "-n", "-e", "-sn", "-ns", "--summary", "--numbered", "--email", "--all"}),
    "version": frozenset({"--build-options"}),
    "branch": frozenset({"--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "-av", "-rv", "--show-current", "--no-color"}),
    "remote": frozenset({"-v", "--verbose", "--all", "--push"}),
    # The latest entry, its tracked files only: older entries live in the
    # stash's reflog, and its untracked files in a third parent, neither of
    # which a read checks (safety.git_kazma_files).
    "stash": _DIFF_FLAGS | {"--oneline"},
}
_VALUES: dict[str, frozenset[str]] = {
    "status": frozenset({"--porcelain", "--untracked-files", "--ignored"}),
    "diff": frozenset({"--unified", "-U", "--diff-filter"}),
    "show": frozenset({"--unified", "-U", "--pretty", "--format", "--date"}),
    "log": frozenset({"-n", "--max-count", "--pretty", "--format", "--date", "--since", "--until", "--author", "--grep"}),
    "rev-parse": frozenset({"--short"}),
    "blame": frozenset({"-L"}),
    "describe": frozenset({"--abbrev", "--match", "--exclude"}),
    "rev-list": frozenset({"-n", "--max-count"}),
    "shortlog": frozenset({"--group"}),
    "branch": frozenset({"--format", "--sort"}),
    "stash": frozenset({"-n", "--max-count", "--format", "--pretty"}),
}


def split_git_command(command: str) -> list[str]:
    """Use the same Windows quoting convention as the post-approval shell."""
    if any(c in command for c in ("\x00", "\r", "\n")):
        raise ValueError("Git commands must be a single argument line")
    parts = shlex.split(command.strip(), posix=os.name != "nt")
    if os.name == "nt":
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return parts


def is_read_request(parts: list[str]) -> bool:
    """Reads with unsupported options must refuse, rather than fall into exec."""
    if not parts:
        return False
    verb, rest = parts[0], parts[1:]
    if verb in _READ_VERBS:
        return True
    if verb == "branch":
        if any(p.split("=", 1)[0] not in _FLAGS["branch"] | _VALUES["branch"]
               for p in rest if p.startswith("-")):
            return False
        return "--list" in rest or all(p.startswith("-") for p in rest)
    if verb == "remote":
        return not rest or rest[0] in ("-v", "--verbose", "get-url", "show")
    return verb == "stash" and bool(rest) and rest[0] in ("list", "show")


def read_operands(parts: list[str]) -> list[str]:
    """Validate each supported option and return positional path/revision operands."""
    if not is_read_request(parts):
        raise ValueError("Git mutation requires tool approval")
    verb, rest = parts[0], parts[1:]
    if verb == "remote" and rest:
        if rest[0] == "show":
            raise ValueError("git remote show may contact a remote; use remote get-url")
        if rest[0] in ("-v", "--verbose") and len(rest) != 1:
            raise ValueError("git remote listing accepts no subcommand")
        if rest[0] == "get-url":
            rest = rest[1:]
    if verb == "stash":
        rest = rest[1:]
    operands: list[str] = []
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--":
            operands.extend(rest[i + 1:])
            break
        if not arg.startswith("-"):
            operands.append(arg)
        else:
            flag, separator, value = arg.partition("=")
            allowed_values = _VALUES.get(verb, frozenset())
            if separator:
                if flag not in allowed_values or not value:
                    raise ValueError(f"Unsupported option for read-only git {verb}: {arg}")
            elif arg in _FLAGS[verb]:
                pass
            elif arg in allowed_values:
                i += 1
                if i >= len(rest) or rest[i].startswith("-"):
                    raise ValueError(f"Missing value for git option {arg}")
            elif verb in ("log", "rev-list", "stash") and re.fullmatch(r"-\d+", arg):
                pass
            elif verb in ("diff", "show") and re.fullmatch(r"-U\d+", arg):
                pass
            else:
                raise ValueError(f"Unsupported option for read-only git {verb}: {arg}")
        i += 1
    if verb == "remote" and parts[1:2] == ["get-url"] and len(operands) != 1:
        raise ValueError("git remote get-url requires exactly one remote name")
    return operands


def is_a_repository(root: Path) -> bool:
    """True when *root* holds a repository git itself finds there: a ``.git``
    file (a linked work tree), or a ``.git`` folder with ``HEAD``, ``objects``
    and ``refs``. Reads run with ``GIT_CEILING_DIRECTORIES`` at the root's
    parent, so no folder above the root is asked."""
    dot = root / ".git"
    if dot.is_file():
        return True
    return dot.is_dir() and (dot / "HEAD").is_file() and (dot / "objects").is_dir() and (dot / "refs").is_dir()


#: A reflog selector (``HEAD@{1}``, ``main@{yesterday}``): a commit the
#: repository's refs may no longer reach. ``@{u}``, ``@{upstream}``,
#: ``@{push}`` and ``@{-N}`` name branches, whose tips a read checks.
_REFLOG_SELECTOR = re.compile(r"@\{(?!(?:u|upstream|push|-\d+)\})", re.IGNORECASE)


def validate_read_paths(parts: list[str], root: Path) -> None:
    """Check path-shaped operands even when Git also accepts revision syntax.

    Every operand is confined to the workspace and asked
    ``store_registry.is_kazma_store``: a read runs with no approval card, and
    Kazma's own stores and secret files are opened by no door. A network or
    device path is refused on its text, before anything resolves it
    (resolving one connects to its host). ``diff`` runs only in a
    repository: outside one git compares its operands as files. What the
    repository itself holds is checked where the read runs
    (``safety.git_kazma_files.read_refusal``); a reflog selector and a stash
    entry but the latest name commits that check does not cover.
    """
    from kazma_core.workspace.remote_paths import remote_path_reason

    operands = read_operands(parts)
    if parts[0] == "diff" and not is_a_repository(root):
        raise ValueError("git diff compares a repository's versions, and this workspace is not a git repository")
    if parts[0] == "stash" and operands:
        raise ValueError("Read-only git stash reads the latest entry only")
    for operand in operands:
        if operand.startswith(":(") or operand.startswith(":/"):
            raise ValueError("Git pathspec magic is unsupported in workspace reads")
        if _REFLOG_SELECTOR.search(operand):
            raise ValueError("Read-only git does not read the reflog")
        path = operand
        # HEAD:path reads an object; validate the path part as well. Drive
        # letters remain filesystem paths on Windows.
        if ":" in path and not (len(path) > 1 and path[1] == ":" and os.name == "nt"):
            path = path.split(":", 1)[1]
        # On the text, before anything below builds or resolves a path from it.
        remote = remote_path_reason(operand) or (remote_path_reason(path) if path else None)
        if remote:
            raise ValueError(remote)
        if not path:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            candidate.resolve().relative_to(root.resolve())
        except (ValueError, OSError) as exc:
            raise ValueError(f"Git operand escapes the workspace: {operand}") from exc
        from kazma_core.store_registry import is_kazma_store, store_refusal
        from kazma_core.workspace.path_policy import control_plane_store_targeted
        if is_kazma_store(candidate, write=False):
            raise ValueError(store_refusal(candidate, door="the IDE's git"))
        if control_plane_store_targeted(path, cwd=str(root)):
            raise ValueError("Git reads may not open Kazma control-plane stores")
