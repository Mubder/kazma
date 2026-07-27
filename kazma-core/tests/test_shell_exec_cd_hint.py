"""shell_exec should give a clear hint when cd is blocked."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_shell_exec_cd_hint(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.agent.tool_registry import LocalToolRegistry
    from kazma_core.workspace.binding import configure_workspace

    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    configure_workspace(str(tmp_path))
    reg = LocalToolRegistry(include_builtins=True)
    # Bypass HITL if present — execute may gate danger tools
    monkeypatch.setenv("KAZMA_ALLOW_HEADLESS_DANGER", "1")
    try:
        from kazma_core.swarm.safety import SafetyMiddleware, set_safety

        set_safety(SafetyMiddleware(allow_headless_danger=True))
    except Exception:
        pass

    res = await reg.execute("shell_exec", {"command": "cd /tmp"})
    content = res.get("content") or ""
    # Either denied by allowlist with cd hint, or HITL — both should mention cd
    assert "cd" in content.lower()
    assert (
        "not in the allowed" in content.lower()
        or "not allowed" in content.lower()
        or "approval" in content.lower()
        or "denied" in content.lower()
    )
    if "not in the allowed" in content.lower():
        assert "workspace" in content.lower() or "absolute" in content.lower()
