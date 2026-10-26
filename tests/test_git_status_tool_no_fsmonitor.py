"""The agent's ``git_status`` never runs the repository's fsmonitor
(audit 2026-10-26 AUD-089).

``core.fsmonitor`` names a command, chosen by whoever wrote the repository's
``.git/config``, and ``git status`` runs it. ``git_status`` is read tier (no
approval card) and ran plain ``git status``; its siblings ``git_log`` and
``git_diff`` already turned the hook off.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def hooked_repo(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    git = shutil.which("git")
    if not git:
        pytest.skip("git is not installed")

    def setup(*args: str) -> None:
        subprocess.run([git, "-c", "core.fsmonitor=false", *args], cwd=tmp_path,
                       capture_output=True, text=True, check=True)

    setup("init")
    setup("config", "user.name", "Test")
    setup("config", "user.email", "test@example.invalid")
    (tmp_path / "file.txt").write_text("before\n", encoding="utf-8")
    setup("add", "file.txt")
    setup("commit", "-m", "baseline")
    marker = tmp_path / "fsmonitor-ran.txt"
    helper = tmp_path / "helper.py"
    helper.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
                      encoding="utf-8")
    setup("config", "core.fsmonitor", f'"{sys.executable}" "{helper}"')
    (tmp_path / "file.txt").write_text("after\n", encoding="utf-8")
    monkeypatch.setattr(
        "kazma_skills.native.git_github_manager.tools._get_workspace", lambda: tmp_path
    )
    return tmp_path, marker


def test_git_status_does_not_run_the_hook(hooked_repo) -> None:
    from kazma_skills.native.git_github_manager.tools import git_status

    _repo, marker = hooked_repo
    out = asyncio.run(git_status())
    assert "file.txt" in out, out
    assert not marker.exists()


def test_negative_control_plain_git_status_runs_it(hooked_repo) -> None:
    repo, marker = hooked_repo
    subprocess.run([shutil.which("git"), "status", "--porcelain"], cwd=repo,
                   capture_output=True, text=True, check=False)
    assert marker.exists(), "git no longer runs core.fsmonitor: this test proves nothing"
