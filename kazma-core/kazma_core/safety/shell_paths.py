"""What a ``shell_exec`` command reads and writes, resolved -- and whether any
of it is Kazma's own.

2026-11 review, on an install whose workspace IS its install folder (the
install's ``.env`` holds the vault key; ``restic.pass`` opens every offsite
backup). The guard checked only the words that LOOKED like one of Kazma's
files (``.env``, ``*.db``) and only the words that LOOKED like a path for
the workspace boundary. So each of these ran after one approval card:

* ``grep -r -a KAZMA_VAULT_KEY .`` -- a folder holding the secret files,
  read whole (and ``grep -r`` with no folder reads the working folder);
* ``cat ENV~1`` / ``cat RESTIC~1.PAS`` -- Windows short (8.3) names of
  ``.env`` and ``restic.pass``;
* ``cat link/secret.txt`` -- a junction inside the workspace leading out.

Now every value a command carries (:func:`path_candidates`) is resolved to
the file it opens (short names, trailing dots, links and junctions,
``~``), asked :func:`kazma_core.store_registry.is_kazma_store` (a program
that may write the files it names -- anything but a plain reader -- with
``write=True``, so Kazma's configuration files count too) and held to the
workspace by its REAL path. A wildcard counts as the folder it is expanded
in (Windows programs expand their own, by long and short name). A command
that reads, copies, archives or moves a whole folder (:data:`WALKS`) is
refused when the folder holds one of Kazma's files
(:func:`kazma_core.store_registry.kazma_file_under`), the working folder
included when the program walks it with no folder named.

:func:`path_refusal` is the one check: ``shell_exec`` asks it after its
approval and :func:`shell_target_refusal` -- the chat tool worker's and the
registry's question before an approval card (``safety/call_preflight``) --
asks it before, so the owner is never asked to approve a call that cannot
run. It touches the filesystem: callers on the event loop use the async
wrapper. ``tests/test_shell_exec_kazma_files.py`` holds it.
"""

from __future__ import annotations

import asyncio
import glob as _glob
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from kazma_core.safety.shell_arguments import (
    argument_refusal,
    attached_values,
    matches_blocked,
    program_name,
    short_option_values,
    split_command_line,
    split_subcommand,
)

logger = logging.getLogger(__name__)

__all__ = [
    "NEVER_ALLOWED",
    "NO_WALK",
    "READ_ONLY_PROGRAMS",
    "WALKS",
    "path_candidates",
    "path_refusal",
    "read_paths",
    "shell_target_refusal",
]

#: Short options whose attached value is refused for a binary (a pattern or
#: file list read from a file, or a file written).
_BLOCKED_ATTACHED = {"tar": {"-I", "-T"}, "grep": {"-f"}, "jq": {"-f"}, "sort": {"-o"}}

#: Programs that only read the files they are given. Any other program may
#: write a file it names (``uniq IN OUT``, ``git diff --output=``,
#: ``pytest --junitxml=``, ``cp``, ``touch``), so a value naming Kazma's
#: configuration is refused for it; these may read configuration.
READ_ONLY_PROGRAMS: frozenset[str] = frozenset({
    "cat", "head", "tail", "grep", "wc", "jq", "cut", "tr", "ls", "du", "find",
    "sort", "echo", "printf", "date", "whoami", "pwd", "df", "free", "uptime",
    "uname", "hostname", "sleep", "ps", "pgrep",
})

_GLOB_MAGIC = re.compile(r"[*?\[]")


@dataclass(frozen=True)
class _Walk:
    """How a program walks the folders it is given."""

    #: Short options that take a value (the value is not a folder).
    short_values: str = ""
    #: Long options that take a value as the next word.
    long_values: frozenset[str] = field(default_factory=frozenset)
    #: It walks its folders whatever the options.
    always: bool = False
    #: Short option letters that make it walk.
    short_flags: str = ""
    #: Long options (or ``--opt=value`` words) that make it walk.
    long_flags: frozenset[str] = field(default_factory=frozenset)
    #: With no folder named, it walks the working folder.
    no_path_is_cwd: bool = False
    #: It moves or rewrites what it walks (configuration counts too).
    relocates: bool = False
    #: The first operand is a pattern, not a path, unless -e/-f give one.
    pattern_first: bool = False
    #: Short / long options under which it follows links out of the folder.
    follows_short: str = ""
    follows_long: frozenset[str] = field(default_factory=frozenset)
    #: It follows links unless told not to (zip: unless -y/--symlinks).
    follows_unless: tuple[str, ...] = ()


