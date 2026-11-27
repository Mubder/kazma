"""Running a card-less git read: what it may print, judged in the repository.

``ide.git_policy`` says which commands are card-less forms. Both doors that
run git with no approval card -- the IDE's git (``/api/ide/git``, the chat
command ``/ide git``, the TUI) and the agent's git read tools -- run one
through :func:`run_card_less`, which first judges, in the repository, a form
that prints what files hold (``diff``, ``show``, ``log`` with a patch,
``blame``). A read it does not judge card-less goes to the approval card:

- it runs only in a work tree git itself accepts (``rev-parse
  --is-inside-work-tree``), asked with the read's own argv and environment;
- every revision of a diff or show, and of a log with a patch, is a commit
  (``<rev>^{commit}``) of the repository's own history -- reachable from
  HEAD, a branch, a tag or a remote-tracking branch -- that shares no history
  with a notes ref: git prints each section under a path from the top of a
  commit's tree. A tree, a blob, a note, a stash entry or a commit no ref
  holds goes to the card, and so does a path the read names that is one of
  Kazma's;
- ``blame`` reads one work-tree file that is not one of Kazma's;
- Kazma's paths are kept out at the source (``store_registry.
  kazma_exclude_pathspecs``, at every work tree of the repository), every
  section whose path ``store_registry.kazma_paths_among`` accepts is dropped
  after git runs, and a line says how many items were left out: the same
  read's names, without the exclusions, judged by both rules;
- what shapes a section is fixed: ``--no-replace-objects`` (each door's
  argv), :data:`SECTION_SHAPE` and, for log and show, :data:`NO_NOTES`.

Gate: ``tests/test_git_reads_print_no_kazma_file.py``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

from kazma_core.ide.git_policy import PATCH_OPTIONS, ReadForm, prints_patches

logger = logging.getLogger(__name__)

__all__ = ["NO_NOTES", "SECTION_SHAPE", "Ask", "CardLessRead", "run_card_less"]

#: Runs ``git <args>`` the way the door runs its read (its argv, its
#: environment) and answers ``(exit code, stdout, stderr)``.
Ask = Callable[[Sequence[str]], tuple[int, str, str]]

#: Fixed for every diff, show and log: what a section looks like. A
#: repository's configuration cannot drop the ``a/`` and ``b/`` prefixes,
#: print paths from a subfolder, expand a submodule into its files or run a
#: diff or textconv program.
SECTION_SHAPE = ("--no-color", "--default-prefix", "--no-relative", "--submodule=short",
                 "--no-ext-diff", "--no-textconv")
#: For log and show: a note is a blob with no path.
NO_NOTES = ("--no-notes",)
_HEADERS = ("diff --git ", "diff --cc ", "diff --combined ")
_RANGE = re.compile(r"\.\.\.?")
#: Options the names-only twin of a patch read leaves out: what is printed,
#: not which commits and files.
_OUTPUT_OPTIONS = frozenset({
    *PATCH_OPTIONS, "--stat", "--numstat", "--shortstat", "--name-only", "--name-status", "--summary",
    "--raw", "-s", "--no-patch", "--oneline", "--graph", "--decorate", "--no-decorate", "--exit-code",
    "--format", "--pretty", "--date",
})


class CardLessRead(NamedTuple):
    """What a card-less read printed; *card* says why it goes to the approval
    card instead (it did not run), None when it ran."""

    code: int
    out: str
    err: str
    card: str | None = None


class _Plan(NamedTuple):
    args: list[str]
    tops: tuple[Path, ...] = ()
    twin: list[str] | None = None


def _said_count(count: int) -> str:
    """The "N items not shown" line in the reader's language."""
    try:
        from kazma_ui.i18n import current_language, t_plural
    except ImportError:
        return f"({count} items not shown: Kazma's own files.)"
    return t_plural("ide.git_items_not_shown", count, current_language())


def _work_tree_top(ask: Ask) -> Path | None:
    """The work tree's top when git itself accepts the folder as one."""
    code, answer, _err = ask(["rev-parse", "--is-inside-work-tree", "--show-toplevel"])
    lines = answer.splitlines()
    if code != 0 or len(lines) < 2 or lines[0].strip() != "true" or not lines[1].strip():
        return None
    return Path(lines[1].strip())


def _work_trees(ask: Ask, top: Path) -> tuple[Path, ...]:
    """Every work tree of the repository, *top* first (``git worktree list``):
    the same paths in a commit are the same files at each of them."""
    tops = [top]
    code, listing, _err = ask(["worktree", "list", "--porcelain"])
    if code == 0:
        for line in listing.splitlines():
            if line.startswith("worktree "):
                place = Path(line[len("worktree "):].strip())
                if place not in tops:
                    tops.append(place)
    return tuple(tops)


