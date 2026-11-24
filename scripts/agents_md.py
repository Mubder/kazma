#!/usr/bin/env python3
"""AGENTS.md as a short core and one file per section.

``AGENTS.md`` was one file of 1.2 MB that every agent loaded whole. It is now
a core, always loaded, whose index names the piece to read before changing
what the piece governs. The pieces are in ``docs/agents/``.

What a piece is
    The pieces, in the order the index lists them, tile the old single file:
    every byte of it is in exactly one piece, unchanged. A piece is a file
    (``docs/agents/<name>.md``) or, for the few rules every task needs, a
    block inside the core between ``<!-- piece: name -->`` and
    ``<!-- /piece -->``. A piece file starts with one comment line naming it
    (``HEADER``), which is not part of the text.

What ``join`` gives back
    The single file, byte for byte (LF): the pieces' texts one after the
    other, without the header lines. The core's own text (how to use it, the
    summary of the rules, the index) is in no piece and not in the join.

    python scripts/agents_md.py for kazma-core/kazma_core/cron/store.py
    python scripts/agents_md.py check
    python scripts/agents_md.py join [--rev COMMIT] [--out FILE]
    python scripts/agents_md.py split --from FILE
    python scripts/agents_md.py merge --theirs COMMIT [--base COMMIT] [--ours COMMIT]

Merging a branch that still edits the single file::

    git merge <branch>                   # AGENTS.md conflicts
    python scripts/agents_md.py merge --theirs MERGE_HEAD
    git add AGENTS.md docs/agents

Merging the split itself into a branch that still has the single file::

    git merge <the split branch>         # AGENTS.md conflicts
    python scripts/agents_md.py merge --ours MERGE_HEAD --theirs HEAD
    git add AGENTS.md docs/agents

``merge`` joins ours (``HEAD`` unless ``--ours``), takes the single file at
the merge base and the branch's, runs ``git merge-file`` on the three, and
writes the pieces (and the core's own blocks) back. On a conflict it writes
no piece: it names each conflict and the piece it is in, and leaves the whole
merged file with its markers where it says; after editing it,
``split --from`` that file writes the pieces.

Standard library only.
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_NAME = "AGENTS.md"
FOLDER = "docs/agents"

#: Nothing in the core may push it past this: it is loaded by every session
#: and every subagent, on every call.
CORE_CAP = 40_000

HEADER = "<!-- AGENTS.md, piece {name}. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->\n"
_HEADER_RE = re.compile(r"\A<!-- AGENTS\.md, piece [^\n]*-->\n")
_BLOCK_RE = re.compile(r"<!-- piece: ([\w-]+) -->\n(.*?)<!-- /piece -->\n", re.S)
_INDEX_RE = re.compile(r"<!-- index -->\n(.*?)<!-- /index -->\n", re.S)
_ENTRY_RE = re.compile(r"^- `(#?)([\w-]+)(\.md)?`(.*)$")
_SECTION_RE = re.compile(r"^### (\d+[a-z]?)\. (.*)$")
_LETTER_RE = re.compile(r"^\*\*([A-Z]\d?[a-z]?)\. ")
#: The words an entry made by ``merge`` carries until someone writes it.
NEW_ENTRY = "NEW: say what it governs"


class AgentsMdError(Exception):
    """The files do not say what this script needs; the message names it."""


@dataclass
class Piece:
    name: str
    inline: bool
    text: str
    entry: str = ""


@dataclass
class Tree:
    """The core, and the pieces in index order."""

    core: str
    pieces: list[Piece] = field(default_factory=list)

    def join(self) -> str:
        return "".join(piece.text for piece in self.pieces)


# -- reading -----------------------------------------------------------------


def _lf(data: bytes) -> str:
    return data.decode("utf-8").replace("\r\n", "\n")


def _git(*args: str, check: bool = True) -> bytes:
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if check and done.returncode:
        raise AgentsMdError(f"git {' '.join(args)}: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def is_split(core: str) -> bool:
    return bool(_INDEX_RE.search(core))


def index_entries(core: str) -> list[tuple[str, bool, str]]:
    """(name, inline, the entry's whole text) in order."""
    found = _INDEX_RE.search(core)
    if not found:
        raise AgentsMdError(f"{CORE_NAME} has no index (<!-- index --> ... <!-- /index -->)")
    entries: list[tuple[str, bool, str]] = []
    for line in found.group(1).split("\n"):
        match = _ENTRY_RE.match(line)
        if match:
            entries.append((match.group(2), bool(match.group(1)), match.group(4).strip()))
        elif line.startswith("- "):
            raise AgentsMdError(f"an index line names no piece: {line[:80]}")
        elif line.startswith("  ") and entries:
            name, inline, text = entries[-1]
            entries[-1] = (name, inline, f"{text} {line.strip()}")
    return entries


def _tree(core: str, read) -> Tree:
    blocks = dict(_BLOCK_RE.findall(core))
    tree = Tree(core)
    for name, inline, entry in index_entries(core):
        if inline:
            if name not in blocks:
                raise AgentsMdError(f"the index names #{name}, and {CORE_NAME} has no such block")
            text = blocks[name]
        else:
            text = _HEADER_RE.sub("", read(f"{FOLDER}/{name}.md"), count=1)
        tree.pieces.append(Piece(name, inline, text, entry))
    return tree


def load(root: Path | None = None) -> Tree:
    """The working tree's core and pieces."""
    root = root or ROOT

    def read(rel: str) -> str:
        path = root / rel
        if not path.is_file():
            raise AgentsMdError(f"the index names {rel}, which does not exist")
        return _lf(path.read_bytes())

    return _tree(read(CORE_NAME), read)


def load_commit(commit: str) -> Tree:
    def read(rel: str) -> str:
        return _lf(_git("show", f"{commit}:{rel}"))

    return _tree(read(CORE_NAME), read)


def single_file(commit: str) -> str:
    """The single file at *commit*, whether it was still one or is split."""
    text = _lf(_git("show", f"{commit}:{CORE_NAME}"))
    return load_commit(commit).join() if is_split(text) else text


def joined(root: Path | None = None) -> str:
    """The single file rebuilt from the working tree."""
    return load(root).join()


# -- splitting ---------------------------------------------------------------


def _offsets(pieces: list[Piece]) -> list[int]:
    """The line each piece starts on (0-based) in the join."""
    starts, line = [], 0
    for piece in pieces:
        starts.append(line)
        line += piece.text.count("\n")
    return starts


def _unique_line(lines: list[str]) -> dict[str, int]:
    seen: dict[str, int] = {}
    for number, line in enumerate(lines):
        seen[line] = -1 if line in seen else number
    return seen


def _opener_key(line: str) -> re.Pattern[str] | None:
    section = _SECTION_RE.match(line)
    if section:
        return re.compile(rf"^### {re.escape(section.group(1))}\. ")
    letter = _LETTER_RE.match(line)
    if letter:
        return re.compile(rf"^\*\*{re.escape(letter.group(1))}\. ")
    return None


def find_starts(old: list[Piece], text: str) -> tuple[list[int], list[str]]:
    """Where each piece of *old* starts in *text* (line numbers), and notes.

    A piece is found by its own first line. One whose first line was edited is
    found between its neighbours: by its section number or letter, else by
    lining the stretch up with the old one.
    """
    lines = text.split("\n")
    once = _unique_line(lines)
    starts: list[int | None] = []
    for number, piece in enumerate(old):
        if number == 0:
            starts.append(0)
            continue
        at = once.get(piece.text.split("\n", 1)[0], -1)
        starts.append(at if at > 0 else None)
    # A first line that moved out of order is not a boundary.
    last = -1
    for number, at in enumerate(starts):
        if at is not None:
            if at <= last:
                raise AgentsMdError(
                    f"piece {old[number].name} starts before the piece listed ahead of it: "
                    "sections were reordered, which merge does not do"
                )
            last = at
    notes: list[str] = []
    for number, at in enumerate(starts):
        if at is not None:
            continue
        piece = old[number]
        low = starts[number - 1]
        assert low is not None
        after = next((n for n in range(number + 1, len(old)) if starts[n] is not None), None)
        high = starts[after] if after is not None else len(lines)
        assert high is not None
        key = _opener_key(piece.text.split("\n", 1)[0])
        hits = [n for n in range(low + 1, high) if key and key.match(lines[n])]
        if len(hits) == 1:
            starts[number] = hits[0]
            notes.append(f"{piece.name}: its first line changed; found by its number")
            continue
        stretch = [line for p in old[number - 1 : after] for line in p.text.split("\n")[:-1]]
        offset = old[number - 1].text.count("\n")
        found = None
        matcher = difflib.SequenceMatcher(None, stretch, lines[low:high], autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if not i1 <= offset < i2:
                continue
            if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
                found = low + j1 + (offset - i1)
            elif tag == "replace" and offset == i1:
                found = low + j1
            break
        if found is None or not low < found < high:
            raise AgentsMdError(
                f"cannot find where piece {piece.name} starts: its first line was "
                f"{piece.text.split(chr(10), 1)[0][:80]!r}"
            )
        starts[number] = found
        notes.append(f"{piece.name}: its first line changed; found by the text around it")
    return [at for at in starts if at is not None], notes


def _slug(number: str, title: str) -> str:
    words = re.sub(r"`[^`]*`|\([^)]*\)", " ", title).lower()
    parts = re.findall(r"[a-z0-9]+", words)[:4]
    return "-".join([number.zfill(2) if number.isdigit() else number, *parts])


def _section_headings(text: str) -> list[tuple[int, str, str]]:
    """(line, number, title) of each numbered section heading outside a code fence."""
    found, fenced = [], False
    for number, line in enumerate(text.split("\n")):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            match = _SECTION_RE.match(line)
            if match:
                found.append((number, match.group(1), match.group(2)))
    return found


def split_text(tree: Tree, text: str) -> tuple[Tree, list[str]]:
    """*text* (a whole single file) cut as *tree* is, and what was noticed.

    A numbered section the tree does not have becomes a piece of its own,
    after the one it stands in.
    """
    if not text.endswith("\n"):
        text += "\n"
    if re.search(r"^(?:<{7} |>{7} )", text, re.M):
        raise AgentsMdError("the file still holds conflict markers")
    starts, notes = find_starts(tree.pieces, text)
    lines = text.split("\n")[:-1]
    known = {number for piece in tree.pieces for _, number, _ in _section_headings(piece.text)}
    taken = {piece.name for piece in tree.pieces}
    out: list[Piece] = []
    for number, piece in enumerate(tree.pieces):
        end = starts[number + 1] if number + 1 < len(starts) else len(lines)
        body = lines[starts[number] : end]
        if not body:
            raise AgentsMdError(f"piece {piece.name} would be empty: remove it and its index line by hand")
        cuts = [
            (line, num, title)
            for line, num, title in _section_headings("\n".join(body) + "\n")
            if num not in known and line > 0
        ]
        first_end = cuts[0][0] if cuts else len(body)
        out.append(Piece(piece.name, piece.inline, "\n".join(body[:first_end]) + "\n", piece.entry))
        for position, (line, num, title) in enumerate(cuts):
            stop = cuts[position + 1][0] if position + 1 < len(cuts) else len(body)
            name = _slug(num, title)
            if name in taken:
                raise AgentsMdError(f"two pieces would be named {name}")
            taken.add(name)
            out.append(
                Piece(name, False, "\n".join(body[line:stop]) + "\n", f"**§{num} {title}** ({NEW_ENTRY})")
            )
            notes.append(f"{name}: a new section; write its index line")
    result = Tree(tree.core, out)
    if result.join() != text:
        raise AgentsMdError("the pieces do not join back to the file that was split")
    return result, notes


def render_core(tree: Tree) -> str:
    """The core with its own blocks' text and any new piece's index line."""
    texts = {piece.name: piece.text for piece in tree.pieces if piece.inline}

    def block(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"<!-- piece: {name} -->\n{texts.get(name, match.group(2))}<!-- /piece -->\n"

    core = _BLOCK_RE.sub(block, tree.core)
    listed = {name for name, _, _ in index_entries(core)}
    previous = None
    for piece in tree.pieces:
        if piece.name not in listed:
            line = f"- `{piece.name}.md` {piece.entry}\n"
            mark = "" if previous is None else f"- `{'#' if previous.inline else ''}{previous.name}{'' if previous.inline else '.md'}`"
            at = core.index("\n" + mark) + 1 if mark else _INDEX_RE.search(core).start(1)
            if mark:
                at = core.index("\n", at) + 1
                while core.startswith("  ", at):
                    at = core.index("\n", at) + 1
            core = core[:at] + line + core[at:]
        previous = piece
    return core


def piece_file_text(piece: Piece) -> str:
    return HEADER.format(name=piece.name) + piece.text


def _write(path: Path, text: str) -> bool:
    """Write *text* with LF unless the file already says it; whether it changed."""
    if path.is_file() and _lf(path.read_bytes()) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return True


def write_tree(tree: Tree, root: Path | None = None) -> list[str]:
    """Write the core and every piece file; the names of those that changed."""
    root = root or ROOT
    changed = []
    for piece in tree.pieces:
        if not piece.inline and _write(root / FOLDER / f"{piece.name}.md", piece_file_text(piece)):
            changed.append(piece.name)
    if _write(root / CORE_NAME, render_core(tree)):
        changed.append(CORE_NAME)
    return changed


# -- checking ----------------------------------------------------------------


def section_numbers(tree: Tree) -> dict[str, list[str]]:
    """Section number -> the pieces whose text holds its heading."""
    found: dict[str, list[str]] = {}
    for piece in tree.pieces:
        for _, number, _ in _section_headings(piece.text):
            found.setdefault(number, []).append(piece.name)
    return found


def section_letters(tree: Tree) -> dict[str, set[str]]:
    """Section number -> its lettered parts ("**B2. ...", "- **E2. ...")."""
    letters: dict[str, set[str]] = {}
    current = None
    for line in tree.join().split("\n"):
        section = _SECTION_RE.match(line)
        if section:
            current = section.group(1)
        elif line.startswith("## "):
            current = None
        part = _PART_RE.match(line)
        if part and current:
            letters.setdefault(current, set()).add(part.group(1))
    return letters


_PART_RE = re.compile(r"^(?:- )?\*\*([A-Z]\d?[a-z]?)\. ")
_REFERENCE_RE = re.compile(r"§(\d+[b-e]?)(?:\s?([A-Z]\d?[a-z]?)(?!\w))?")


def unresolved_references(tree: Tree) -> list[str]:
    """Every "§N" in the core and the pieces that names no section, and every
    "§N B2" whose section has lettered parts and no such one."""
    numbers = set(section_numbers(tree))
    letters = section_letters(tree)
    problems = []
    for name, text in [(CORE_NAME, tree.core), *((p.name, p.text) for p in tree.pieces)]:
        for number, letter in sorted(set(_REFERENCE_RE.findall(text))):
            if number not in numbers:
                problems.append(f"{name}: §{number} names no section")
            elif letter and number in letters and letter not in letters[number]:
                problems.append(f"{name}: §{number} {letter}: section {number} has no part {letter}")
    return problems


def check(root: Path | None = None) -> list[str]:
    """What is wrong with the split; empty when nothing is."""
    root = root or ROOT
    problems: list[str] = []
    core_path = root / CORE_NAME
    size = len(_lf(core_path.read_bytes()).encode("utf-8"))
    if size > CORE_CAP:
        problems.append(
            f"{CORE_NAME} is {size:,} bytes, past its cap of {CORE_CAP:,}. Every session loads it on every "
            f"call: put the text in a section file under {FOLDER}/ and at most two lines in the index"
        )
    try:
        tree = load(root)
    except AgentsMdError as exc:
        return [*problems, str(exc)]
    names = [piece.name for piece in tree.pieces]
    problems += [f"{name}: listed more than once in the index" for name in sorted({n for n in names if names.count(n) > 1})]
    blocks = [name for name, _ in _BLOCK_RE.findall(tree.core)]
    inline = {piece.name for piece in tree.pieces if piece.inline}
    problems += [f"{CORE_NAME}: the block {name} is not in the index" for name in blocks if name not in inline]
    problems += [f"{CORE_NAME}: the block {name} is there twice" for name in sorted({n for n in blocks if blocks.count(n) > 1})]
    if tree.core.count("<!-- piece:") != len(blocks) or tree.core.count("<!-- /piece -->") != len(blocks):
        problems.append(f"{CORE_NAME}: a block is not closed, or is not where the script reads it")
    listed = {piece.name for piece in tree.pieces if not piece.inline}
    problems += [
        f"{FOLDER}/{path.name}: not in the index"
        for path in sorted((root / FOLDER).iterdir())
        if path.stem not in listed or path.suffix != ".md"
    ]
    for piece in tree.pieces:
        if not piece.text.endswith("\n") or not piece.text.strip():
            problems.append(f"{piece.name}: empty, or its last line has no newline")
        if not piece.inline:
            head = _lf((root / FOLDER / f"{piece.name}.md").read_bytes()).split("\n", 1)[0] + "\n"
            if head != HEADER.format(name=piece.name):
                problems.append(f"{piece.name}: its first line must be {HEADER.format(name=piece.name).strip()}")
        if NEW_ENTRY in piece.entry or len(piece.entry) < 20:
            problems.append(f"{piece.name}: its index line does not say what it governs")
    for number, where in sorted(section_numbers(tree).items()):
        if len(where) > 1:
            problems.append(f"section {number} is there {len(where)} times: {', '.join(where)}")
    return problems + unresolved_references(tree)


# -- merging -----------------------------------------------------------------


def merge_base(ours: str, theirs: str) -> str:
    return _git("merge-base", ours, theirs).decode().strip()


def three_way(ours: str, base: str, theirs: str) -> tuple[str, int]:
    """The three texts merged by ``git merge-file``, and how many conflicts."""
    with tempfile.TemporaryDirectory(prefix="agents-md-") as folder:
        paths = []
        for name, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            path = Path(folder) / name
            path.write_bytes(text.encode("utf-8"))
            paths.append(str(path))
        done = subprocess.run(
            ["git", "merge-file", "-p", "--diff3", "-L", "ours", "-L", "base", "-L", "theirs", *paths],
            capture_output=True,
        )
    if done.returncode < 0 or done.returncode > 127:
        raise AgentsMdError(f"git merge-file: {done.stderr.decode('utf-8', 'replace').strip()}")
    return _lf(done.stdout), done.returncode


def name_conflicts(tree: Tree, merged: str) -> list[str]:
    """Each conflict of *merged*: the piece it is in, its line there, its words."""
    starts = _offsets(tree.pieces)
    found: list[str] = []
    ours_line, side = 0, "ours"
    for number, line in enumerate(merged.split("\n"), 1):
        if line.startswith("<<<<<<< "):
            where = max(n for n, start in enumerate(starts) if start <= ours_line)
            found.append(
                f"{tree.pieces[where].name} (line {ours_line - starts[where] + 1} of its text, "
                f"line {number} of the merged file)"
            )
        elif line.startswith("||||||| "):
            side = "base"
        elif line.startswith(">>>>>>> "):
            side = "ours"
        elif side == "ours":
            ours_line += 1
    return found


def merge(theirs: str, base: str | None = None, ours: str = "HEAD") -> tuple[Tree, list[str], str, list[str]]:
    """(the merged pieces, notes, the merged file, the conflicts by name)."""
    ours_tree = load_commit(ours)
    theirs_core = _lf(_git("show", f"{theirs}:{CORE_NAME}"))
    if is_split(theirs_core):
        raise AgentsMdError(f"{CORE_NAME} at {theirs} is already split: git merges its pieces")
    base_commit = base or merge_base(ours, theirs)
    merged, conflicts = three_way(ours_tree.join(), single_file(base_commit), theirs_core)
    if conflicts:
        return ours_tree, [], merged, name_conflicts(ours_tree, merged)
    tree, notes = split_text(ours_tree, merged)
    return tree, notes, merged, []


def _pieces_edited(ours: str) -> list[str]:
    """Piece files the working tree holds otherwise than *ours*: merge would overwrite them."""
    out = _git("status", "--porcelain", "--", FOLDER).decode("utf-8", "replace").split("\n")
    return [line for line in out if line.strip()] if ours == "HEAD" else []


# -- finding -----------------------------------------------------------------


#: File names too common to find a piece by.
_COMMON_STEMS = {
    "__init__", "app", "base", "common", "config", "graph", "index", "registry", "router", "routes",
    "store", "text", "tools", "types",
}


def governing(tree: Tree, path: str) -> list[tuple[int, Piece]]:
    """The pieces that name *path* (a file or folder of the repository), most first."""
    rel = path.replace("\\", "/").removeprefix(ROOT.as_posix()).strip("/")
    inside = re.sub(r"^kazma-\w+/kazma_\w+/", "", rel)
    name, stem = Path(inside).name, Path(inside).stem
    package = re.match(r"^kazma-\w+/(kazma_\w+)/", rel)
    module = re.sub(r"(?:/__init__)?\.py$", "", inside).replace("/", ".")
    patterns = {rel: 8, inside: 6}
    if package and inside.endswith(".py"):
        patterns[f"{package.group(1)}.{module}"] = 6
    if "." in module:
        patterns[f"{module}."] = 5
    if stem in _COMMON_STEMS:
        # ``git_github_manager/tools.py``: its folder is what a piece names.
        patterns[f"{Path(inside).parent.name}/"] = 3
        patterns[Path(inside).parent.name] = 1
    else:
        patterns |= {name: 4, f"`{stem}.": 3, f"`{stem}`": 2}
    patterns = {what: weight for what, weight in patterns.items() if len(what) > 3}
    found = []
    for piece in tree.pieces:
        score = sum(weight * min(3, (piece.text + piece.entry).count(what)) for what, weight in patterns.items())
        if score:
            found.append((score, piece))
    return sorted(found, key=lambda pair: -pair[0])


# -- the command -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("for", help="the pieces that govern a path")
    command.add_argument("paths", nargs="+")
    commands.add_parser("check", help="whether the core, the index and the pieces agree")
    command = commands.add_parser("join", help="rebuild the single file")
    command.add_argument("--rev", help="of this commit, instead of the working tree")
    command.add_argument("--out", type=Path)
    command = commands.add_parser("split", help="write the pieces from a whole single file")
    command.add_argument("--from", dest="source", type=Path, required=True)
    command = commands.add_parser("merge", help="merge a branch that still edits the single file")
    command.add_argument("--theirs", required=True)
    command.add_argument("--base")
    command.add_argument("--ours", default="HEAD")
    command.add_argument("--dry-run", action="store_true", help="say what would change; write nothing")
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except AgentsMdError as exc:
        print(f"agents_md: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    if args.command == "for":
        tree = load()
        for path in args.paths:
            print(path)
            found = governing(tree, path)
            for score, piece in found[:10]:
                where = CORE_NAME if piece.inline else f"{FOLDER}/{piece.name}.md"
                print(f"  {score:3} {where}  {piece.entry[:90]}")
            if not found:
                print("  no piece names it: choose by the index")
        return 0
    if args.command == "check":
        problems = check()
        for problem in problems:
            print(problem)
        if not problems:
            print(f"{CORE_NAME}: {len(_lf((ROOT / CORE_NAME).read_bytes()).encode()):,} bytes of {CORE_CAP:,}; "
                  f"{len(load().pieces)} pieces")
        return 1 if problems else 0
    if args.command == "join":
        text = single_file(args.rev) if args.rev else joined()
        if args.out:
            args.out.write_bytes(text.encode("utf-8"))
        else:
            sys.stdout.buffer.write(text.encode("utf-8"))
        return 0
    if args.command == "split":
        tree, notes = split_text(load_commit("HEAD") if _core_unmerged() else load(), _lf(args.source.read_bytes()))
        return _finish(tree, notes)
    edited = _pieces_edited(args.ours)
    if edited and not args.dry_run:
        raise AgentsMdError("commit or undo these piece edits first; merge writes over them:\n" + "\n".join(edited))
    tree, notes, merged, conflicts = merge(args.theirs, args.base, args.ours)
    if conflicts:
        out = Path(_git("rev-parse", "--absolute-git-dir").decode().strip()) / "agents_md_merged.md"
        out.write_bytes(merged.encode("utf-8"))
        print(f"{len(conflicts)} conflict(s); no piece was written:")
        for conflict in conflicts:
            print(f"  {conflict}")
        print(f"Edit {out}, then: python scripts/agents_md.py split --from \"{out}\"")
        return 1
    if args.dry_run:
        before = {piece.name: piece.text for piece in load_commit(args.ours).pieces}
        changed = [piece.name for piece in tree.pieces if before.get(piece.name) != piece.text]
        print(f"clean; would change: {', '.join(changed) or 'nothing'}")
        for note in notes:
            print(f"  {note}")
        return 0
    return _finish(tree, notes)


def _core_unmerged() -> bool:
    return bool(_git("ls-files", "-u", "--", CORE_NAME).strip())


def _finish(tree: Tree, notes: list[str]) -> int:
    changed = write_tree(tree)
    print(f"written: {', '.join(changed) or 'nothing changed'}")
    for note in notes:
        print(f"  {note}")
    problems = check()
    for problem in problems:
        print(f"check: {problem}")
    return 2 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
