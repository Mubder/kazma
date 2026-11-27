"""The git commands a door runs with no approval card: an allowlist of forms.

Two doors run git with no card: the IDE's git (``/api/ide/git``, the chat
command ``/ide git``, the TUI) and the agent's git read tools. A command runs
there only when it is one of the forms in :data:`CARD_LESS` -- a verb, the
options listed for it, each value inside its bound, operands of the kind the
form takes -- and, for a read that prints a file's contents, only when
``safety.git_kazma_files`` plans it in the repository. Everything else goes
to the approval card (``shell_exec``, danger tier), which shows the exact
command: an unknown verb, option, value or operand is not refused, it is
asked. Until 2026-11-27 the rule listed bad forms and refused them, and each
review found another form git accepts.

Gate: ``tests/test_git_reads_print_no_kazma_file.py`` (it enumerates
:data:`CARD_LESS`, and probes that every other verb and option asks).
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

__all__ = [
    "CARD_LESS",
    "PATCH_OPTIONS",
    "ReadForm",
    "card_less_form",
    "card_less_shape",
    "kazma_operand_refusal",
    "prints_patches",
    "split_git_command",
    "validate_read_paths",
]


@dataclass(frozen=True)
class _Form:
    """One card-less shape of a git verb.

    *operands*: ``"none"``; ``"names"`` (refs, revisions, patterns: the verb
    prints names and ids); ``"paths"`` (pathspecs); ``"revisions"``
    (revisions, then paths: diff, show, log); ``"one-file"`` (one work-tree
    file, no revision: blame). *contents*: the verb prints what files hold
    (always, or for log only with a patch option): it runs only as
    ``safety.git_kazma_files`` plans it."""

    flags: frozenset[str]
    values: frozenset[str]
    operands: str
    contents: bool = False


class ReadForm(NamedTuple):
    """A card-less read, laid out: the verb (with ``stash``'s own word), its
    options in order (a value joined to its option), the operands before
    ``--`` and those after it (None when there is no ``--``)."""

    head: list[str]
    options: list[str]
    before: list[str]
    after: list[str] | None


#: Options that make a read print file contents in patch form.
PATCH_OPTIONS = frozenset({"-p", "--patch", "--binary", "--unified", "-U"})
_NAMES_OUTPUT = frozenset({
    "--stat", "--numstat", "--shortstat", "--name-only", "--name-status",
    "--summary", "--raw", "-s", "--no-patch",
})
_DIFF_FLAGS = _NAMES_OUTPUT | {"-p", "--patch", "--binary"} | {
    "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames",
    "--ignore-space-at-eol", "--ignore-all-space", "--ignore-space-change",
    "-w", "-b", "--exit-code", "--quiet",
}
_LOG_FLAGS = _DIFF_FLAGS | {
    "--oneline", "--all", "--graph", "--decorate", "--no-decorate", "--reverse",
    "--first-parent", "--no-merges", "--merges", "--date-order", "--topo-order",
}

#: Not with a patch on log: ``--all`` walks the notes' and the stash's refs,
#: and a graph prefixes every line, so a section cannot be told by its header.
_NOT_WITH_A_PATCH = frozenset({"--all", "--graph"})

#: The card-less forms, by verb (``stash list`` by its two words). Branch and
#: tag only list (:func:`_lists_only`). Every other verb -- ``remote``
#: (its URLs can carry a password), ``stash show``, ``cat-file``, ``grep``,
#: ``archive``, ``notes``, ``reflog``... -- goes to the card.
CARD_LESS: dict[str, _Form] = {
    "status": _Form(frozenset({"-s", "--short", "-b", "--branch", "-sb", "-bs", "--porcelain", "-z", "--ignored"}),
                   frozenset({"--porcelain", "--untracked-files", "--ignored"}), "paths"),
    "branch": _Form(frozenset({"--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose", "-av", "-rv",
                              "--show-current", "--no-color"}),
                   frozenset({"--format", "--sort"}), "names"),
    "tag": _Form(frozenset({"-l", "--list", "--no-color"}), frozenset({"--format", "--sort"}), "names"),
    "rev-parse": _Form(frozenset({"--abbrev-ref", "--show-toplevel", "--show-prefix", "--verify", "--quiet", "--short",
                                 "--is-inside-work-tree", "--is-bare-repository", "--symbolic", "--symbolic-full-name"}),
                      frozenset({"--short"}), "names"),
    "ls-files": _Form(frozenset({"--cached", "--others", "--modified", "--deleted", "--stage", "--unmerged",
                                "--exclude-standard", "-z", "-c", "-m", "-d", "-s", "-o"}),
                     frozenset(), "paths"),
    "describe": _Form(frozenset({"--always", "--tags", "--all", "--long", "--exact-match", "--first-parent"}),
                     frozenset({"--abbrev", "--match", "--exclude"}), "names"),
    "rev-list": _Form(frozenset({"--count", "--all", "--reverse", "--first-parent", "--no-merges", "--merges",
                                "--left-right", "--boundary"}),
                     frozenset({"-n", "--max-count"}), "names"),
    "shortlog": _Form(frozenset({"-s", "-n", "-e", "-sn", "-ns", "--summary", "--numbered", "--email", "--all"}),
                     frozenset({"--group"}), "names"),
    "version": _Form(frozenset({"--build-options"}), frozenset(), "none"),
    "stash list": _Form(_NAMES_OUTPUT | {"--oneline"}, frozenset({"-n", "--max-count", "--format", "--pretty"}), "none"),
    "log": _Form(_LOG_FLAGS, frozenset({"-n", "--max-count", "--pretty", "--format", "--date", "--since", "--until",
                                       "--author", "--grep"}), "revisions", contents=True),
    "diff": _Form(_DIFF_FLAGS | {"--cached", "--staged"}, frozenset({"--unified", "-U", "--diff-filter"}),
                 "revisions", contents=True),
    "show": _Form(_DIFF_FLAGS | {"--oneline"}, frozenset({"--unified", "-U", "--pretty", "--format", "--date"}),
                 "revisions", contents=True),
    "blame": _Form(frozenset({"--porcelain", "--line-porcelain", "--incremental", "--no-textconv", "-w", "-l", "-s",
                             "-e", "--show-email", "--show-number"}),
                  frozenset({"-L"}), "one-file", contents=True),
}

#: The pretty formats git builds in (a user-defined ``pretty.<name>`` alias
#: is the repository's format, which may print notes).
_PRETTY_NAMES = frozenset({"oneline", "short", "medium", "full", "fuller", "reference", "email", "mboxrd", "raw"})
#: Placeholders a bounded log format may use: a commit's ids, people, dates,
#: refs and message. Never ``%N`` (notes), ``%G`` (runs the signature
#: program), ``%g`` (reflog), ``%(...)`` or colours.
_PLACEHOLDER = re.compile(
    r"%(?:[+\- ]?(?:H|h|T|t|P|p|a[nNeElLdDrtiIsh]|c[nNeElLdDrtiIsh]|d|D|S|e|s|f|b|B|n|m|%|x[0-9a-fA-F]{2})"
    r"|w\(\d*(?:,\d*){0,2}\))"
)
#: Atoms a bounded branch or tag format (or sort key) may use: names, ids,
#: people, dates and subjects. Never ``raw`` (an object's bytes, a blob's
#: too), ``contents``/``body``/``trailers`` or ``signature`` (runs the
#: signature program).
_REF_ATOMS = frozenset({
    "refname", "objectname", "objecttype", "upstream", "push", "HEAD", "symref", "worktreepath",
    "subject", "authorname", "authoremail", "authordate", "committername", "committeremail",
    "committerdate", "creatordate", "creator", "taggername", "taggeremail", "taggerdate", "tag",
    "type", "object",
})
_ATOM = re.compile(r"%\(\*?([A-Za-z-]+)(?::[A-Za-z0-9=,._-]*)?\)")


def _bounded_pretty(value: str) -> bool:
    """A log format with no placeholder but :data:`_PLACEHOLDER`'s, or a
    built-in pretty name."""
    if value in _PRETTY_NAMES:
        return True
    body = value.split(":", 1)[1] if value.startswith(("format:", "tformat:")) else value
    if "%" not in body:
        return value.startswith(("format:", "tformat:"))
    return "%" not in _PLACEHOLDER.sub("", body)


def _bounded_ref_format(value: str) -> bool:
    return all(atom in _REF_ATOMS for atom in _ATOM.findall(value)) and "%" not in _ATOM.sub("", value).replace("%%", "")


def _bounded_sort(value: str) -> bool:
    key = value.lstrip("-").lstrip("*").split(":", 1)[0]
    return key in _REF_ATOMS or key in ("version", "v")


_DIGITS = re.compile(r"\d+")
_VALUE_BOUNDS: dict[str, re.Pattern[str]] = {
    "-n": _DIGITS, "--max-count": _DIGITS, "-U": _DIGITS, "--unified": _DIGITS,
    "--short": _DIGITS, "--abbrev": _DIGITS,
    "--untracked-files": re.compile(r"no|normal|all"),
    "--ignored": re.compile(r"traditional|no|matching"),
    "--porcelain": re.compile(r"v1|v2"),
    "--diff-filter": re.compile(r"[ACDMRTUXBacdmrtuxb*]+"),
    "--group": re.compile(r"author|committer|trailer:[A-Za-z0-9-]+"),
}


def _within_bound(verb: str, flag: str, value: str) -> bool:
    """Whether *value* is inside *flag*'s bound for *verb*."""
    if flag in ("--format", "--pretty"):
        return _bounded_ref_format(value) if verb in ("branch", "tag") else _bounded_pretty(value)
    if flag == "--sort":
        return _bounded_sort(value)
    bound = _VALUE_BOUNDS.get(flag)
    return bound is None or bool(bound.fullmatch(value))


def split_git_command(command: str) -> list[str]:
    """Use the same Windows quoting convention as the post-approval shell."""
    if any(c in command for c in ("\x00", "\r", "\n")):
        raise ValueError("Git commands must be a single argument line")
    parts = shlex.split(command.strip(), posix=os.name != "nt")
    if os.name == "nt":
        parts = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]
    return parts


