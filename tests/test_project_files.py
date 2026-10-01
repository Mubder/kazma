"""A folder's files as its git repository keeps them (2026-10-02).

The agent's ``file_search`` and the code index walked every folder not on a
skip list. On the live install (the agent's workspace is the install folder)
a 4.7 GB repository cloned into it and ignored by its ``.gitignore`` took a
search 13.9 s and the code index's whole budget. ``git_project_files`` is
the one list of a project's files: the Workspace page, the search and the
index all use it, and walk only where git has nothing to say.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), check=True, capture_output=True)


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    _touch(root / ".gitignore", "vendor/\n*.log\n")
    _touch(root / "src" / "app.py", "TOKEN_NEEDLE = 1\n")
    _touch(root / "vendor" / "huge" / "lib.py", "TOKEN_NEEDLE = 2\n")
    _touch(root / "debug.log", "TOKEN_NEEDLE\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "one")
    _touch(root / "notes" / "draft.py", "TOKEN_NEEDLE = 3\n")  # untracked, not ignored
    return root


def _rel(paths: list[Path] | None, root: Path) -> list[str] | None:
    return None if paths is None else sorted(p.relative_to(root).as_posix() for p in paths)


def test_a_repository_lists_what_it_keeps(repo: Path) -> None:
    from kazma_core.workspace.project_files import git_project_files

    assert _rel(git_project_files(repo), repo) == [".gitignore", "notes/draft.py", "src/app.py"]


def test_a_folder_inside_a_repository_lists_its_own_part(repo: Path) -> None:
    from kazma_core.workspace.project_files import git_project_files

    assert _rel(git_project_files(repo / "src"), repo / "src") == ["app.py"]


def test_an_ignored_folder_asked_for_by_name_is_walked(repo: Path) -> None:
    from kazma_core.workspace.project_files import git_project_files

    assert git_project_files(repo / "vendor") is None


def test_a_folder_outside_any_repository_is_walked(tmp_path: Path) -> None:
    from kazma_core.workspace.project_files import git_project_files

    plain = tmp_path / "plain"
    _touch(plain / "a.py")
    assert git_project_files(plain) is None


async def test_file_search_skips_what_the_repository_ignores(repo: Path) -> None:
    """The agent's search finds the project's matches, not the ignored clone's."""
    assert await _search(repo, ".") == ["notes/draft.py", "src/app.py"]


async def test_file_search_still_searches_an_ignored_folder_it_is_pointed_at(repo: Path) -> None:
    assert await _search(repo, "vendor") == ["vendor/huge/lib.py"]


async def test_without_git_the_search_walks_everything(repo: Path, monkeypatch) -> None:
    """Negative control: the old walk returns the ignored clone's match too."""
    from kazma_core.workspace import project_files

    monkeypatch.setattr(project_files, "git_project_files", lambda root: None)
    assert await _search(repo, ".") == ["notes/draft.py", "src/app.py", "vendor/huge/lib.py"]


def test_the_code_index_reads_what_the_repository_keeps(repo: Path, monkeypatch) -> None:
    from kazma_core.code_index.walk import iter_source_files

    files = sorted(p.relative_to(repo.resolve()).as_posix() for p in iter_source_files(repo))
    assert files == ["notes/draft.py", "src/app.py"]


async def _search(root: Path, path: str) -> list[str]:
    """Run the agent's file_search over *root*; the files it matched."""
    from kazma_core.agent.tool_builtins import filesystem
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from tests.test_tool_paths import _Capture

    cap = _Capture()
    filesystem.register_filesystem_tools(cap)
    async with workspace_path_scope(root):
        out = await cap.tools["file_search"]("TOKEN_NEEDLE", path=path, glob="*.py", limit=50)
    files = set()
    for line in str(out).splitlines():
        if "TOKEN_NEEDLE" in line:
            file_part = line.rsplit(":", 2)[0]
            files.add(Path(file_part).resolve().relative_to(root.resolve()).as_posix())
    return sorted(files)


# ── one list of folders no walk enters ───────────────────────────────────


def test_every_walk_skips_the_same_folders() -> None:
    """The search, the code index and the Workspace page kept three lists."""
    from kazma_core.agent.tool_builtins import filesystem
    from kazma_core.code_index import walk
    from kazma_core.workspace.project_files import GENERATED_DIRS
    from kazma_ui import workspace_api

    assert filesystem._WALK_SKIP_DIRS is GENERATED_DIRS
    assert walk.SKIP_DIRS is GENERATED_DIRS
    assert workspace_api._SKIP_DIRS is GENERATED_DIRS


async def test_no_walk_enters_a_generated_folder(tmp_path: Path) -> None:
    """Outside any repository, each walk returns the project's file only."""
    from kazma_core.code_index.walk import iter_source_files
    from kazma_ui import workspace_api as wa

    root = tmp_path / "plain"
    _touch(root / "src" / "real.py", "TOKEN_NEEDLE = 0\n")
    for name in ("AppData", "node_modules", "target", "kazma-data"):
        _touch(root / name / "x" / "junk.py", "TOKEN_NEEDLE = 9\n")
    assert await _search(root, ".") == ["src/real.py"]
    assert [p.relative_to(root.resolve()).as_posix() for p in iter_source_files(root)] == ["src/real.py"]
    assert [f["path"] for f in wa._scan_recent_files(root, 20)] == ["src/real.py"]


def test_the_repository_ignores_windows_user_data() -> None:
    """A command run with HOME at the checkout left AppData/ in the live install."""
    repo = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        ["git", "check-ignore", "-q", "AppData/Local/uv/cache/x"], cwd=str(repo), capture_output=True
    )
    assert res.returncode == 0, "AppData/ is not in .gitignore"
