"""AGENTS.md is a short core and one file per section, and nothing is lost.

It was one file of 1.2 MB (about 384,000 tokens) that every session and
subagent re-read on every call. These hold the split of 2026-11-24: the core
under its cap, every piece in the index once, the index's paths real, the
join byte for byte, and ``merge`` -- a branch that still edits the single
file gets its edits into the right pieces, or a named conflict and no write.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess

import pytest

from tests._agents_md import SCRIPT, agents_md

ROOT = SCRIPT.parents[1]
SHORT = {
    "core/": "kazma-core/kazma_core/",
    "gw/": "kazma-gateway/kazma_gateway/",
    "ui/": "kazma-ui/kazma_ui/",
    "cli/": "kazma-cli/kazma_cli/",
    "tui/": "kazma-tui/kazma_tui/",
    "skills/": "kazma-skills/kazma_skills/",
}


# -- the real file -------------------------------------------------------------


def test_the_core_the_index_and_the_pieces_agree():
    """Run ``python scripts/agents_md.py check`` for the same list."""
    assert agents_md.check() == []


def test_the_core_stays_under_its_cap():
    size = len((ROOT / "AGENTS.md").read_bytes().replace(b"\r\n", b"\n"))
    assert size <= agents_md.CORE_CAP <= 40_000, (
        f"AGENTS.md is {size:,} bytes. Every session and subagent loads it on every call: put the text in "
        "the section file under docs/agents/ that governs the code, and at most two lines in the index."
    )


def test_every_piece_is_read_whole():
    """A piece is read whole before a change: one far past the others is split by its parts."""
    big = {p.name: len(p.text.encode()) for p in agents_md.load().pieces if len(p.text.encode()) > 60_000}
    assert not big, f"split these by their own lettered parts, each with its index line: {big}"


def test_splitting_the_join_gives_the_same_pieces():
    tree = agents_md.load()
    again, notes = agents_md.split_text(tree, tree.join())
    assert notes == []
    assert [(p.name, p.text) for p in again.pieces] == [(p.name, p.text) for p in tree.pieces]
    assert agents_md.render_core(again) == tree.core


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, check=True).stdout
    return out.decode("utf-8").split("\n")


def _long(short: str) -> str:
    for prefix, full in SHORT.items():
        if short.startswith(prefix):
            return full + short[len(prefix):]
    return short


def _index_paths(core: str) -> set[str]:
    """Every path the folder table and the index name, written out."""
    part = core[core.index("## Index") :]
    spans = re.findall(r"`([^`\s]+)`", part[part.index("| Folder |") :])
    return {_long(s) for s in spans if "/" in s or s.endswith((".toml", ".lock", ".ps1", ".sh"))}


def _matches(path: str, files: list[str]) -> bool:
    if path.endswith("/"):
        return any(f.startswith(path) for f in files)
    return any(fnmatch.fnmatch(f, path) for f in files)


def test_every_path_the_index_names_exists():
    files = _tracked()
    paths = _index_paths(agents_md.load().core)
    assert len(paths) > 200
    assert [p for p in sorted(paths) if not _matches(p, files)] == []
    assert not _matches("kazma-core/kazma_core/no_such_*.py", files)  # the control


def _uncovered(core: str, files: list[str]) -> list[str]:
    paths = _index_paths(core)
    folders = set()
    for f in files:
        package = next((full for full in SHORT.values() if f.startswith(full)), None)
        if package and f.endswith(".py") and "/" in f[len(package):]:
            folders.add(package + f[len(package):].split("/")[0] + "/")
    return [
        folder for folder in sorted(folders)
        if not any(folder.startswith(p) if p.endswith("/") else p.startswith(folder) for p in paths)
    ]


def test_every_product_folder_is_in_the_index():
    """An agent about to edit a file in any package folder finds a line for it."""
    core = agents_md.load().core
    assert _uncovered(core, _tracked()) == []
    assert _uncovered(core, ["kazma-core/kazma_core/brand_new/x.py"]) == ["kazma-core/kazma_core/brand_new/"]


def test_references_by_number_from_the_rest_of_the_repository_resolve():
    """"AGENTS.md section 50" in code, a test, a workflow or a doc names a section."""
    numbers = set(agents_md.section_numbers(agents_md.load()))
    pattern = re.compile(r"AGENTS(?:\.md)?,? *(?:§|[Ss]ections? )(\d+[b-e]?)")
    out = subprocess.run(
        ["git", "grep", "-hI", "AGENTS", "--", ":!AGENTS.md", ":!docs/agents"],
        cwd=ROOT, capture_output=True, check=True,
    ).stdout.decode("utf-8", "replace")
    found = set(pattern.findall(out))
    assert len(found) > 20
    assert found - numbers == set()


def test_a_reference_that_names_nothing_is_found():
    tree = agents_md.load()
    tree.core += "see §999, §15 Z and §7 A\n"
    assert agents_md.unresolved_references(tree) == [
        "AGENTS.md: §15 Z: section 15 has no part Z",
        "AGENTS.md: §999 names no section",
    ]


def test_a_path_finds_the_pieces_that_govern_it():
    tree = agents_md.load()
    for path, piece in {
        "kazma-core/kazma_core/cron/store.py": "16-scheduled",
        "kazma-core/kazma_core/memory/changes.py": "15h-facts",
        "kazma-gateway/kazma_gateway/adapters/whatsapp.py": "64-whatsapp",
        "scripts/service/kazma_guard.py": "39-guard",
        "kazma-core/kazma_core/spaces/fairness.py": "70h-spaces-fairness-people-keys",
        "kazma-skills/kazma_skills/native/git_github_manager/tools.py": "10-ide-and-workspace",
    }.items():
        assert piece in [p.name for _, p in agents_md.governing(tree, path)[:3]], path


# -- a small repository --------------------------------------------------------

#: A single file with what the real one has: irregular blank lines, a section
#: in lettered parts, sections out of order, a heading with nothing above it,
#: a fenced "### 9." that is no heading, words in Arabic.
OLD = """# Guide

