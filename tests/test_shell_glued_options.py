"""Post-approval shell options cannot hide paths or executable helpers."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def shell(tmp_path, monkeypatch):
    from kazma_core.agent.tool_builtins.system import register_system_tools
    from kazma_core.safety import post_hitl
    from kazma_core.security import process_budget
    from kazma_core.tools import file_write

    tools = {}

    class Registry:
        def register(self, **kwargs):
            def add(func):
                tools[func.__name__] = func
                return func
            return add

    register_system_tools(Registry())
    monkeypatch.setenv("KAZMA_HOST_SHELL", "1")
    monkeypatch.setenv("KAZMA_SHELL_ALLOW_ARCHIVE", "1")
    monkeypatch.setattr(file_write, "_get_workspace", lambda: tmp_path)
    monkeypatch.setattr(post_hitl, "resolve_shell_binary", lambda name, **_: name)
    run = AsyncMock(side_effect=AssertionError("unsafe command reached the child"))
    monkeypatch.setattr(process_budget, "run_bounded_async", run)
    return tools["shell_exec"], run


@pytest.mark.parametrize("command", [
    "tar -cf archive.tar -C.. secret.txt", "tar -cC.. -f archive.tar secret.txt",
    "tar -cf../outside.tar file.txt", "tar -Ievil -cf archive.tar file.txt",
    "tar -cIevil -f archive.tar file.txt", "tar -T../outside -cf archive.tar",
    "git -ccore.fsmonitor=evil status", "grep -f../secrets pattern", "jq -f../secrets",
    "tar cvf archive.tar .", "tar -xf archive.tar",
    "grep -nrf../secrets pattern", "jq -rf../secrets",
    "zip -qb../outside archive.zip file.txt", "zip -qO../outside.zip archive.zip file.txt",
    "unzip -qd../outside archive.zip",
])
@pytest.mark.asyncio
async def test_glued_flags_refuse_before_execution(shell, command):
    execute, run = shell
    assert (await execute(command)).startswith("Error:")
    run.assert_not_called()


@pytest.mark.parametrize("strict", ["0", "1"])
@pytest.mark.asyncio
async def test_unresolved_binary_never_falls_back_to_os_search(shell, monkeypatch, strict):
    from kazma_core.safety import post_hitl

    execute, run = shell
    monkeypatch.setenv("KAZMA_SHELL_STRICT", strict)
    monkeypatch.setattr(post_hitl, "resolve_shell_binary", lambda *a, **k: None)
    assert "could not resolve" in await execute("git status")
    run.assert_not_called()


def test_binary_search_uses_absolute_directories_without_current_directory(tmp_path, monkeypatch):
    import os
    from kazma_core.safety.post_hitl import resolve_shell_binary

    trusted = tmp_path / "trusted"
    current = tmp_path / "current"
    trusted.mkdir()
    current.mkdir()
    suffix = ".exe" if os.name == "nt" else ""
    safe = trusted / ("proof" + suffix)
    evil = current / ("proof" + suffix)
    for file in (safe, evil):
        file.write_bytes(b"synthetic-executable")
        file.chmod(0o755)
    monkeypatch.chdir(current)
    assert resolve_shell_binary("proof", restricted_path=str(trusted)) == str(safe)
    assert resolve_shell_binary("proof", restricted_path=".") is None


def test_trusted_directory_cannot_redirect_a_binary_outside_it(tmp_path):
    import os
    from kazma_core.safety.post_hitl import resolve_shell_binary

    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"synthetic-executable")
    outside.chmod(0o755)
    try:
        (trusted / ("proof.exe" if os.name == "nt" else "proof")).symlink_to(outside)
    except OSError:
        pytest.skip("host does not allow symlink creation")
    assert resolve_shell_binary("proof", restricted_path=str(trusted)) is None
