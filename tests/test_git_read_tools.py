"""Kazma reads a repository's history without a shell.

Live 2026-10-19 (Telegram): "Run git log -3 --oneline" could not be answered.
Host shell_exec is off by policy, python_exec's sandbox refuses subprocess,
and the only native git read tool was git_status. ``git_log`` and
``git_diff`` are read-tier native tools; their output (commit messages,
file contents) is fenced as data.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from kazma_skills.native.git_github_manager import tools as git_tools


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                   cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    for n in range(1, 4):
        (tmp_path / "a.txt").write_text(f"line {n}\n", encoding="utf-8")
        _git(tmp_path, "add", "a.txt")
        _git(tmp_path, "commit", "-q", "-m", f"commit number {n}")
    monkeypatch.setattr(git_tools, "_get_workspace", lambda: str(tmp_path))
    return tmp_path


def test_git_log_reads_the_newest_commits(repo):
    out = asyncio.run(git_tools.git_log(count=2))
    assert "commit number 3" in out and "commit number 2" in out and "commit number 1" not in out
    assert "kazma:data" in out  # fenced: commit messages are data


def test_git_log_full_form_and_bound(repo):
    out = asyncio.run(git_tools.git_log(count=10_000, oneline=False))
    assert out.count("Author: T") == 3


def test_git_diff_shows_uncommitted_changes_and_a_commit(repo):
    (repo / "a.txt").write_text("changed\n", encoding="utf-8")
    assert "+changed" in asyncio.run(git_tools.git_diff())
    assert "a.txt" in asyncio.run(git_tools.git_diff(stat=True))
    assert "commit number 2" in asyncio.run(git_tools.git_diff(target="HEAD~1"))


def test_git_diff_refuses_an_option_as_target(repo):
    assert asyncio.run(git_tools.git_diff(target="--output=x")).startswith("Error:")


def test_they_are_read_tier_and_native_tools():
    from pathlib import Path

    import yaml

    from kazma_core.safety.hitl import TOOL_TIERS

    manifest = yaml.safe_load((Path(git_tools.__file__).with_name("skill_manifest.yaml")).read_text(encoding="utf-8"))
    for name in ("git_log", "git_diff"):
        assert TOOL_TIERS[name] == "read"
        assert name in manifest["tools"]
