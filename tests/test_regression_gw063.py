"""Regression tests for gw-063: three critical bugs.

BUG 1: ReAct iteration counter dead — supervisor_node returns iteration unchanged.
BUG 2: KG edge attribute name mismatch between engine and adapter.
BUG 3: KG engine graph property type hint (docstring-only fix, no runtime test needed).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from kazma_core.llm_provider import LLMResponse, ToolCall

# -----------------------------------------------------------------------
# BUG 1: ReAct iteration counter must increment on tool-call path
# -----------------------------------------------------------------------


class TestIterationCounterIncrement:
    """supervisor_node must return iteration+1 when routing to TOOL_WORKER."""

    @pytest.mark.asyncio
    async def test_iteration_increments_on_tool_calls(self):
        """When LLM returns tool_calls, iteration must be incremented."""
        from kazma_core.agent.graph_builder import supervisor_node
        from kazma_core.agent.state import NodeName

        # Mock LLM response with tool calls
        response = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="tc1", name="search", arguments={"q": "test"})],
            finish_reason="tool_calls",
            model="test-model",
            usage={"total_tokens": 100},
            cost_usd=0.001,
        )

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=response)

        cost_breaker = MagicMock()
        cost_breaker.should_halt.return_value = False

        authority = AsyncMock()
        authority.check_and_enforce = AsyncMock(side_effect=lambda s: s)

        tracer = MagicMock()

        state = {
            "iteration": 3,
            "messages": [{"role": "user", "content": "hello"}],
        }

        result = await supervisor_node(
            state,
            llm=llm,
            system_prompt="test",
            tool_definitions=[{"type": "function", "function": {"name": "search"}}],
            tool_executor=None,
            cost_breaker=cost_breaker,
            authority=authority,
            tracer=tracer,
        )

        assert result["next_node"] == NodeName.TOOL_WORKER
        assert result["iteration"] == 4, (
            f"BUG 1 REGRESSION: iteration should be 4 (3+1), got {result['iteration']}"
        )

    @pytest.mark.asyncio
    async def test_iteration_starts_from_zero(self):
        """First tool-call iteration should return 1, not 0."""
        from kazma_core.agent.graph_builder import supervisor_node

        response = LLMResponse(
            content="",
            tool_calls=[ToolCall(id="tc1", name="read", arguments={"path": "/tmp"})],
            finish_reason="tool_calls",
            model="test-model",
            usage={"total_tokens": 50},
            cost_usd=0.0,
        )

        llm = AsyncMock()
        llm.chat = AsyncMock(return_value=response)

        cost_breaker = MagicMock()
        cost_breaker.should_halt.return_value = False

        authority = AsyncMock()
        authority.check_and_enforce = AsyncMock(side_effect=lambda s: s)

        tracer = MagicMock()

        state = {
            "iteration": 0,
            "messages": [{"role": "user", "content": "read file"}],
        }

        result = await supervisor_node(
            state,
            llm=llm,
            system_prompt="test",
            tool_definitions=[],
            tool_executor=None,
            cost_breaker=cost_breaker,
            authority=authority,
            tracer=tracer,
        )

        assert result["iteration"] == 1, (
            f"BUG 1 REGRESSION: first iteration should be 1, got {result['iteration']}"
        )


# -----------------------------------------------------------------------
# BUG 2: KG edge attribute consistency between engine and adapter
# -----------------------------------------------------------------------


class TestEdgeAttributeConsistency:
    """Engine and adapter must use the same 'relation' key on graph edges."""

    def test_engine_and_adapter_share_relation_key(self):
        """Edge added via adapter must be queryable via engine using 'relation'."""
        from kazma_core.kg_engine import KazmaKG
        from kazma_core.memory.kg_adapter import KnowledgeGraphAdapter

        engine = KazmaKG()
        adapter = KnowledgeGraphAdapter(engine=engine)

        adapter.add_entity("a", "node")
        adapter.add_entity("b", "node")
        adapter.add_relation("a", "b", "works_with")

        # Query via engine — uses 'relation' key
        engine_edges = engine.get_edges(source="a", target="b")
        assert len(engine_edges) == 1
        assert engine_edges[0]["relation"] == "works_with", (
            "BUG 2 REGRESSION: engine edge missing 'relation' key"
        )

        # Query via adapter — now also uses 'relation' key
        adapter_edges = adapter.query_relations(source="a", target="b")
        assert len(adapter_edges) == 1
        assert adapter_edges[0]["relation"] == "works_with", (
            "BUG 2 REGRESSION: adapter edge missing 'relation' key"
        )

    def test_adapter_export_uses_relation_key(self):
        """export_subgraph must use 'relation' key, not 'relation_type'."""
        import json

        from kazma_core.memory.kg_adapter import KnowledgeGraphAdapter

        kg = KnowledgeGraphAdapter(backend="networkx")
        kg.add_entity("x", "doc")
        kg.add_entity("y", "doc")
        kg.add_relation("x", "y", "references")

        raw = kg.export_subgraph(["x", "y"])
        data = json.loads(raw)
        assert len(data["edges"]) == 1
        edge = data["edges"][0]
        assert "relation" in edge, (
            f"BUG 2 REGRESSION: export_subgraph edge has keys {list(edge.keys())}, "
            "expected 'relation'"
        )
        assert edge["relation"] == "references"
        assert "relation_type" not in edge

    def test_context_window_uses_relation_key(self):
        """get_context_window text must contain 'relation' attribute, not 'relation_type'."""
        from kazma_core.memory.kg_adapter import KnowledgeGraphAdapter

        kg = KnowledgeGraphAdapter(backend="networkx")
        kg.add_entity("alice", "person")
        kg.add_entity("bob", "person")
        kg.add_relation("alice", "bob", "collaborates_with")

        ctx = kg.get_context_window("alice", max_tokens=2000)
        # The relation label should appear in the context text
        assert "collaborates_with" in ctx["text"]

    def test_adapter_persistence_uses_relation_key(self, tmp_path):
        """Relations persisted to SQLite and reloaded must use 'relation' key on graph."""
        from kazma_core.memory.kg_adapter import KnowledgeGraphAdapter

        db_path = str(tmp_path / "test.db")
        kg = KnowledgeGraphAdapter(backend="networkx", persist_path=db_path)
        kg.add_entity("a", "node")
        kg.add_entity("b", "node")
        kg.add_relation("a", "b", "linked_to")

        # Reload from same DB
        kg2 = KnowledgeGraphAdapter(backend="networkx", persist_path=db_path)
        edges = kg2.query_relations(source="a", target="b")
        assert len(edges) == 1
        assert edges[0]["relation"] == "linked_to", (
            "BUG 2 REGRESSION: reloaded edge uses wrong key"
        )