def _ends(operand: str) -> list[str]:
    """The revisions a revision operand names (both ends of a range)."""
    text = operand.lstrip("^")
    for suffix in ("^@", "^!"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    text = re.sub(r"\^-\d*$", "", text)
    return [end for end in _RANGE.split(text) if end]


def _is_revision(ask: Ask, operand: str) -> bool:
    if operand.startswith("^") or _RANGE.search(operand) or operand.endswith(("^@", "^!")):
        return True
    # A commit first: the question :func:`_commits` asks next, answered once.
    if ask(["rev-parse", "--verify", "--quiet", f"{operand}^{{commit}}"])[0] == 0:
        return True
    return ask(["rev-parse", "--verify", "--quiet", operand])[0] == 0


def _remembered(ask: Ask) -> Ask:
    """*ask*, each question asked of git once while a read is planned."""
    answers: dict[tuple[str, ...], tuple[int, str, str]] = {}

    def once(args: Sequence[str]) -> tuple[int, str, str]:
        key = tuple(args)
        if key not in answers:
            answers[key] = ask(args)
        return answers[key]

    return once


def _commits(ask: Ask, revisions: Sequence[str]) -> tuple[list[str], list[str], str | None]:
    """The commit ids the revisions' ends and HEAD name, the refs that hold
    the repository's own history (HEAD among them when it is a commit), and
    why not, when an end (or HEAD) is no commit: a tree, a blob, a tag on
    either, ``X^{tree}``, AUTO_MERGE."""
    ids: list[str] = []
    for end in (end for operand in revisions for end in _ends(operand)):
        code, found, _err = ask(["rev-parse", "--verify", "--quiet", f"{end}^{{commit}}"])
        if code == 0 and found.strip():
            ids.append(found.strip())
        elif ask(["cat-file", "-t", end])[0] == 0:
            return [], [], f"{end} is not a commit"
        # An end that names no object here (missing, and no lazy fetch)
        # prints nothing: git says so itself.
    holders = ["--branches", "--tags", "--remotes"]
    code, head, _err = ask(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"])
    if code == 0 and head.strip():
        ids.append(head.strip())
        holders.append("HEAD")
    elif ask(["cat-file", "-t", "HEAD"])[0] == 0:
        return [], [], "HEAD is not a commit"
    # A HEAD that names no object here (a new repository, a missing object:
    # no lazy fetch) shows nothing; git says so itself.
    return ids, holders, None


def _held_by_refs(ask: Ask, ids: Sequence[str], holders: Sequence[str]) -> bool:
    """Whether every commit is reachable from a branch, a tag, a
    remote-tracking branch or HEAD: not only from the stash, a reflog, an
    alternate's objects or nothing at all."""
    code, outside, _err = ask(["rev-list", "--max-count=1", *ids, "--not", *holders])
    return code == 0 and not outside.strip()


def _shares_notes_history(ask: Ask, ids: Sequence[str]) -> bool:
    """Whether a commit shares history with a notes ref: a note is a blob
    filed under a commit's id, so a patch of the notes' history prints notes
    under paths that are ids."""
    code, tips, _err = ask(["for-each-ref", "--format=%(objectname)", "refs/notes/"])
    # merge-base A B C: a common ancestor of A and any of B, C.
    return any(ask(["merge-base", tip, *ids])[0] == 0 for tip in (tips.split() if code == 0 else []))


def _not_history(ask: Ask, revisions: Sequence[str]) -> str | None:
    """Why the revisions (and HEAD) are not all commits of the repository's
    own history, or None when they are."""
    ids, holders, why = _commits(ask, revisions)
    if why or not ids:
        return why
    named = ids[:-1] if "HEAD" in holders else ids
    if named and not _held_by_refs(ask, named, holders):
        return "a commit no branch, tag or HEAD holds"
    if _shares_notes_history(ask, ids):
        return "a commit that shares history with the notes"
    return None


def _repo_path(root: Path, top: Path, path: str) -> str:
    """*path* (from the workspace *root*, or absolute) as a path from the
    top. A pattern stays text."""
    try:
        if Path(path).is_absolute():
            return Path(path).resolve().relative_to(top.resolve()).as_posix()
        prefix = root.resolve().relative_to(top.resolve()).as_posix()
    except (OSError, ValueError):
        prefix = "."
    text = path.replace("\\", "/")
    return text if prefix in ("", ".") else f"{prefix}/{text}"


def _kazmas(tops: Sequence[Path], paths: Sequence[str]) -> set[str]:
    """Which of *paths* (from the top) are Kazma's at any work tree: kept out
    by the exclusions, or one of its files by the predicate."""
    from kazma_core.store_registry import kazma_excluded_among, kazma_paths_among

    named = {p for p in paths if p}
    found: set[str] = set()
    for top in tops:
        found |= kazma_excluded_among(top, named - found)
    for top in tops:
        found.update(kazma_paths_among(top, named - found))
    return found


def _blame_plan(root: Path, tops: tuple[Path, ...], form: ReadForm) -> tuple[_Plan | None, str]:
    from kazma_core.store_registry import is_kazma_store

    rel = [*form.before, *(form.after or [])][0]
    target = root / rel
    if not target.is_file():
        return None, "blame reads a work-tree file"
    repo = _repo_path(root, tops[0], str(target.resolve()))
    if is_kazma_store(target, write=False) or _kazmas(tops, [repo]):
        return None, "the file is one of Kazma's"
    return _Plan(["blame", "--no-textconv", *form.options, "--", rel], tops), ""


def _plan(root: Path, ask: Ask, form: ReadForm) -> tuple[_Plan | None, str]:
    """The read to run, or None and why it goes to the card."""
    verb = form.head[0]
    tail = ["--", *form.after] if form.after is not None else []
    patches = prints_patches(verb, form.options)
    if verb not in ("diff", "show", "log", "blame") or (verb == "log" and not patches):
        shape = [*SECTION_SHAPE, *NO_NOTES] if verb == "log" else []
        return _Plan([*form.head, *shape, *form.options, *form.before, *tail]), ""
    top = _work_tree_top(ask)
    if top is None:
        return None, "not a work tree git accepts"
    tops = _work_trees(ask, top)
    if verb == "blame":
        return _blame_plan(root, tops, form)
    revisions = [op for op in form.before if _is_revision(ask, op)]
    given = [*(op for op in form.before if op not in revisions), *(form.after or [])]
    why = _not_history(ask, revisions)
    if why:
        return None, why
    if _kazmas(tops, [_repo_path(root, top, p) for p in given]):
        return None, "the read names one of Kazma's files"
    shape = [*SECTION_SHAPE, *NO_NOTES] if verb in ("log", "show") else list(SECTION_SHAPE)
    if not patches:
        return _Plan([*form.head, *shape, *form.options, *revisions, *(["--", *given] if given else [])]), ""
    excluded = list(dict.fromkeys(spec for place in tops for spec in _exclusions(place)))
    # With no path of its own, a log or show keeps every commit it walks,
    # those that touched only Kazma's files among them (shown empty).
    sparse = ["--full-history", "--sparse"] if verb in ("log", "show") and not given else []
    pathspec = given or [":(top)"]
    args = [*form.head, *shape, *form.options, *sparse, *revisions, "--", *pathspec, *excluded]
    walk = [o for o in form.options if o.split("=", 1)[0] not in _OUTPUT_OPTIONS
            and not re.fullmatch(r"-U\d+", o)]
    twin = None if "--quiet" in form.options else [
        *form.head, *shape, *walk, *sparse, *(["--format="] if verb in ("log", "show") else []),
        "--name-only", *revisions, "--", *pathspec]
    return _Plan(args, tops, twin), ""


def _exclusions(top: Path) -> list[str]:
    from kazma_core.store_registry import kazma_exclude_pathspecs

    return kazma_exclude_pathspecs(top)


def _questions(form: ReadForm) -> list[list[str]]:
    """The questions :func:`_plan` asks of a contents read, none depending on
    another's answer: asked at once (each git process costs tens of
    milliseconds, more on Windows)."""
    verb = form.head[0]
    if verb not in ("diff", "show", "log", "blame") or (verb == "log" and not prints_patches(verb, form.options)):
        return []
    asked = [["rev-parse", "--is-inside-work-tree", "--show-toplevel"], ["worktree", "list", "--porcelain"]]
    if verb != "blame":
        asked += [["rev-parse", "--verify", "--quiet", "HEAD^{commit}"],
                  ["for-each-ref", "--format=%(objectname)", "refs/notes/"]]
        asked += [["rev-parse", "--verify", "--quiet", f"{end}^{{commit}}"]
                  for operand in form.before for end in _ends(operand)]
    return asked


def _at_once(ask: Ask, questions: Sequence[Sequence[str]]) -> list[tuple[int, str, str]]:
    if len(questions) < 2:
        return [ask(q) for q in questions]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(8, len(questions)), thread_name_prefix="kazma-git-read") as pool:
        return list(pool.map(ask, questions))


def run_card_less(root: Path, ask: Ask, form: ReadForm) -> CardLessRead:
    """Judge and run the card-less read *form* in the workspace *root*, every
    question and the read itself through *ask*. Blocking (a door awaits it in
    a thread). A read that prints a file's contents has the sections of
    Kazma's files left out and a line saying how many; a read this does not
    judge card-less is not run (``card`` says why)."""
    remembered = _remembered(ask)
    _at_once(remembered, _questions(form))
    plan, why = _plan(root, remembered, form)
    if plan is None:
        logger.info("[git_read] %s goes to the approval card: %s", form.head[0], why)
        return CardLessRead(0, "", "", why or "outside the card-less forms")
    if not plan.tops or form.head[0] == "blame":
        return CardLessRead(*ask(plan.args))
    (code, out, err), *twin = _at_once(ask, [plan.args, *([plan.twin] if plan.twin is not None else [])])
    out, dropped = _drop_kazma_sections(plan.tops, out)
    left_out = dropped
    if twin and code in (0, 1):
        _tcode, names, _terr = twin[0]
        listed = [_unquoted(line) for line in names.splitlines() if line.strip()]
        kazmas = _kazmas(plan.tops, listed)
        left_out = max(dropped, sum(1 for name in listed if name in kazmas))
    if left_out:
        logger.info("[git_read] %d item(s) of Kazma's own files left out of a read", left_out)
        ending = "" if not out or out.endswith("\n") else "\n"
        out = f"{out}{ending}{_said_count(left_out)}\n"
    return CardLessRead(code, out, err)


def _unquoted(text: str) -> str:
    """A path as git prints it: C-quoted when it holds a special character
    (``"a/x\\ty"``), else as is."""
    if not (len(text) >= 2 and text[0] == text[-1] == '"'):
        return text
    body, out, i = text[1:-1], bytearray(), 0
    simple = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if re.fullmatch(r"[0-7]{3}", body[i + 1:i + 4]):
                out.append(int(body[i + 1:i + 4], 8))
                i += 4
                continue
        out += ch.encode("utf-8")
        i += 1
    return out.decode("utf-8", "replace")


def _prefixed(text: str) -> str | None:
    """``a/x`` or ``b/x`` (quoted or not) as ``x``; None for ``/dev/null``."""
    path = _unquoted(text.strip())
    if path == "/dev/null":
        return None
    return path[2:] if path[:2] in ("a/", "b/") else path


def _section_paths(lines: Sequence[str]) -> set[str]:
    """Every path a patch section's header lines name."""
    paths: set[str] = set()
    first = lines[0].rstrip("\r\n")
    if first.startswith("diff --git "):
        rest = first[len("diff --git "):]
        if rest.startswith('"'):
            for token in re.findall(r'"(?:[^"\\]|\\.)*"|\S+', rest):
                found = _prefixed(token)
                if found:
                    paths.add(found)
        else:
            for at in [m.start() for m in re.finditer(" b/", rest)]:
                left, right = rest[:at], rest[at + 1:]
                if left[2:] == right[2:]:
                    paths.add(left[2:])
            if not paths and rest[2:]:
                paths.add(rest[2:])
    else:
        for header in _HEADERS[1:]:
            if first.startswith(header):
                paths.add(_unquoted(first[len(header):].strip()))
    for line in lines[1:12]:
        text = line.rstrip("\r\n")
        for mark in ("--- ", "+++ "):
            if text.startswith(mark):
                found = _prefixed(text[4:])
                if found:
                    paths.add(found)
        for mark in ("rename from ", "rename to ", "copy from ", "copy to "):
            if text.startswith(mark):
                paths.add(_unquoted(text[len(mark):]))
    return paths


def _drop_kazma_sections(tops: Sequence[Path], text: str) -> tuple[str, int]:
    """*text* (what a patch read printed) without each section whose path is
    one of Kazma's own files at any of the repository's work trees *tops*,
    and how many were dropped.

    A section starts at a ``diff --git``, ``diff --cc`` or ``diff --combined``
    header and ends at the next one or at a ``commit`` line: a line of a
    file's contents is always marked (``+``, ``-``, space, or base85 with no
    space) and never starts either."""
    from kazma_core.store_registry import kazma_paths_among

    chunks: list[tuple[bool, list[str]]] = []
    for line in text.splitlines(keepends=True):
        if line.startswith(_HEADERS):
            chunks.append((True, [line]))
        elif line.startswith("commit ") or not chunks:
            chunks.append((False, [line]))
        else:
            chunks[-1][1].append(line)
    sections = [lines for is_section, lines in chunks if is_section]
    if not sections:
        return text, 0
    named = {path for lines in sections for path in _section_paths(lines)}
    kazmas = {path for top in tops for path in kazma_paths_among(top, named)}
    kept: list[str] = []
    dropped = 0
    for is_section, lines in chunks:
        if is_section and _section_paths(lines) & kazmas:
            dropped += 1
            continue
        kept += lines
    return ("".join(kept) if dropped else text), dropped
