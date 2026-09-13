"""An approval the operator gave must be honoured wherever it is checked.

Reported: a tool approved in the Web UI was asked again on Telegram/Discord and
auto-rejected at the 300-second timeout —

    ❌ REJECTED
    Worker: mcp:filesystem
    Task: Tool: mcp__filesystem__get_file_info
    Danger-tier tool 'mcp__filesystem__get_file_info' requires approval

Two code paths gate tools, and they did not agree:

``hitl.requires_approval()``     the agent's own tools. Honours YOLO,
                                 ``has_task_grant`` and ``has_tool_grant``.
``swarm.safety.check()``         MCP servers and swarm workers. Honoured
                                 YOLO **only**.

So "allow this tool" in an approval card created a grant the first path
respected and the second could not see. The operator answered; the second path
never looked, and the 300s timer ran out in front of them.

This is the same defect as the provider layer's two ``test_provider`` routes
and its two frontend halves: one concept, two implementations, drifting until
the difference becomes a bug. The fix there was deletion. Here both paths are
load-bearing, so the invariant is tested instead — every standing-approval
check one path trusts, the other must trust too.
"""

from __future__ import annotations

import pytest


class TestBothGatesHonourTheSameGrants:
    def test_the_swarm_path_consults_every_grant_the_agent_path_does(self):
        """Parsed, not grepped. If someone adds a new standing-approval kind to
        `requires_approval` and not to `check`, an operator's answer starts
        being ignored on one surface again — silently, and only on the surface
        they were not looking at."""
        import ast
        import inspect

        from kazma_core.safety import hitl
        from kazma_core.swarm import safety

        def called_names(source: str) -> set[str]:
            tree = ast.parse(source.lstrip())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        names.add(func.id)
                    elif isinstance(func, ast.Attribute):
                        names.add(func.attr)
            return names

        agent_path = called_names(inspect.getsource(hitl.requires_approval))
        swarm_path = called_names(inspect.getsource(safety.SafetyMiddleware.check))
        # The swarm module wraps them so the hot path avoids a re-import.
        swarm_path |= {
            "has_tool_grant" if "_grant_tool" in swarm_path else "",
            "has_task_grant" if "_grant_task" in swarm_path else "",
        }

        standing = {"is_yolo_active", "has_task_grant", "has_tool_grant"}
        missing = (standing & agent_path) - swarm_path
        assert not missing, (
            "the MCP/swarm gate ignores standing approvals the agent gate "
            f"honours: {sorted(missing)} — an approval given in one surface "
            "will be re-asked and auto-rejected in another"
        )


class TestAToolGrantSuppressesTheSecondAsk:
    """The behaviour, not just the call graph."""

    @pytest.fixture
    def thread(self, tmp_path, monkeypatch):
        from kazma_core.config_store import ConfigStore, set_config_store

        store = ConfigStore(
            db_path=str(tmp_path / "grants.db"),
            yaml_path=str(tmp_path / "none.yaml"),
        )
        try:
            set_config_store(store)
        except Exception:  # pragma: no cover - older signature
            import kazma_core.config_store as cs_mod

            monkeypatch.setattr(cs_mod, "_store", store, raising=False)
        return "thread-under-test"

    def test_granting_a_tool_makes_has_tool_grant_true(self, thread):
        from kazma_core.safety.hitl_grants import grant_tool, has_tool_grant

        tool = "mcp__filesystem__get_file_info"
        assert has_tool_grant(thread, tool) is False
        grant_tool(thread, tool, actor="web:admin")
        assert has_tool_grant(thread, tool) is True

    def test_the_swarm_gate_reads_that_grant(self, thread):
        """The wrapper the gate actually calls — so the grant and the check
        cannot drift apart through a rename."""
        from kazma_core.safety.hitl_grants import grant_tool
        from kazma_core.swarm.safety import _grant_tool

        tool = "mcp__filesystem__get_file_info"
        assert _grant_tool(thread, tool) is False
        grant_tool(thread, tool, actor="web:admin")
        assert _grant_tool(thread, tool) is True

    def test_a_grant_is_scoped_to_its_thread(self, thread):
        """It must not leak: approving a tool in one conversation cannot
        pre-approve it in another."""
        from kazma_core.safety.hitl_grants import grant_tool
        from kazma_core.swarm.safety import _grant_tool

        tool = "mcp__filesystem__get_file_info"
        grant_tool(thread, tool, actor="web:admin")
        assert _grant_tool("a-different-thread", tool) is False

    def test_a_grant_is_scoped_to_its_tool(self, thread):
        from kazma_core.safety.hitl_grants import grant_tool
        from kazma_core.swarm.safety import _grant_tool

        grant_tool(thread, "mcp__filesystem__get_file_info", actor="web:admin")
        assert _grant_tool(thread, "mcp__filesystem__write_file") is False
