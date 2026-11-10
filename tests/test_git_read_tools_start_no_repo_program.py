"""git_status, git_diff and git_log start no program the repository names.

The three are read tier: they run with no approval card, in whatever
repository the workspace is. Only ``core.fsmonitor`` was switched off (audit
2026-10-26 AUD-089). A repository's ``.git/config`` can also name an external
diff (``diff.external``), a textconv driver (``diff.<driver>.textconv``) and a
clean filter (``filter.<driver>.clean``), each a command git starts on
``git diff`` / ``git show`` / ``git status`` (2026-11-10 audit). The tools now
pass ``--no-ext-diff --no-textconv`` and empty every filter driver the
configuration defines (``tools._git_read``).

A real repository, whose drivers run a script that writes a marker file; the
negative control runs the same commands as the tools built them before.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kazma_skills.native.git_github_manager import tools as git_tools

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [GIT, "-c", "core.fsmonitor=false", "-c", "user.name=T",
         "-c", "user.email=t@example.invalid", *args],
        cwd=repo, capture_output=True, text=True, check=False,
    )


@pytest.fixture
def armed_repo(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = tmp_path / "ran.txt"
    helper = tmp_path / "helper.py"
    helper.write_text(
        "import sys\n"
        f"open({str(marker)!r}, 'a').write(sys.argv[1] + '\\n')\n"
        "if sys.argv[1] == 'clean':\n"
        "    sys.stdout.write(sys.stdin.read())\n"
        "elif sys.argv[1] == 'textconv':\n"
        "    sys.stdout.write(open(sys.argv[2]).read())\n",
        encoding="utf-8",
    )
    run = f'"{Path(sys.executable).as_posix()}" "{helper.as_posix()}"'
    _git(repo, "init", "-q")
    (repo / ".gitattributes").write_text("*.txt diff=conv filter=keep\n", encoding="utf-8")
    (repo / "a.txt").write_text("line 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")
    (repo / "a.txt").write_text("line 2\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "second")
    # Armed only now, so setting the repository up ran none of them.
    _git(repo, "config", "diff.external", f"{run} external")
    _git(repo, "config", "diff.conv.textconv", f"{run} textconv")
    _git(repo, "config", "filter.keep.clean", f"{run} clean")
    _git(repo, "config", "filter.keep.smudge", f"{run} smudge")
    # Same size, new content: git must read the file through its filter.
    (repo / "a.txt").write_text("line 3\n", encoding="utf-8")
    monkeypatch.setattr(git_tools, "_get_workspace", lambda: str(repo))
    return repo, marker


def test_the_read_tools_start_none_of_the_repository_programs(armed_repo):
    repo, marker = armed_repo
    status = asyncio.run(git_tools.git_status())
    assert "a.txt" in status, status
    diff = asyncio.run(git_tools.git_diff())
    assert "+line 3" in diff, diff
    shown = asyncio.run(git_tools.git_diff(target="HEAD~1"))
    assert "line 1" in shown or "first" in shown, shown
    ranged = asyncio.run(git_tools.git_diff(target="HEAD~1..HEAD"))
    assert "+line 2" in ranged, ranged
    log = asyncio.run(git_tools.git_log(count=2, path="a.txt"))
    assert "second" in log
    assert not marker.exists(), marker.read_text()


def test_the_filter_drivers_are_found_in_the_configuration(armed_repo):
    repo, _marker = armed_repo
    listing = _git(repo, "config", "--get-regexp",
                   r"^filter\..*\.(clean|smudge|process)$").stdout
    overrides = git_tools._filter_driver_overrides(listing)
    assert "filter.keep.clean=" in overrides
    assert "filter.keep.process=" in overrides


def test_control_the_old_commands_start_them(armed_repo):
    """The argv the tools used before: ``git -c core.fsmonitor=false <args>``."""
    repo, marker = armed_repo

    def old(*args: str) -> None:
        subprocess.run([GIT, "-c", "core.fsmonitor=false", *args], cwd=repo,
                       capture_output=True, text=True, check=False)

    old("status", "--porcelain")
    assert "clean" in marker.read_text(), "git no longer runs the clean filter: this proves nothing"
    old("diff")
    assert "external" in marker.read_text()
    marker.unlink()
    old("show", "--format=%h %s", "HEAD~1")
    assert marker.exists(), "git show no longer runs diff.external: this proves nothing"
    marker.unlink()
    # --no-ext-diff alone is not enough: the textconv driver still runs.
    old("show", "--no-ext-diff", "--format=%h %s", "HEAD~1")
    assert "textconv" in marker.read_text()