def _lists_only(verb: str, rest: list[str]) -> bool:
    """branch and tag list with no operand, or with ``--list`` (``-l``) and
    patterns; with any other operand they create."""
    listing = {"--list"} | ({"-l"} if verb == "tag" else set())
    return bool(listing & set(rest)) or all(p.startswith("-") for p in rest)


def _form_key(parts: list[str]) -> str | None:
    if not parts:
        return None
    verb, rest = parts[0], parts[1:]
    if verb == "stash":
        return "stash list" if rest[:1] == ["list"] else None
    if verb in ("branch", "tag") and not _lists_only(verb, rest):
        return None
    return verb if verb in CARD_LESS else None


def _joined(flag: str, value: str) -> str:
    return f"{flag}={value}" if flag.startswith("--") else f"{flag}{value}"


def card_less_shape(parts: list[str]) -> ReadForm | None:
    """*parts* laid out when they are one of the card-less forms by their
    words alone (verb, options, values, operands): else None, and the
    command goes to the approval card. The repository's own check comes
    after (``safety.git_kazma_files``), for a form that prints contents."""
    laid = _layout(parts)
    if laid is None:
        return None
    form, read = laid
    operands = [*read.before, *(read.after or [])]
    if form.operands == "none" and operands:
        return None
    if form.operands == "one-file" and len(operands) != 1:
        return None
    verb = read.head[0]
    if verb == "log" and prints_patches(verb, read.options) and _NOT_WITH_A_PATCH & set(read.options):
        return None
    if form.contents and any(_names_an_object(op) for op in operands):
        return None
    return read


