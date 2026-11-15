"""git_commit never stages one of Kazma's own files.

2026-11 review, the sibling of shell_exec reading a folder that holds
Kazma's secret files: on an install whose workspace IS its install folder,
``git_commit`` without ``files`` ran ``git add .``, and a secret file the
repository does not ignore (``restic.pass`` is not in Kazma's
``.gitignore``) went into the index and the next commit, where ``git_diff``
and ``git_log`` print it. Now the tool asks git what it would stage
(``git add --dry-run``, which changes nothing) and refuses, before anything
is staged, when that holds one of Kazma's files.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from kazma_core.agent.tool_refusal import RefusedBeforeEffect

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")

SECRET = "demo-restic-passphrase"


@pytest.fixture()
def install(tmp_path, monkeypatch):
    from kazma_core import git_identity, paths
    from kazma_core.security.child_env import tool_child_env

    root = tmp_path / "install"
    (root / "kazma-data" / "workspace").mkdir(parents=True)
    monkeypatch.setenv("KAZMA_DATA_DIR", str(root / "kazma-data"))
    monkeypatch.setenv("KAZMA_USER_HOME", str(root / ".kazma"))
    monkeypatch.setattr(paths, "installed_project_root", lambda: root)
    monkeypatch.setattr(paths, "get_project_root", lambda: root)
    monkeypatch.setattr(git_identity, "get_bot_identity", lambda: None)
    env = tool_child_env()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=root, check=True, env=env)
    (root / ".gitignore").write_text("kazma-data/\n.kazma/\n", encoding="utf-8")
    (root / "restic.pass").write_text(SECRET + "\n", encoding="utf-8")
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    return root


def _staged(root) -> str:
    return subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=root,
                          capture_output=True, text=True).stdout


async def test_committing_everything_refuses_a_kazma_secret(install):
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_skills.native.git_github_manager.tools import git_commit

    async with workspace_path_scope(install):
        with pytest.raises(RefusedBeforeEffect) as refused:
            await git_commit("everything")
    assert "restic.pass" in str(refused.value)
    assert SECRET not in str(refused.value)
    assert _staged(install) == ""


async def test_naming_the_secret_is_refused_and_a_plain_file_commits(install):
    from kazma_core.ide.workspace_scope import workspace_path_scope
    from kazma_skills.native.git_github_manager.tools import git_commit

    async with workspace_path_scope(install):
        with pytest.raises(RefusedBeforeEffect):
            await git_commit("secret", files=["restic.pass"])
        assert _staged(install) == ""
        await git_commit("notes", files=["notes.txt"])
    log = subprocess.run(["git", "log", "--name-only", "--format=%s"], cwd=install,
                         capture_output=True, text=True).stdout
    assert "notes.txt" in log and "restic.pass" not in log


def test_control_git_itself_would_stage_the_secret(install):
    """Negative control: without the check, ``git add .`` stages it."""
    from kazma_core.security.child_env import tool_child_env

    subprocess.run(["git", "add", "."], cwd=install, check=True, env=tool_child_env())
    assert "restic.pass" in _staged(install)