## Scope
all of it

### 1. First (`a.py`)

- rule one
- rule two

- style one
- style two


### 3. Third
**A. Part a.** words
- more

**B. Part b.** نص عربي
```text
### 9. not a heading
```
### 2. Second

last words

## Server

> **RULE.** never

details
"""
PIECES = [
    ("overview", True, "# Guide"),
    ("01-first", False, "### 1. First (`a.py`)"),
    ("style", True, "- style one"),
    ("03a-third", False, "### 3. Third"),
    ("03b-third", False, "**B. Part b.** نص عربي"),
    ("02-second", False, "### 2. Second"),
    ("server", False, "## Server"),
    ("rule", True, "> **RULE.** never"),
    ("details", False, "details"),
]


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.autocrlf=false", *args],
        cwd=repo, capture_output=True, check=True,
    ).stdout.decode()


def _split_tree():
    lines = OLD.split("\n")
    starts = [lines.index(first) for _, _, first in PIECES] + [len(lines) - 1]
    pieces, core = [], "words of the core\n\n"
    for number, (name, inline, _) in enumerate(PIECES):
        text = "\n".join(lines[starts[number] : starts[number + 1]]) + "\n"
        pieces.append(agents_md.Piece(name, inline, text, "what this piece governs"))
        if inline:
            core += f"<!-- piece: {name} -->\n{text}<!-- /piece -->\n"
    core += "## Index\n<!-- index -->\n"
    for name, inline, _ in PIECES:
        core += f"- `{'#' if inline else ''}{name}{'' if inline else '.md'}` what this piece governs\n"
    return agents_md.Tree(core + "<!-- /index -->\n", pieces)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repository whose ``theirs`` branch still edits the single file and
    whose ``main`` is split; the script works on it."""
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "AGENTS.md").write_bytes(OLD.encode())
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "single")
    _git(tmp_path, "branch", "theirs")
    monkeypatch.setattr(agents_md, "ROOT", tmp_path)
    agents_md.write_tree(_split_tree())
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "split")
    return tmp_path


def _theirs(repo, *changes: tuple[str, str], branch: str = "theirs") -> str:
    text = OLD
    for before, after in changes:
        assert text.count(before) == 1
        text = text.replace(before, after)
    _git(repo, "checkout", "-q", branch)
    (repo / "AGENTS.md").write_bytes(text.encode())
    _git(repo, "commit", "-qam", "edit")
    _git(repo, "checkout", "-q", "main")
    return text


def _piece(repo, name: str) -> str:
    return (repo / "docs" / "agents" / f"{name}.md").read_bytes().decode()


def _cli(*args: str) -> int:
    return agents_md.main(list(args))


def test_the_join_is_the_single_file_byte_for_byte(repo):
    assert agents_md.joined() == OLD
    assert agents_md.single_file("theirs") == OLD
    assert agents_md.single_file("HEAD") == OLD
    assert agents_md.check() == []


def test_crlf_in_the_worktree_changes_nothing(repo):
    for path in [repo / "AGENTS.md", *(repo / "docs" / "agents").iterdir()]:
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert agents_md.joined() == OLD
    assert agents_md.check() == []
    assert agents_md.write_tree(agents_md.load()) == []


