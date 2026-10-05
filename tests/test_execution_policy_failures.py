"""Policy and output-fence failures must not grant host capability."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from kazma_core.safety.post_hitl import host_shell_allowed
from kazma_core.tools.code_exec import local_exec_forbidden


@pytest.mark.parametrize("docker", ["force", "required", "1", "true", "on", "yes", "docker"])
def test_forced_container_profile_requires_explicit_host_shell(monkeypatch, docker):
    monkeypatch.setenv("KAZMA_CODE_EXEC_DOCKER", docker)
    monkeypatch.delenv("KAZMA_HOST_SHELL", raising=False)
    monkeypatch.delenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", raising=False)
    assert not host_shell_allowed()
    assert local_exec_forbidden()


def test_production_blocks_host_shell_unless_explicitly_granted(monkeypatch):
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    monkeypatch.delenv("KAZMA_HOST_SHELL", raising=False)
    assert not host_shell_allowed()
    monkeypatch.setenv("KAZMA_HOST_SHELL", "1")
    assert host_shell_allowed()


def test_policy_lookup_failure_refuses_host_fallback(monkeypatch):
    import kazma_core.security.platform_rbac as rbac
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.delenv("KAZMA_HOST_SHELL", raising=False)
    monkeypatch.delenv("KAZMA_CODE_EXEC_ALLOW_LOCAL", raising=False)
    monkeypatch.delenv("KAZMA_CODE_EXEC_DOCKER", raising=False)
    monkeypatch.setattr(rbac, "multi_user_enabled", lambda: (_ for _ in ()).throw(RuntimeError("policy unavailable")))
    assert local_exec_forbidden()
    assert not host_shell_allowed()


@pytest.mark.asyncio
async def test_failed_fence_withholds_untrusted_mcp_output(monkeypatch):
    from kazma_core.mcp.manager import AsyncMCPManager, MCPServerHandle
    import kazma_core.safety.prompt_fence as fence
    manager = AsyncMCPManager()
    manager._servers["fixture"] = MCPServerHandle(name="fixture", transport="stdio", connected=True)
    monkeypatch.setattr(manager, "_send", AsyncMock(return_value={"content": [{"type": "text", "text": "hostile-secret-payload"}]}))
    monkeypatch.setattr(fence, "fence_untrusted", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("unavailable")))
    result = await manager.execute_mcp_tool("fixture", "read_fixture", {})
    assert result["is_error"]
    assert "hostile-secret-payload" not in result["content"]
