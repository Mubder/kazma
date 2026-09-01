"""Wave 4: H-8 / H-9 / M-3 — HITL-adjacent, registry-safe."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from kazma_core.agent.graph_builder import tool_worker_node
from kazma_core.agent.state import initial_supervisor_state
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.swarm.safety import SafetyMiddleware


class _Tracer:
    def trace_tool_execution(self, *a, **k):
        pass


class _Exec:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        return {"content": "ok", "is_error": False}


def test_narrowed_require_approval_for_cannot_ungate_danger(monkeypatch):
    """H-9: Settings list ADDS; TOOL_TIERS danger floor still gates the bus."""
    monkeypatch.setattr(
        "kazma_core.safety.hitl.get_hitl_config",
        lambda _cfg=None: {
            "enabled": True,
            "require_approval_for": ["file_write"],
        },
    )
    safety = SafetyMiddleware(enabled=True, allow_headless_danger=False)
    safety.remove_danger_tool("shell_exec")
    assert safety.is_danger_tool("shell_exec") is True
    assert safety.check_sync("shell_exec") is False
    assert safety.is_danger_tool("file_read") is False
    assert safety.check_sync("file_read") is True
    # MCP-style names without a tier stay classification-false unless forced.
    assert safety.is_danger_tool("write_file") is False


@pytest.mark.asyncio
async def test_execute_applies_rewritten_args(monkeypatch):
    """H-8: allow + rewritten_args must win on the IDE/swarm choke."""
    seen: dict[str, str] = {}
    registry = LocalToolRegistry(include_builtins=False)

    @registry.register(description="echo", category="test")
    async def echo_tool(text: str = "") -> str:
        seen["text"] = text
        return text

    monkeypatch.setattr(
        "kazma_core.safety.commitment.authorize_effect",
        lambda *a, **k: SimpleNamespace(
            decision="allow",
            reason="ok",
            rewritten_args={"text": "stored-proposal"},
            clarify_question=None,
        ),
    )
    out = await registry.execute("echo_tool", {"text": "model-hallucination"})
    assert out["is_error"] is False
    assert seen.get("text") == "stored-proposal"


@pytest.mark.asyncio
async def test_execute_clarify_fail_closed_does_not_mint_a_gate(monkeypatch):
    """H-8: clarify/confirm must not register a second gate row."""
    registry = LocalToolRegistry(include_builtins=False)

    @registry.register(description="echo", category="test")
    async def echo_tool(text: str = "") -> str:
        raise AssertionError("must not execute on clarify")

    def _no_gate(*_a, **_k):
        raise AssertionError("register_gate must not run from execute()")

    monkeypatch.setattr(
        "kazma_core.safety.commitment.authorize_effect",
        lambda *a, **k: SimpleNamespace(
            decision="clarify",
            reason="which date?",
            rewritten_args=None,
            clarify_question="which date?",
        ),
    )
    monkeypatch.setattr(
        "kazma_core.safety.hitl_gates.register_gate",
        _no_gate,
    )
    out = await registry.execute("echo_tool", {"text": "x"})
    assert out["is_error"] is True
    assert "run this from chat" in out["content"].lower()
    assert "clarify" in out["content"].lower()


@pytest.mark.anyio
async def test_tool_worker_live_reads_hitl_config(monkeypatch):
    """M-3: compiled snapshot listed file_read; live config does not.

    Without the live-read, requires_approval(file_read) would interrupt().
    """
    monkeypatch.setattr(
        "kazma_core.safety.hitl.get_hitl_config",
        lambda _cfg=None: {
            "enabled": True,
            "require_approval_for": [],
        },
    )
    state = initial_supervisor_state()
    state["tool_calls_pending"] = [
        {"id": "c1", "name": "file_read", "arguments": {"path": "."}},
    ]
    exe = _Exec()
    snapshot = {
        "enabled": True,
        "require_approval_for": ["file_read"],
    }
    result = await tool_worker_node(
        state,
        tool_executor=exe,
        tracer=_Tracer(),
        hitl_config=snapshot,
    )
    assert exe.calls == [("file_read", {"path": "."})]
    assert result.get("next_node") is not None
