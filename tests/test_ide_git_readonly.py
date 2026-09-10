"""Read-only git must not HITL via shell_exec (Telegram REJECTED spam)."""

from __future__ import annotations

import subprocess
from pathlib import Path

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