#: Programs on shell_exec's allowlist that read, copy, archive or move a
#: whole folder. ``find`` and ``tar`` and git are read by their own rules
#: (:func:`_walked_folders`). ``tests/test_shell_exec_kazma_files.py`` fails
#: on an allowlisted program that is neither here nor in :data:`NO_WALK`.
WALKS: dict[str, _Walk] = {
    "grep": _Walk(
        short_values="efmABCDd",
        long_values=frozenset({
            "--regexp", "--file", "--max-count", "--after-context",
            "--before-context", "--context", "--directories", "--devices",
            "--exclude", "--exclude-from", "--exclude-dir", "--include",
            "--label", "--binary-files", "--group-separator",
        }),
        short_flags="rR",
        long_flags=frozenset({"--recursive", "--dereference-recursive", "--directories=recurse"}),
        no_path_is_cwd=True,
        pattern_first=True,
        follows_short="R",
        follows_long=frozenset({"--dereference-recursive"}),
    ),
    "ls": _Walk(
        short_values="ITw",
        long_values=frozenset({
            "--block-size", "--format", "--hide", "--ignore", "--indicator-style",
            "--quoting-style", "--sort", "--tabsize", "--time", "--time-style", "--width",
        }),
        short_flags="R",
        long_flags=frozenset({"--recursive"}),
        no_path_is_cwd=True,
        follows_short="L",
        follows_long=frozenset({"--dereference"}),
    ),
    "du": _Walk(
        short_values="BdtX",
        long_values=frozenset({
            "--block-size", "--max-depth", "--threshold", "--exclude",
            "--exclude-from", "--time-style",
        }),
        always=True,
        no_path_is_cwd=True,
        follows_short="L",
        follows_long=frozenset({"--dereference"}),
    ),
    "cp": _Walk(
        short_values="St",
        long_values=frozenset({"--suffix", "--target-directory"}),
        short_flags="rRa",
        long_flags=frozenset({"--recursive", "--archive"}),
        follows_short="L",
        follows_long=frozenset({"--dereference"}),
    ),
    "mv": _Walk(
        short_values="St",
        long_values=frozenset({"--suffix", "--target-directory"}),
        always=True,
        relocates=True,
    ),
    "zip": _Walk(
        short_values="bOtn",
        short_flags="r",
        long_flags=frozenset({"--recurse-paths", "--recurse-patterns"}),
        follows_unless=("-y", "--symlinks"),
    ),
    "gzip": _Walk(
        short_values="S",
        long_values=frozenset({"--suffix"}),
        short_flags="r",
        long_flags=frozenset({"--recursive"}),
        relocates=True,
    ),
    "gunzip": _Walk(
        short_values="S",
        long_values=frozenset({"--suffix"}),
        short_flags="r",
        long_flags=frozenset({"--recursive"}),
        relocates=True,
    ),
    # Read by their own rules in _walked_folders.
    "find": _Walk(always=True, no_path_is_cwd=True),
    "tar": _Walk(),
    "git": _Walk(),
}

#: Allowlisted programs that never walk a folder's files for their content,
#: with why.
NO_WALK: dict[str, str] = {
    "cat": "reads the files named", "head": "reads the files named",
    "tail": "reads the files named", "wc": "reads the files named",
    "sort": "reads the files named", "uniq": "reads one file, writes one",
    "cut": "reads the files named", "tr": "reads its input only",
    "jq": "reads the files named", "echo": "names no file",
    "printf": "names no file", "date": "reads only a file -f names",
    "whoami": "names no file", "pwd": "names no file", "df": "file systems, not files",
    "free": "names no file", "uptime": "names no file", "uname": "names no file",
    "hostname": "names no file", "sleep": "names no file",
    "pgrep": "processes, not files",
    "mkdir": "creates the folders named", "touch": "creates the files named",
    "unzip": "writes the archive's members (checked by name)",
    "uv": "reads its project's own files through the build backend",
    "pytest": "imports Python files only", "ruff": "reads Python files only",
    "mypy": "reads Python files only",
}

