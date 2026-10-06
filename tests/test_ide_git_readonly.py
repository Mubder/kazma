"""Read-only git must not HITL via shell_exec (Telegram REJECTED spam)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from kazma_core.ide.service import IdeService, _is_readonly_git


def test_status_and_diff_are_readonly() -> None:
    assert _is_readonly_git("status -sb")
    assert _is_readonly_git("status -s")
    assert _is_readonly_git("diff")
    assert _is_readonly_git("log -1 --oneline")
    assert _is_readonly_git("rev-parse --abbrev-ref HEAD")


def test_mutating_git_is_not_readonly() -> None:
    assert not _is_readonly_git("push origin main")
    assert not _is_readonly_git("commit -am x")
    assert not _is_readonly_git("clean -fdx")
    assert not _is_readonly_git("checkout -b tmp")
    assert not _is_readonly_git("stash drop")


@pytest.mark.asyncio
async def test_git_status_does_not_call_shell_exec(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from kazma_core.ide import service as svc

    monkeypatch.setattr(svc, "_resolve_workspace_root", lambda: tmp_path)
    called: list[str] = []

    async def _no_shell(self, command: str, timeout: int = 60):  # noqa: ARG001
        called.append(command)
        return {"ok": False, "error": "should not HITL", "output": ""}

    monkeypatch.setattr(IdeService, "run", _no_shell)

    def _fake_run(*a, **k):  # noqa: ARG001
        return subprocess.CompletedProcess(
            args=["git", "status", "-sb"], returncode=0, stdout="## main\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ide = IdeService()
    out = await ide.git("status -sb")
    assert called == []
    assert out["ok"] is True
    assert "main" in (out.get("output") or "")


@pytest.mark.parametrize("command", [
    "branch created", "branch --set-upstream-to=origin/main main",
    "branch -c copied", "branch --edit-description", "branch --track new origin/main",
])
def test_branch_mutations_require_approval(command):
    assert not _is_readonly_git(command)


@pytest.mark.parametrize("command", [
    "diff --no-index --output=../outside.txt a.txt b.txt",
    "diff --output=inside.txt", "log --output=inside.txt", "show --ext-diff",
    "diff --textconv", "rev-parse --git-path config", "remote show origin",
    "remote -v add added local-repository", "remote --verbose remove origin",
])
async def test_read_command_unsafe_options_never_spawn_or_write(monkeypatch, tmp_path, command):
    from kazma_core.ide import service as svc
    monkeypatch.setattr(svc, "_resolve_workspace_root", lambda: tmp_path)
    for name, content in (("a.txt", "first"), ("b.txt", "second")):
        (tmp_path / name).write_text(content, encoding="utf-8")
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unsafe read must be refused before dispatch")
    monkeypatch.setattr(subprocess, "run", forbidden)
    ide = IdeService()
    guarded_run = AsyncMock(side_effect=forbidden)
    monkeypatch.setattr(ide, "run", guarded_run)
    result = await ide.git(command)
    assert result["ok"] is False
    assert calls == []
    guarded_run.assert_not_awaited()
    assert not (tmp_path / "inside.txt").exists()
    assert not (tmp_path.parent / "outside.txt").exists()


@pytest.mark.parametrize("command", ["diff -- ../outside.txt", "show HEAD:../outside.txt"])
async def test_read_git_paths_cannot_escape_workspace(monkeypatch, tmp_path, command):
    from kazma_core.ide import service as svc
    monkeypatch.setattr(svc, "_resolve_workspace_root", lambda: tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("outside path must be refused before spawning")
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = await IdeService().git(command)
    assert result["ok"] is False
    assert "workspace" in result["error"].lower()


async def test_real_git_reads_do_not_run_repository_helpers(monkeypatch, tmp_path):
    import shutil
    import sys

    from kazma_core.ide import service as svc

    git = shutil.which("git")
    assert git
    def setup(*args):
        return subprocess.run([git, "-c", "core.fsmonitor=false", *args], cwd=tmp_path,
                              capture_output=True, text=True, check=True)
    setup("init")
    setup("config", "user.name", "Test")
    setup("config", "user.email", "test@example.invalid")
    (tmp_path / "file.txt").write_bytes(b"before\n")
    (tmp_path / ".gitattributes").write_bytes(b"file.txt diff=audit\n")
    setup("add", "file.txt", ".gitattributes")
    setup("commit", "-m", "baseline")
    marker = tmp_path / "helper-ran.txt"
    helper = tmp_path / "helper.py"
    helper.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\nprint('helper')\n", encoding="utf-8")
    helper_command = f'"{sys.executable}" "{helper}"'
    setup("config", "core.fsmonitor", helper_command)
    setup("config", "diff.audit.textconv", helper_command)
    setup("config", "diff.external", helper_command)
    (tmp_path / "file.txt").write_bytes(b"after\n")
    monkeypatch.setattr(svc, "_resolve_workspace_root", lambda: tmp_path)
    ide = IdeService()
    for command in ("status -sb", "diff -- file.txt", "show HEAD:file.txt", "log -1 --oneline", "branch --list", "blame file.txt"):
        result = await ide.git(command)
        assert result["ok"], (command, result)
    assert not marker.exists()


async def test_branch_creation_uses_the_existing_approval_path(monkeypatch, tmp_path):
    from kazma_core.ide import service as svc
    monkeypatch.setattr(svc, "_resolve_workspace_root", lambda: tmp_path)
    ide = IdeService()
    gate = AsyncMock(return_value={"ok": False, "error": "approval required", "output": ""})
    monkeypatch.setattr(ide, "run", gate)
    await ide.git("branch newbranch")
    gate.assert_awaited_once_with("git branch newbranch", timeout=60)


async def test_git_reads_keep_operator_newline_configuration(monkeypatch, tmp_path):
    import shutil

    from kazma_core.ide import service as svc

    git = shutil.which("git")
    assert git
    global_config = tmp_path / "operator.gitconfig"
    global_config.write_bytes(b"[core]\n\tautocrlf = true\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    def setup(*args):
        return subprocess.run([git, "-c", "core.fsmonitor=false", *args], cwd=workspace,
                              capture_output=True, text=True, check=True)
    setup("init")
    setup("config", "user.name", "Test")
    setup("config", "user.email", "test@example.invalid")
    (workspace / "file.txt").write_bytes(b"unchanged\r\n")
    setup("add", "file.txt")
    setup("commit", "-m", "baseline")
    monkeypatch.setattr(svc, "_resolve_workspace_root", lambda: workspace)
    result = await IdeService().git("status --porcelain")
    assert result["ok"]
    assert result["output"] == ""