def _layout(parts: list[str]) -> tuple[_Form, ReadForm] | None:
    """*parts*' form and layout when the verb and every option are listed,
    each value inside its bound; the operands are not judged here."""
    key = _form_key(parts)
    if key is None:
        return None
    form = CARD_LESS[key]
    head = key.split(" ")
    verb, rest = head[0], parts[len(head):]
    options: list[str] = []
    before: list[str] = []
    after: list[str] | None = None
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg == "--":
            after = list(rest[i + 1:])
            break
        if not arg.startswith("-") or arg == "-":
            before.append(arg)
        else:
            flag, separator, value = arg.partition("=")
            if separator:
                if flag not in form.values or not value or not _within_bound(verb, flag, value):
                    return None
                options.append(arg)
            elif arg in form.flags:
                options.append(arg)
            elif arg in form.values:
                i += 1
                if i >= len(rest) or rest[i].startswith("-") or not _within_bound(verb, arg, rest[i]):
                    return None
                options.append(_joined(arg, rest[i]))
            elif verb in ("log", "rev-list") and re.fullmatch(r"-\d+", arg):
                options.append(arg)
            elif verb in ("diff", "show") and re.fullmatch(r"-U\d+", arg):
                options.append(arg)
            else:
                return None
        i += 1
    return form, ReadForm(head, options, before, after)