#: Programs shell_exec's allowlist may never hold, in any mode, with why.
#: ``tests/test_shell_exec_kazma_files.py`` reads the allowlist from its source
#: and fails on any of these.
NEVER_ALLOWED: dict[str, str] = {
    "kazma": "Kazma's own command line: `migrate export` writes the vault and its "
             "key into a bundle, `restore` and `import` replace stores",
    "ps": "`ps eww` prints another process's environment (the server's vault key)",
    "env": "prints this process's environment",
    "printenv": "prints this process's environment",
    "python": "runs any code (python_exec is the sandboxed way)",
    "python3": "runs any code",
    "node": "runs any code",
    "bash": "a shell runs any command",
    "sh": "a shell runs any command",
    "cmd": "a shell runs any command",
    "powershell": "a shell runs any command",
    "pwsh": "a shell runs any command",
}

#: tar's long options that take their value as the next word (so the value
#: is not taken for a member to add).
_TAR_LONG_VALUES = frozenset({
    "--file", "--directory", "--exclude", "--exclude-from", "--transform",
    "--xform", "--owner", "--group", "--mode", "--mtime", "--newer",
    "--after-date", "--newer-mtime", "--label", "--format", "--blocking-factor",
    "--record-size", "--starting-file", "--checkpoint", "--suffix",
    "--strip-components", "--level", "--sort", "--warning", "--quoting-style",
    "--exclude-tag", "--exclude-tag-all", "--exclude-tag-under",
})
_TAR_CREATE_LONG = frozenset({"--create", "--append", "--update", "--catenate", "--concatenate"})


class _ShellArgumentRefused(ValueError):
    """An option shell_exec refuses, found while reading the values."""


# ── the values a command carries ─────────────────────────────────────────


def path_candidates(binary: str, words: list[str]) -> list[tuple[str, str]]:
    """``(word, value)`` for every value of *words* that may name a file.

    The word itself, a short option's attached value (``-oC:\\x.bat``), every
    tail of a short cluster (which letter takes a value depends on the
    program) and a long option's ``=value``. Raises ``ValueError`` (with the
    message the tool shows) for an unsupported short option or a refused
    attached one.
    """
    out: list[tuple[str, str]] = []
    for word in words:
        if not word:
            continue
        values = [word]
        for flag, value in short_option_values(binary, word):
            if flag in _BLOCKED_ATTACHED.get(binary, set()):
                raise _ShellArgumentRefused(f"'{flag}' is not allowed for {binary}; use a native tool.")
            if binary == "sort" and flag in ("-t", "-k"):
                continue  # a field separator or key, never a path
            values.append(value)
        # One character is a delimiter (``cut -d/``), not a path.
        values.extend(v for v in attached_values(word) if len(v) > 1)
        if word.startswith("-") and "=" in word:
            values.append(word.split("=", 1)[1])
        out.extend((word, value) for value in values if value and not value.startswith("-"))
    return out


# ── the folders a command walks ──────────────────────────────────────────


def _takes_value(flag: str, long_values: frozenset[str]) -> bool:
    return matches_blocked(flag, long_values)


def _operands(words: list[str], spec: _Walk) -> list[str]:
    """The words that are not options or option values."""
    out: list[str] = []
    i = 0
    only = False
    while i < len(words):
        word = words[i]
        if only or not word.startswith("-") or word == "-":
            out.append(word)
            i += 1
            continue
        if word == "--":
            only = True
            i += 1
            continue
        if word.startswith("--"):
            if "=" not in word and _takes_value(word, spec.long_values):
                i += 1
            i += 1
            continue
        letters = word[1:]
        for j, ch in enumerate(letters):
            if ch in spec.short_values:
                if j == len(letters) - 1:
                    i += 1  # the value is the next word
                break
        i += 1
    return out


