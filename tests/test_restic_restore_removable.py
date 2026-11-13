"""A restic restore can be removed by the account that made it.

restic restores a snapshot under the source's absolute path and gives each
folder it rebuilt above the backup the REAL folder's recorded rights.
``C:\\Users`` lets an ordinary account read but not delete, so pytest could
not remove the restic tests' restores and kept every full run's temp folder:
29 of them, 7.5 GB, found on 2026-11-13. ``restic_repo.restore`` now gives
the rebuilt parents the target's rights; the restored files keep theirs.

Kept apart from ``test_restic_repo.py``: the CI recovery-drills job runs that
file and fails on any skip, and two cases here need Windows.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from kazma_core.backup import restic_repo as rr

_PW = "test-passphrase-not-a-real-secret"

_needs_restic_on_windows = pytest.mark.skipif(
    os.name != "nt" or not rr.restic_available(),
    reason="restic on Windows rebuilds parents with their recorded rights",
)


def _remove_like_pytest(path: Path) -> None:
    """Remove *path* the way pytest removes its temp folders: an entry it
    cannot remove is made writable and tried once more, nothing else."""
    import shutil

    def retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


@pytest.fixture
def repo(tmp_path):
    path = str(tmp_path / "repo")
    res = rr.init_repo(path, _PW)
    assert res.ok, res.error
    return path


@pytest.fixture
def locked_source(tmp_path):
    """A payload below two folders that deny this account deleting, the way
    ``C:\\Users`` does: ``parent`` refuses deleting what is in it, ``locked``
    refuses being deleted. A deny for the account's own SID holds even when
    the test runs as an administrator, so the case does not depend on where
    the temp folder is or on the account's rights."""
    sid = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True, stdin=subprocess.DEVNULL,
    ).stdout.strip().split(",")[-1].strip('"')
    parent = tmp_path / "src" / "parent"
    locked = parent / "locked"
    data = locked / "data"
    data.mkdir(parents=True)
    (data / "kazma.yaml").write_text("mcp: []\n", encoding="utf-8")
    for folder, right in ((parent, "DC"), (locked, "DE")):
        subprocess.run(["icacls", str(folder), "/deny", f"*{sid}:({right})"],
                       capture_output=True, check=True, stdin=subprocess.DEVNULL)
    yield data
    for folder in (parent, locked):
        subprocess.run(["icacls", str(folder), "/reset"], capture_output=True,
                       check=False, stdin=subprocess.DEVNULL)


@_needs_restic_on_windows
def test_a_restore_can_be_removed_by_the_account_that_made_it(repo, locked_source, tmp_path):
    """The rebuilt parents take the target's rights; the restored files are
    intact and the whole restore goes the way pytest removes a temp folder."""
    assert rr.backup(repo, _PW, [str(locked_source)]).ok
    target = tmp_path / "restored"
    res = rr.restore(repo, _PW, str(target))
    assert res.ok, res.error
    assert next(target.rglob("kazma.yaml")).read_text(encoding="utf-8") == "mcp: []\n"
    assert res.detail.get("parents_released", 0) >= 3, res.detail
    assert "parents_kept" not in res.detail, res.detail

    _remove_like_pytest(target)
    assert not target.exists()


@_needs_restic_on_windows
def test_control_restic_alone_leaves_a_restore_its_account_cannot_remove(
        repo, locked_source, tmp_path):
    """Negative control: the same restore made by restic's command alone, as
    ``restore`` ran it before, cannot be removed by the account that made it."""
    assert rr.backup(repo, _PW, [str(locked_source)]).ok
    raw = tmp_path / "raw"
    raw.mkdir()
    rr._run(["restore", "latest", "--target", str(raw)], repo, _PW, action="restore")
    assert next(raw.rglob("kazma.yaml")).is_file()
    try:
        with pytest.raises(PermissionError) as refused:
            _remove_like_pytest(raw)
        assert str(refused.value.filename).endswith("locked"), refused.value
    finally:
        # The control leaves nothing behind: the owner resets every right.
        subprocess.run(["icacls", str(raw), "/reset", "/T", "/C", "/Q"],
                       capture_output=True, check=False, stdin=subprocess.DEVNULL)
        _remove_like_pytest(raw)
    assert not raw.exists()


def test_rebuilt_parents_are_the_folders_above_each_backed_up_path(tmp_path):
    """Only the folders restic rebuilt are changed, top down; the backed-up
    folder keeps the rights its snapshot recorded. A path that is not on a
    drive letter, or a drive root, rebuilt none."""
    target = tmp_path / "t"
    for rel in ("C/a/b/data", "C/a/other", "D/x/y"):
        (target / rel).mkdir(parents=True)
    got = rr._rebuilt_parents(target, [r"C:\a\b\data", r"C:\a\other", r"D:\x\y",
                                       r"\\server\share\z", "C:\\"])
    assert got == [target / "C", target / "D", target / "C" / "a",
                   target / "D" / "x", target / "C" / "a" / "b"]


def _restic_restores_outside_restore(sources: dict[str, str]) -> list[str]:
    """Every ``restic restore`` built anywhere but ``restic_repo.restore``."""
    import ast

    found = []
    for name, src in sources.items():
        tree = ast.parse(src)
        allowed: set[int] = set()
        if name.endswith("restic_repo.py"):
            for fn in ast.walk(tree):
                if isinstance(fn, ast.FunctionDef) and fn.name == "restore":
                    allowed |= {id(n) for n in ast.walk(fn)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)) or id(node) in allowed:
                continue
            words = [e.value if isinstance(e, ast.Constant) else None for e in node.elts]
            restic_command = "restic" in words and "restore" in words
            # restic_repo's own runner takes the arguments without "restic".
            run_command = name.endswith("restic_repo.py") and words[:1] == ["restore"]
            if restic_command or run_command:
                found.append(f"{name}:{node.lineno}")
    return found


def test_every_restic_restore_goes_through_restore():
    """``restic_repo.restore`` is what releases the rebuilt parents, so no
    product code or script runs ``restic restore`` any other way."""
    root = Path(__file__).resolve().parents[1]
    sources = {}
    for folder in ("kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli",
                   "kazma-tui", "kazma-skills", "scripts"):
        for f in (root / folder).rglob("*.py"):
            if "tests" not in f.relative_to(root).parts:
                sources[f.relative_to(root).as_posix()] = f.read_text(encoding="utf-8")
    assert "kazma-core/kazma_core/backup/restic_repo.py" in sources
    assert _restic_restores_outside_restore(sources) == []

    planted = {
        "scripts/x.py": 'subprocess.run(["restic", "restore", "latest", "--target", t])',
        "kazma-core/kazma_core/backup/restic_repo.py":
            'def other():\n    _run(["restore", s, "--target", t], r, p, action="restore")\n',
    }
    assert len(_restic_restores_outside_restore(planted)) == 2