def _names_an_object(operand: str) -> bool:
    """``rev:path``, ``:path``, ``:/text`` or pathspec magic: an object or a
    search, not a commit and a path. A Windows drive letter is a path."""
    if os.name == "nt" and re.match(r"^[A-Za-z]:[\\/]", operand):
        return ":" in operand[2:]
    return ":" in operand


def prints_patches(verb: str, options: list[str]) -> bool:
    """Whether a diff, show or log with *options* prints patches (a file's
    contents): log only with a patch option, diff and show unless a names
    output (``--stat``, ``--name-only``...) replaces the patch."""
    names = {o.split("=", 1)[0] for o in options}
    patch = bool(names & PATCH_OPTIONS) or any(re.fullmatch(r"-U\d+", o) for o in options)
    if verb == "log":
        return patch
    if verb in ("diff", "show"):
        return patch or not (names & _NAMES_OUTPUT)
    return False


def card_less_form(parts: list[str], root: Path) -> ReadForm | None:
    """:func:`card_less_shape`, with every operand checked in the workspace
    *root* (:func:`validate_read_paths`); None sends the command to the
    approval card. Blocking: it resolves paths."""
    shape = card_less_shape(parts)
    if shape is None:
        return None
    try:
        validate_read_paths(parts, root)
    except ValueError:
        return None
    return shape


def kazma_operand_refusal(parts: list[str], root: Path) -> str | None:
    """The store refusal for the first word of *parts* after the verb that
    names one of Kazma's files (for ``rev:path`` its path part), or None.

    Asked of every IDE git command, card-less or not, before it runs or goes
    to the card: the one predicate refuses Kazma's files to every door
    (``store_registry.is_kazma_store``), and the card would refuse it too. A
    network or device path is never resolved here (the card's door refuses
    it on its text). Blocking: it resolves paths."""
    from kazma_core.store_registry import is_kazma_store, store_refusal
    from kazma_core.workspace.remote_paths import remote_path_reason

    for word in parts[1:]:
        if word.startswith("-") or remote_path_reason(word):
            continue
        path = word
        if ":" in path and not (len(path) > 1 and path[1] == ":" and os.name == "nt"):
            path = path.split(":", 1)[1]
        if not path or remote_path_reason(path):
            continue
        candidate = Path(path) if Path(path).is_absolute() else root / path
        if is_kazma_store(candidate, write=False):
            return store_refusal(candidate, door="the IDE's git")
    return None


#: A reflog selector (``HEAD@{1}``, ``main@{yesterday}``): a commit the
#: repository's refs may no longer reach. ``@{u}``, ``@{upstream}``,
#: ``@{push}`` and ``@{-N}`` name branches.
_REFLOG_SELECTOR = re.compile(r"@\{(?!(?:u|upstream|push|-\d+)\})", re.IGNORECASE)


def validate_read_paths(parts: list[str], root: Path) -> None:
    """Check path-shaped operands even when Git also accepts revision syntax.

    Every operand is confined to the workspace and asked
    ``store_registry.is_kazma_store``. A network or device path is refused on
    its text, before anything resolves it (resolving one connects to its
    host). Pathspec magic and reflog selectors are refused. Raises
    ValueError; a door sends what this refuses to the approval card.
    """
    from kazma_core.workspace.remote_paths import remote_path_reason

    laid = _layout(parts)
    if laid is None:
        raise ValueError("Not a card-less git read")
    _form, shape = laid
    for operand in [*shape.before, *(shape.after or [])]:
        if operand.startswith(":(") or operand.startswith(":/"):
            raise ValueError("Git pathspec magic is unsupported in workspace reads")
        if _REFLOG_SELECTOR.search(operand):
            raise ValueError("Read-only git does not read the reflog")
        path = operand
        # HEAD:path names an object; check the path part as well. Drive
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