def _cluster_letters(words: list[str], spec: _Walk) -> str:
    """Every short switch letter the words set (before a value letter)."""
    letters = ""
    for word in words:
        if word == "--":
            break
        if not word.startswith("-") or word.startswith("--") or word == "-":
            continue
        for ch in word[1:]:
            if ch in spec.short_values:
                break
            letters += ch
    return letters


def _has_long(words: list[str], flags: frozenset[str] | tuple[str, ...]) -> bool:
    for word in words:
        if word == "--":
            return False
        if word in flags or matches_blocked(word.split("=", 1)[0], tuple(f for f in flags if "=" not in f)):
            return True
    return False


def _grep_walks(words: list[str]) -> bool:
    spec = WALKS["grep"]
    if any(ch in spec.short_flags for ch in _cluster_letters(words, spec)):
        return True
    if _has_long(words, spec.long_flags):
        return True
    for i, word in enumerate(words):
        if word in ("-d", "--directories") and i + 1 < len(words) and words[i + 1] == "recurse":
            return True
        if word == "-drecurse":
            return True
    return False


def _grep_paths(words: list[str]) -> list[str]:
    ops = _operands(words, WALKS["grep"])
    named_pattern = any(
        w in ("-e", "--regexp", "--file") or w.startswith(("--regexp=", "--file="))
        or (w.startswith("-") and not w.startswith("--") and "e" in w[1:2])
        for w in words
    )
    return ops if named_pattern else ops[1:]


def _find_starts(words: list[str]) -> list[str]:
    i = 0
    while i < len(words):
        word = words[i]
        if word in ("-H", "-L", "-P") or word.startswith("-O"):
            i += 1
        elif word == "-D":
            i += 2
        else:
            break
    starts: list[str] = []
    for word in words[i:]:
        if word.startswith(("-", "(", "!", ",")) or word == ")":
            break
        starts.append(word)
    return starts or ["."]


def _tar_walk(words: list[str], cwd: Path) -> list[tuple[str, Path]]:
    tar = _Walk(short_values="fCIT", long_values=_TAR_LONG_VALUES)
    letters = _cluster_letters(words, tar)
    create = "c" in letters or any(
        w in _TAR_CREATE_LONG or w in ("-r", "-u", "-A") for w in words
    )
    if not create or "--no-recursion" in words:
        return []
    bases = [cwd]
    found: list[tuple[str, Path]] = []
    for i, word in enumerate(words):
        value = None
        if word in ("-C", "--directory") and i + 1 < len(words):
            value = words[i + 1]
        elif word.startswith("--directory="):
            value = word.split("=", 1)[1]
        elif word.startswith("-C") and len(word) > 2 and not word.startswith("--"):
            value = word[2:]
        if value:
            base = Path(value) if os.path.isabs(value) else bases[-1] / value
            bases.append(base)
            found.append((word, base))
    for op in _operands(words, tar):
        for base in bases:
            found.append((op, Path(op) if os.path.isabs(op) else base / op))
    return found


def _git_bases(words: list[str], cwd: Path) -> list[Path]:
    """The folders a git command works in: after its -C options, and its work tree."""
    base = cwd
    tree: Path | None = None
    i = 0
    while i < len(words):
        word = words[i]
        if not word.startswith("-"):
            break
        flag, has_value, value = word.partition("=")
        if flag == "-C" and i + 1 < len(words):
            target = words[i + 1]
            base = Path(target) if os.path.isabs(target) else base / target
            i += 2
            continue
        if flag == "--work-tree":
            if not has_value and i + 1 < len(words):
                value = words[i + 1]
                i += 1
            tree = Path(value) if os.path.isabs(value) else base / value
        i += 1
    return [base] + ([tree] if tree is not None else [])


