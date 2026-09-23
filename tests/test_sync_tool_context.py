"""A sync tool runs in a worker thread and still sees the caller's context.

Regression for the 2026-09-22 audit. ``LocalToolRegistry.execute`` ran sync
tools through ``loop.run_in_executor(None, ...)``, which — unlike
``asyncio.to_thread`` — does not copy ContextVars. The tenant, the per-task
workspace scope, the thread id and the HITL double-gating flags all live in
ContextVars, so the first sync tool would have read the global workspace and
no tenant (every vault secret ``None``). Every built-in tool happened to be
async, so it had not bitten yet.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from kazma_core.agent.tool_registry import LocalToolRegistry


@pytest.mark.asyncio
async def test_sync_tool_sees_the_callers_tenant(monkeypatch):
    from kazma_core.tenant_context import get_current_tenant_id, tenant_scope

    registry = LocalToolRegistry(include_builtins=False)

    @registry.register(description="report the tenant", category="test")
    def which_tenant() -> str:  # deliberately sync
        return str(get_current_tenant_id())

    monkeypatch.setattr(
        "kazma_core.safety.commitment.authorize_effect",
        lambda *a, **k: SimpleNamespace(
            decision="allow", reason="ok", rewritten_args=None, clarify_question=None
        ),
    )
    with tenant_scope("acme"):
        out = await registry.execute("which_tenant", {})
    assert out["is_error"] is False, out
    assert out["content"] == "acme"