def test_merge_puts_each_edit_in_its_piece(repo, capsys):
    text = _theirs(
        repo,
        ("- rule two\n", "- rule two\n- rule three\n"),
        ("- style two\n", "- style two, changed\n"),
        ("last words\n", "last words\n\nand more\n"),
        ("**B. Part b.** نص عربي", "**B. Part b, renamed.** نص عربي جديد"),
        ("### 3. Third\n", "### 3. Third, renamed\n"),
    )
    assert _cli("merge", "--theirs", "theirs") == 0
    assert agents_md.joined() == text
    assert "- rule three" in _piece(repo, "01-first")
    assert "changed" in (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert "and more" in _piece(repo, "02-second")
    assert _piece(repo, "03b-third").split("\n")[1].startswith("**B. Part b, renamed.**")
    assert _piece(repo, "03a-third").split("\n")[1] == "### 3. Third, renamed"
    assert "found by its number" in capsys.readouterr().out
    assert agents_md.check() == []


def test_merge_keeps_ours_too(repo):
    path = repo / "docs" / "agents" / "02-second.md"
    path.write_bytes(path.read_bytes().replace(b"last words", b"our last words"))
    _git(repo, "commit", "-qam", "ours")
    text = _theirs(repo, ("- rule one\n", "- rule one, theirs\n"))
    assert _cli("merge", "--theirs", "theirs") == 0
    assert agents_md.joined() == text.replace("last words", "our last words")


def test_an_unnumbered_first_line_that_changed_is_found_by_the_text_around_it(repo):
    text = _theirs(repo, ("- style one\n", "- style number one\n"), ("> **RULE.** never", "> **RULE.** never ever"))
    assert _cli("merge", "--theirs", "theirs") == 0
    assert agents_md.joined() == text
    core = (repo / "AGENTS.md").read_bytes().decode()
    assert "<!-- piece: style -->\n- style number one\n" in core
    assert "<!-- piece: rule -->\n> **RULE.** never ever\n" in core


def test_a_new_section_becomes_a_piece_and_waits_for_its_index_line(repo, capsys):
    text = _theirs(repo, ("last words\n\n", "last words\n\n### 4. A new thing (`x.py`)\n\nits rule\n\n"))
    assert _cli("merge", "--theirs", "theirs") == 2
    assert agents_md.joined() == text
    assert _piece(repo, "04-a-new-thing").endswith("### 4. A new thing (`x.py`)\n\nits rule\n\n")
    assert [p.name for p in agents_md.load().pieces][5:7] == ["02-second", "04-a-new-thing"]
    assert agents_md.check() == ["04-a-new-thing: its index line does not say what it governs"]


def test_a_conflict_is_named_and_nothing_is_written(repo, capsys):
    path = repo / "docs" / "agents" / "02-second.md"
    path.write_bytes(path.read_bytes().replace(b"last words", b"our words"))
    _git(repo, "commit", "-qam", "ours")
    _theirs(repo, ("last words", "their words"), ("- rule one\n", "- rule one, theirs\n"))
    before = {p.name: p.read_bytes() for p in (repo / "docs" / "agents").iterdir()}
    assert _cli("merge", "--theirs", "theirs") == 1
    said = capsys.readouterr().out
    assert "1 conflict" in said and "02-second (line 3 of its text" in said
    assert {p.name: p.read_bytes() for p in (repo / "docs" / "agents").iterdir()} == before
    out = repo / ".git" / "agents_md_merged.md"
    marked = out.read_bytes().decode()
    assert _cli("split", "--from", str(out)) == 1  # the markers are still there
    resolved = re.sub(r"<{7} ours\n.*?\n>{7} theirs\n", "both words\n", marked, flags=re.S)
    out.write_bytes(resolved.encode())
    assert _cli("split", "--from", str(out)) == 0
    assert "both words" in _piece(repo, "02-second")
    assert "rule one, theirs" in _piece(repo, "01-first")


def test_the_whole_procedure_for_two_branches(repo):
    """git merge conflicts on AGENTS.md; the script settles it; the next branch merges after it."""
    _git(repo, "branch", "second", "theirs")
    _theirs(repo, ("- rule two\n", "- rule two, theirs\n"))
    _theirs(repo, ("last words", "words of the second"), branch="second")
    for branch in ("theirs", "second"):
        done = subprocess.run(["git", "merge", "--no-commit", branch], cwd=repo, capture_output=True)
        assert done.returncode != 0  # a conflict: ours is the core
        assert _cli("merge", "--theirs", "MERGE_HEAD") == 0
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", f"merge {branch}")
    assert agents_md.joined() == OLD.replace("rule two", "rule two, theirs").replace("last words", "words of the second")
    assert agents_md.check() == []


def test_the_split_merged_into_a_branch_that_still_has_the_single_file(repo):
    text = _theirs(repo, ("last words", "their words"), ("- style two", "- style two, theirs"))
    _git(repo, "checkout", "-q", "theirs")
    done = subprocess.run(["git", "merge", "--no-commit", "main"], cwd=repo, capture_output=True)
    assert done.returncode != 0
    assert _cli("merge", "--ours", "MERGE_HEAD", "--theirs", "HEAD") == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "split here too")
    assert agents_md.single_file("HEAD") == agents_md.joined() == text
    assert agents_md.check() == []