def _git_walk(sub: str, sub_words: list[str]) -> tuple[list[str], bool]:
    """``(pathspecs walked, relocates)`` of a git subcommand that reads
    untracked or ignored files of the work tree."""
    plain = _Walk(short_values="m", long_values=frozenset({"--message", "--pathspec-from-file", "--chmod"}))
    letters = _cluster_letters(sub_words, plain)
    ops = _operands(sub_words, plain)
    if sub == "add":
        everything = "A" in letters or any(w in ("--all", "--no-ignore-removal") for w in sub_words)
        return (ops or (["."] if everything else [])), False
    if sub == "stash":
        untracked = "u" in letters or "a" in letters or any(
            w in ("--include-untracked", "--all") for w in sub_words
        )
        if not untracked:
            return [], False
        paths = [o for o in ops if o not in ("push", "save")]
        return (paths or ["."]), True
    if sub == "grep":
        if not any(w in ("--no-index", "--untracked", "--no-exclude-standard") for w in sub_words):
            return [], False
        grep = _Walk(short_values="efABCm", long_values=frozenset({"-e", "--max-count", "--context"}))
        named = any(w == "-e" or w.startswith("-e") for w in sub_words if not w.startswith("--"))
        paths = _operands(sub_words, grep)
        paths = paths if named else paths[1:]
        return (paths or ["."]), False
    if sub == "diff" and "--no-index" in sub_words:
        return ops, False
    return [], False


def _walked_folders(
    binary: str, sub: str, words: list[str], cwd: Path,
) -> list[tuple[str, Path, bool, bool]]:
    """``(word, folder, relocates, follows links)`` for each folder *words* make the program walk.

    A folder that does not exist (yet) is left to the program. The words
    are the command's words after the program; *sub* is git's subcommand.
    """
    found: list[tuple[str, Path, bool, bool]] = []
    if binary == "find":
        follows = any(w in ("-L", "-follow") for w in words)
        for start in _find_starts(words):
            found.append((start, cwd / start if not os.path.isabs(start) else Path(start), False, follows))
        return found
    if binary == "tar":
        return [(w, p, False, False) for w, p in _tar_walk(words, cwd)]
    if binary == "git":
        try:
            index, _sub = split_subcommand("git", words)
        except ValueError:
            return []
        paths, relocates = _git_walk(sub, words[index + 1:])
        out: list[tuple[str, Path, bool, bool]] = []
        for base in _git_bases(words[:index], cwd):
            for p in paths:
                out.append((p, Path(p) if os.path.isabs(p) else base / p, relocates, False))
        return out
    spec = WALKS.get(binary)
    if spec is None:
        return []
    letters = _cluster_letters(words, spec)
    walks = spec.always or (
        _grep_walks(words) if binary == "grep"
        else any(ch in spec.short_flags for ch in letters) or _has_long(words, spec.long_flags)
    )
    if not walks:
        return []
    follows = (
        any(ch in spec.follows_short for ch in letters)
        or _has_long(words, spec.follows_long)
        or (bool(spec.follows_unless) and not any(w in spec.follows_unless for w in words))
    )
    paths = _grep_paths(words) if spec.pattern_first else _operands(words, spec)
    if not paths and spec.no_path_is_cwd:
        paths = ["."]
    for p in paths:
        found.append((p, Path(p) if os.path.isabs(p) else cwd / p, spec.relocates, follows))
    return found


# ── the checks ───────────────────────────────────────────────────────────


def _real(path: Path | str) -> Path:
    from kazma_core.workspace.real_paths import real_path

    return real_path(path)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _kazma_file_refusal(binary: str, word: str, value: str, real: Path, write: bool) -> str | None:
    from kazma_core.store_registry import (
        is_kazma_config_file,
        is_kazma_secret_file,
        is_kazma_store,
        store_refusal,
    )

    if not is_kazma_store(real, write=write):
        return None
    if not is_kazma_secret_file(real) and write and is_kazma_config_file(real):
        head = f"Kazma configuration file — {binary} may not change it (argument '{word}'). "
    else:
        head = "Kazma control-plane store — shell_exec may not open it. "
    return head + store_refusal(real, door="shell_exec")


def _expand_braces(value: str) -> list[str]:
    """``a{b,c}d`` -> ``abd``, ``acd`` (a program's own globbing may expand braces)."""
    match = re.search(r"\{([^{},]*,[^{}]*)\}", value)  # to the first comma: linear
    if not match:
        return [value]
    out: list[str] = []
    for part in match.group(1).split(","):
        out.extend(_expand_braces(value[: match.start()] + part + value[match.end():]))
        if len(out) > 64:
            break
    return out[:64]


