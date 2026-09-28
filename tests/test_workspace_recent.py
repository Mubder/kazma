"""The Workspace page's recent files: the project's own files, fast, never empty by accident.

``_scan_recent_files`` walked the whole workspace with ``rglob`` -- ``.venv``,
``.git``, ``kazma-data`` and its backups included -- and one unreadable folder
anywhere returned nothing: on the live install (whose workspace is the install
folder) the page waited 30 s for an empty list (2026-09-28). A repository's
files now come from git; any other folder gets a walk that skips hidden and
generated folders and a folder it cannot read, not the whole scan.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from kazma_ui import workspace_api as wa


def _touch(path: Path, text: str = "x", age: float = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if age:
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True,
    )


def _paths(root: Path, limit: int = 20) -> list[str]:
    return [f["path"] for f in wa._scan_recent_files(root, limit)]


def test_a_repository_lists_its_own_files_newest_first(tmp_path):
    root = tmp_path / "repo"
    _touch(root / "src" / "old.py", age=3600)
    _touch(root / "README.md", age=60)
    _touch(root / "src" / "new.py")
    _touch(root / ".gitignore", "node_modules/\nbuild/\n")
    _touch(root / "node_modules" / "pkg" / "index.js")  # ignored by the repository
    _touch(root / ".venv" / "lib" / "site.py")  # hidden folder
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "one")
    _touch(root / "notes" / "draft.md")  # untracked but not ignored: part of the project
    paths = _paths(root)
    assert set(paths) == {"notes/draft.md", "src/new.py", "README.md", "src/old.py"}
    assert paths[-2:] == ["README.md", "src/old.py"]


def test_git_runs_without_the_servers_secrets_or_the_repositorys_programs(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    seen: dict = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, env=kw.get("env"))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setenv("KAZMA_VAULT_KEY", "must-not-reach-git")
    monkeypatch.setattr(wa.subprocess, "run", fake_run)
    assert wa._git_project_files(root) == []
    assert "core.fsmonitor=false" in seen["argv"]
    assert "KAZMA_VAULT_KEY" not in seen["env"]


def test_a_folder_that_is_not_a_repository_is_walked_without_hidden_or_generated_trees(tmp_path):
    root = tmp_path / "ws"
    _touch(root / "a.txt", age=10)
    _touch(root / "docs" / "c.md")
    _touch(root / "node_modules" / "x.js")
    _touch(root / ".cache" / "y.txt")
    _touch(root / "kazma-data" / "backups" / "z.db")
    _touch(root / ".env")
    assert _paths(root) == ["docs/c.md", "a.txt"]


def test_the_sandbox_inside_an_ignoring_checkout_is_walked(tmp_path):
    """The default sandbox sits inside the install's checkout, which ignores
    it: git would list nothing there, so only a repository's own top level
    asks git."""
    install = tmp_path / "install"
    _touch(install / ".gitignore", "kazma-data/\n")
    _git(install, "init", "-q")
    sandbox = install / "kazma-data" / "workspace"
    _touch(sandbox / "notes.md")
    assert _paths(sandbox) == ["notes.md"]


def test_git_missing_falls_back_to_the_walk(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    _touch(root / "main.py")

    def no_git(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(wa.subprocess, "run", no_git)
    assert _paths(root) == ["main.py"]


def test_an_unreadable_folder_costs_only_itself(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    _touch(root / "readable" / "ok.txt")
    _touch(root / "locked" / "secret.txt")
    real_scandir = os.scandir

    def scandir(path="."):
        if Path(path).name == "locked":
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert _paths(root) == ["readable/ok.txt"]


def _old_scan(root: Path) -> list[Path]:
    """The scan as it was until 2026-09-28 (its walk and its error handling)."""
    files = []
    try:
        for p in root.rglob("*"):
            if p.is_file() and not p.name.startswith("."):
                files.append(p)
    except PermissionError:
        return []
    return files


def test_the_old_scan_returned_nothing_for_one_unreadable_folder(tmp_path, monkeypatch):
    """Negative control: the same situation emptied the old scan."""
    root = tmp_path / "ws"
    _touch(root / "readable" / "ok.txt")

    def rglob(self, pattern):
        yield root / "readable" / "ok.txt"
        raise PermissionError(13, "Permission denied", str(root / "locked"))

    monkeypatch.setattr(Path, "rglob", rglob)
    assert _old_scan(root) == []


def test_the_walk_stops_at_its_cap(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    for i in range(10):
        _touch(root / f"f{i}.txt")
    monkeypatch.setattr(wa, "_WALK_FILE_CAP", 3)
    assert len(wa._walk_files(root)) == 3