def test_a_conflict_settled_during_a_git_merge(repo):
    path = repo / "docs" / "agents" / "02-second.md"
    path.write_bytes(path.read_bytes().replace(b"last words", b"our words"))
    _git(repo, "commit", "-qam", "ours")
    _theirs(repo, ("last words", "their words"))
    subprocess.run(["git", "merge", "--no-commit", "theirs"], cwd=repo, capture_output=True)
    assert _cli("merge", "--theirs", "MERGE_HEAD") == 1
    out = repo / ".git" / "agents_md_merged.md"
    out.write_bytes(re.sub(r"<{7} ours\n.*?\n>{7} theirs\n", "both\n", out.read_bytes().decode(), flags=re.S).encode())
    assert _cli("split", "--from", str(out)) == 0
    assert agents_md.check() == []
    assert "both" in _piece(repo, "02-second")


def test_merge_refuses_what_it_would_lose(repo, capsys):
    _theirs(repo, ("last words", "their words"))
    (repo / "docs" / "agents" / "01-first.md").write_text("edited", encoding="utf-8")
    assert _cli("merge", "--theirs", "theirs") == 1
    assert "commit or undo these piece edits first" in capsys.readouterr().err
    _git(repo, "checkout", "--", "docs/agents")
    assert _cli("merge", "--theirs", "HEAD") == 1
    assert "already split" in capsys.readouterr().err


def test_moved_or_removed_sections_stop_the_merge(repo, capsys):
    first = OLD[OLD.index("### 1. First") : OLD.index("- style one")]
    _git(repo, "checkout", "-q", "theirs")
    (repo / "AGENTS.md").write_bytes((OLD.replace(first, "") + first).encode())
    _git(repo, "commit", "-qam", "moved")
    _git(repo, "checkout", "-q", "main")
    assert _cli("merge", "--theirs", "theirs") == 1
    assert "reordered" in capsys.readouterr().err
    assert agents_md.joined() == OLD


@pytest.mark.parametrize(
    ("break_it", "said"),
    [
        (lambda r: (r / "docs/agents/99-stray.md").write_text("x", encoding="utf-8"), "99-stray.md: not in the index"),
        (lambda r: (r / "docs/agents/02-second.md").unlink(), "02-second.md, which does not exist"),
        (lambda r: _edit(r, "AGENTS.md", "- `02-second.md` what this piece governs\n", ""), "02-second.md: not in the index"),
        (lambda r: _edit(r, "AGENTS.md", "<!-- /index -->", "- `02-second.md` what this piece governs\n<!-- /index -->"),
         "02-second: listed more than once"),
        (lambda r: _edit(r, "AGENTS.md", "- `details.md` what this piece governs", "- `details.md` x"),
         "details: its index line does not say"),
        (lambda r: _edit(r, "docs/agents/details.md", "details\n", "### 2. Again\n"), "section 2 is there 2 times"),
        (lambda r: _edit(r, "docs/agents/02-second.md", "last words", "see §77"), "02-second: §77 names no section"),
        (lambda r: _edit(r, "docs/agents/details.md", "<!-- AGENTS.md, piece details.", "<!-- AGENTS.md, piece other."),
         "details: its first line must be"),
        (lambda r: _edit(r, "AGENTS.md", "- `#rule` what this piece governs\n", ""), "the block rule is not in the index"),
        (lambda r: _edit(r, "AGENTS.md", "words of the core", "x" * agents_md.CORE_CAP), "put the text in a section file"),
    ],
)
def test_negative_control_check_finds_each_fault(repo, break_it, said):
    break_it(repo)
    assert any(said in problem for problem in agents_md.check()), agents_md.check()


def _edit(repo, rel: str, before: str, after: str) -> None:
    path = repo / rel
    text = path.read_bytes().decode()
    assert before in text
    path.write_bytes(text.replace(before, after, 1).encode())