def _wildcard_refusal(
    binary: str, word: str, value: str, lexical: Path, root: Path, cwd_text: str, write: bool,
) -> str | None:
    """A wildcard is the files it matches: a program on Windows expands its
    own (``FindFirstFile`` matches long AND short names), so a wildcard that
    matches one of Kazma's files by either name is refused, and every file
    it matches is checked like a named one."""
    import fnmatch

    from kazma_core.store_registry import kazma_files_in, store_refusal
    from kazma_core.workspace.real_paths import short_name

    folder_part, last = os.path.split(str(lexical))
    folders = (
        [Path(f) for f in _glob.glob(folder_part, include_hidden=True)]
        if _GLOB_MAGIC.search(folder_part)
        else [Path(folder_part)]
    )
    pattern = last.lower()
    for folder in folders[:256]:
        real_folder = _real(folder)
        if real_folder != root and not _under(real_folder, root):
            return (
                f"path '{value}' (argument '{word}') is outside the workspace "
                f"({cwd_text}). Paths must stay inside the workspace."
            )
        for kazma_file in kazma_files_in(real_folder, write=write):
            names = [kazma_file.name, short_name(kazma_file) or ""]
            if any(n and fnmatch.fnmatchcase(n.lower(), pattern) for n in names):
                return (
                    f"'{value}' (argument '{word}') matches {kazma_file.name}, one of "
                    "Kazma's own files (programs expand wildcards themselves, by long "
                    "and by short name). " + store_refusal(kazma_file, door="shell_exec")
                )
    for match in _glob.glob(str(lexical), include_hidden=True)[:1024]:
        refused = _path_refusal(binary, word, value, Path(match), root, cwd_text, write)
        if refused:
            return refused
    return None


def _one_value_refusal(
    binary: str, word: str, value: str, root: Path, cwd_text: str, write: bool,
) -> str | None:
    from kazma_core.workspace.remote_paths import remote_path_reason

    # Before anything resolves it: resolving a network path connects to the
    # host it names (workspace/remote_paths).
    remote = remote_path_reason(value)
    if remote:
        return f"{remote} (argument '{word}')"
    text = os.path.expanduser(value) if value.startswith("~") else value
    lexical = Path(text) if os.path.isabs(text) else Path(os.path.join(str(root), text))
    if _GLOB_MAGIC.search(text):
        return _wildcard_refusal(binary, word, value, lexical, root, cwd_text, write)
    return _path_refusal(binary, word, value, lexical, root, cwd_text, write)


def _path_refusal(
    binary: str, word: str, value: str, lexical: Path, root: Path, cwd_text: str, write: bool,
) -> str | None:
    real = _real(lexical)
    refused = _kazma_file_refusal(binary, word, value, real, write)
    if refused:
        return refused
    if real != root and not _under(real, root):
        lexical_inside = _under(Path(os.path.normpath(str(lexical))), root)
        via = f" (a link or junction leads it to {real})" if lexical_inside else ""
        return (
            f"path '{value}' (argument '{word}') is outside "
            f"the workspace ({cwd_text}){via}. Paths must stay "
            "inside the workspace."
        )
    return None


def _link_out(folder: Path, root: Path, budget: int = 20_000) -> Path | str | None:
    """A link or junction under *folder* that leads out of *root*, "budget" when
    the folder is too large to look through, or None."""
    from kazma_core.workspace.real_paths import is_linked, real_path

    seen = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        here = Path(dirpath)
        kept: list[str] = []
        for name in dirnames + filenames:
            seen += 1
            child = here / name
            if is_linked(child):
                if not _under(real_path(child), root):
                    return child
            elif name in dirnames:
                kept.append(name)
        dirnames[:] = [d for d in dirnames if d in kept]
        if seen >= budget:
            return "budget"
    return None


def _folder_refusal(
    binary: str, word: str, folder: Path, relocates: bool, follows: bool, root: Path, cwd_text: str,
) -> str | None:
    from kazma_core.store_registry import kazma_file_under, store_refusal

    real = _real(folder)
    if not real.is_dir():
        return None
    if real != root and not _under(real, root):
        return (
            f"path '{word}' is outside the workspace ({cwd_text}). "
            "Paths must stay inside the workspace."
        )
    found = kazma_file_under(real, write=relocates)
    if found is not None:
        verb = "moves or rewrites" if relocates else "reads"
        what = (
            f"{found.name}, Kazma's own folder of stores and secrets"
            if found.is_dir() else found.name
        )
        why = "" if found.is_dir() else " " + store_refusal(found, door="shell_exec")
        return (
            f"'{word}' ({real}) holds Kazma's own files ({what}), and {binary} "
            f"{verb} everything under the folders it is given. Name a folder that "
            "does not hold them, or use file_search / file_list, which leave "
            "Kazma's files out." + why
        )
    if follows:
        out = _link_out(real, root)
        if out == "budget":
            return (
                f"'{word}' holds too many files to check for links leading out of the "
                f"workspace, and {binary} follows links here. Name a smaller folder."
            )
        if out is not None:
            return (
                f"'{word}' holds a link or junction ({out}) that leads outside the "
                f"workspace ({cwd_text}), and {binary} follows links here."
            )
    return None


def _copy_targets(binary: str, words: list[str], cwd: Path) -> list[tuple[str, Path]]:
    """``(word, file it creates)`` for cp/mv: a source copied into a folder
    lands there under its own name."""
    spec = WALKS[binary]
    ops = _operands(words, spec)
    target_dir: str | None = None
    for i, word in enumerate(words):
        if word in ("-t", "--target-directory") and i + 1 < len(words):
            target_dir = words[i + 1]
        elif word.startswith("--target-directory="):
            target_dir = word.split("=", 1)[1]
        elif word.startswith("-t") and len(word) > 2 and not word.startswith("--"):
            target_dir = word[2:]
    if target_dir is not None:
        dest, sources = target_dir, [o for o in ops if o != target_dir]
    elif len(ops) >= 2:
        dest, sources = ops[-1], ops[:-1]
    else:
        return []
    dest_path = Path(dest) if os.path.isabs(dest) else cwd / dest
    if not _real(dest_path).is_dir():
        return []
    out: list[tuple[str, Path]] = []
    for src in sources:
        src_path = Path(src) if os.path.isabs(src) else cwd / src
        if src.replace("\\", "/").endswith("/.") and src_path.is_dir():
            try:
                names = [entry.name for entry in os.scandir(src_path)][:4096]
            except OSError:
                names = []
            out.extend((src, dest_path / name) for name in names)
        else:
            name = Path(os.path.normpath(src)).name
            if name:
                out.append((src, dest_path / name))
    return out


def _unzip_targets(words: list[str], cwd: Path) -> list[tuple[str, Path]] | str:
    """``(member, file it creates)`` for an extracting unzip, or why it is refused."""
    if any(
        w.startswith("-") and not w.startswith("--") and any(ch in w[1:] for ch in "ltvpcZz")
        for w in words
    ):
        return []  # lists, tests or pipes: writes nothing
    dest = cwd
    archive: str | None = None
    i = 0
    while i < len(words):
        word = words[i]
        if word == "-d" and i + 1 < len(words):
            dest = Path(words[i + 1]) if os.path.isabs(words[i + 1]) else cwd / words[i + 1]
            i += 2
            continue
        if word.startswith("-d") and len(word) > 2:
            value = word[2:]
            dest = Path(value) if os.path.isabs(value) else cwd / value
        elif not word.startswith("-") and archive is None:
            archive = word
        i += 1
    if archive is None:
        return []
    path = Path(archive) if os.path.isabs(archive) else cwd / archive
    if not path.is_file() and path.with_suffix(".zip").is_file():
        path = path.with_suffix(".zip")
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()[:20_000]
    except (OSError, zipfile.BadZipFile, ValueError):
        return []  # unzip cannot read it either
    out: list[tuple[str, Path]] = []
    for name in names:
        parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
        if parts:
            out.append((name, dest.joinpath(*parts)))
    return out


def path_refusal(
    binary: str, sub: str, words: list[str], candidates: list[tuple[str, str]], cwd: str | Path,
) -> str | None:
    """Why the values and folders of a command are refused, or None.

    *words* are the command's words after the program, *candidates* its
    values (:func:`path_candidates`), *cwd* the workspace it runs in.
    Blocking: it resolves paths and may walk a folder.
    """
    from kazma_core.store_registry import is_kazma_store

    cwd_text = str(cwd)
    root = _real(cwd)
    write = binary not in READ_ONLY_PROGRAMS
    for word, value in candidates:
        for expanded in _expand_braces(value):
            refused = _one_value_refusal(binary, word, expanded, root, cwd_text, write)
            if refused:
                return refused
    for word, folder, relocates, follows in _walked_folders(binary, sub, words, root):
        refused = _folder_refusal(binary, word, folder, relocates, follows, root, cwd_text)
        if refused:
            return refused
    if binary == "git":
        # A checkout or merge writes files it does not name: the other
        # commit's, an ignored kazma.local.yaml included.
        from kazma_core.safety.git_tree_writes import git_command_refusal

        refused = git_command_refusal(words, root)
        if refused:
            return refused
    created: list[tuple[str, Path]] = []
    if binary in ("cp", "mv"):
        created = _copy_targets(binary, words, root)
    elif binary == "unzip":
        targets = _unzip_targets(words, root)
        created = targets if isinstance(targets, list) else []
    for word, target in created:
        real = _real(target)
        if is_kazma_store(real, write=True):
            from kazma_core.store_registry import store_refusal

            return (
                f"{binary} would write '{real.name}' ({word}), which is Kazma's own "
                "file. " + store_refusal(real, door="shell_exec")
            )
        if real != root and not _under(real, root):
            return (
                f"{binary} would write '{target}' ({word}) outside the workspace "
                f"({cwd_text})."
            )
    return None


def read_paths(
    binary: str, sub: str, words: list[str], candidates: list[tuple[str, str]], cwd: str | Path,
) -> list[Path]:
    """The files and folders a command's values name and its walks enter,
    as :func:`path_refusal` reads them (a wildcard expanded, at most 500
    matches). Blocking. ``shell_exec`` asks it whether the command read a
    page Kazma saved from the web, to fence what it printed."""
    import glob

    root = _real(cwd)
    found: list[Path] = []
    for _word, value in candidates:
        for expanded in _expand_braces(value):
            lexical = Path(expanded) if os.path.isabs(expanded) else root / expanded
            if any(ch in expanded for ch in "*?["):
                found += [Path(m) for m in glob.glob(str(lexical))[:500]]
            else:
                found.append(lexical)
    for _word, folder, _relocates, _follows in _walked_folders(binary, sub, words, root):
        found.append(folder)
    return found


# ── before the approval card ─────────────────────────────────────────────


def _shell_target_refusal_sync(command: str) -> str | None:
    """Why ``shell_exec`` would refuse *command*'s values, or None (blocking)."""
    try:
        words = split_command_line(command)
    except ValueError:
        return None  # the tool says why
    if not words:
        return None
    binary = program_name(words[0])
    if binary not in WALKS and binary not in NO_WALK:
        # Not a program the tool runs at all: its own refusal says so, with
        # the hint for it (`cd`: commands already run in the workspace).
        return None
    rest = words[1:]
    try:
        index, sub = split_subcommand(binary, rest)
    except ValueError as exc:
        return str(exc)
    try:
        candidates = path_candidates(binary, rest)
    except ValueError as exc:
        return str(exc)
    try:
        from kazma_core.workspace.binding import resolve_active_root

        cwd = resolve_active_root()
    except (OSError, RuntimeError, ValueError, ImportError):
        logger.debug("[shell_paths] no workspace before the card", exc_info=True)
        return None  # the tool checks again where it runs
    refused = path_refusal(binary, sub, rest, candidates, cwd)
    if refused:
        return refused
    return argument_refusal(binary, sub, rest, rest[index + 1:])


async def shell_target_refusal(command: str) -> str | None:
    """:func:`_shell_target_refusal_sync` off the event loop (it reads the
    workspace store and the filesystem)."""
    return await asyncio.to_thread(_shell_target_refusal_sync, command)
