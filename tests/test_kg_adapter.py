"""Tests for KnowledgeGraphAdapter — KG-backed memory integration layer."""

from __future__ import annotations

import json

import pytest

from kazma_core.memory.kg_adapter import KnowledgeGraphAdapter


@pytest.fixture
def kg(tmp_path):
    """In-memory KG (no persistence)."""
    return KnowledgeGraphAdapter(backend="networkx")


@pytest.fixture
def kg_persist(tmp_path):
    """KG with SQLite persistence."""
    db_path = str(tmp_path / "kg_test.db")
    return KnowledgeGraphAdapter(backend="networkx", persist_path=db_path)


class TestAddEntity:
    """Test entity creation and node properties."""

    def test_add_entity_creates_node(self, kg):
        eid = kg.add_entity("alice", "person", {"role": "engineer"})
        assert eid == "alice"
        assert kg.entity_count == 1

    def test_add_entity_stores_properties(self, kg):
        kg.add_entity("bob", "person", {"role": "designer", "team": "ux"})
        results = kg.query_entities({"type": "person"})
        assert len(results) == 1
        assert results[0]["properties"]["role"] == "designer"
        assert results[0]["properties"]["team"] == "ux"

    def test_add_entity_persists(self, kg_persist):
        kg_persist.add_entity("node1", "concept", {"weight": 0.9})
        assert kg_persist.entity_count == 1
        # Verify SQLite persistence by creating a new instance over the same file
        db_path = kg_persist._persist_path
        kg2 = KnowledgeGraphAdapter(backend="networkx", persist_path=db_path)
        assert kg2.entity_count == 1
        results = kg2.query_entities({"type": "concept"})
        assert results[0]["properties"]["weight"] == 0.9


class TestAddRelation:
    """Test relation creation and edge properties."""

    def test_add_relation_creates_edge(self, kg):
        kg.add_entity("a", "node")
        kg.add_entity("b", "node")
        rel_id = kg.add_relation("a", "b", "linked_to", {"weight": 0.8})
        assert rel_id is not None
        assert kg.relation_count == 1

    def test_add_relation_stores_properties(self, kg):
        kg.add_entity("x", "doc")
        kg.add_entity("y", "doc")
        kg.add_relation("x", "y", "references", {"since": "2026-01"})
        rels = kg.query_relations(source="x", target="y")
        assert len(rels) == 1
        assert rels[0]["relation"] == "references"
        assert rels[0]["properties"]["since"] == "2026-01"


class TestQueryEntities:
    """Test entity filtering by type and properties."""

    def test_query_entities_by_type(self, kg):
        kg.add_entity("e1", "person", {"name": "Alice"})
        kg.add_entity("e2", "document", {"title": "Spec"})
        kg.add_entity("e3", "person", {"name": "Bob"})

        people = kg.query_entities({"type": "person"})
        assert len(people) == 2
        assert all(e["type"] == "person" for e in people)

    def test_query_entities_by_property(self, kg):
        kg.add_entity("e1", "person", {"role": "engineer"})
        kg.add_entity("e2", "person", {"role": "designer"})
        kg.add_entity("e3", "person", {"role": "engineer"})

        engineers = kg.query_entities({"type": "person", "role": "engineer"})
        assert len(engineers) == 2
        assert all(e["properties"]["role"] == "engineer" for e in engineers)

    def test_query_entities_empty_filters(self, kg):
        kg.add_entity("a", "t1")
        kg.add_entity("b", "t2")
        all_entities = kg.query_entities()
        assert len(all_entities) == 2


class TestGetNeighbors:
    """Test graph traversal at various depths."""

    def test_get_neighbors_depth_1(self, kg):
        # a -- b -- c (a and c are NOT direct neighbors)
        kg.add_entity("a", "node")
        kg.add_entity("b", "node")
        kg.add_entity("c", "node")
        kg.add_relation("a", "b", "linked")
        kg.add_relation("b", "c", "linked")

        neighbors = kg.get_neighbors("a", depth=1)
        ids = {n["id"] for n in neighbors}
        assert ids == {"b"}

    def test_get_neighbors_depth_2(self, kg):
        # a -- b -- c -- d
        for n in ("a", "b", "c", "d"):
            kg.add_entity(n, "node")
        kg.add_relation("a", "b", "linked")
        kg.add_relation("b", "c", "linked")
        kg.add_relation("c", "d", "linked")

        neighbors = kg.get_neighbors("a", depth=2)
        ids = {n["id"] for n in neighbors}
        assert ids == {"b", "c"}

    def test_get_neighbors_missing_entity(self, kg):
        assert kg.get_neighbors("nonexistent") == []

    def test_get_neighbors_undirected_traversal(self, kg):
        """Predecessors are also discovered (graph feels undirected)."""
        kg.add_entity("a", "node")
        kg.add_entity("b", "node")
        kg.add_relation("b", "a", "points_to")

        neighbors = kg.get_neighbors("a", depth=1)
        ids = {n["id"] for n in neighbors}
        assert "b" in ids


class TestContextWindow:
    """Test context window generation with token budget."""

    def test_context_window_within_limit(self, kg):
        kg.add_entity("alice", "person", {"role": "engineer"})
        kg.add_relation("alice", "bob", "collaborates_with")
        kg.add_entity("bob", "person", {"role": "designer"})

        ctx = kg.get_context_window("alice", max_tokens=2000)
        assert ctx["token_count"] > 0
        assert ctx["token_count"] <= 2000
        assert "alice" in ctx["text"]
        assert "alice" in ctx["entities"]

    def test_context_window_respects_token_budget(self, kg):
        """With a very small budget, context is truncated."""
        kg.add_entity("root", "node", {"description": "x" * 500})
        for i in range(20):
            kg.add_entity(f"n{i}", "node", {"description": "y" * 200})
            kg.add_relation("root", f"n{i}", "linked")

        ctx = kg.get_context_window("root", max_tokens=50)
        assert ctx["token_count"] <= 50

    def test_context_window_missing_entity(self, kg):
        ctx = kg.get_context_window("nope", max_tokens=2000)
        assert ctx["text"] == ""
        assert ctx["token_count"] == 0


class TestExportSubgraph:
    """Test subgraph JSON export."""

    def test_export_subgraph_produces_valid_json(self, kg):
        kg.add_entity("a", "node", {"x": 1})
        kg.add_entity("b", "node", {"y": 2})
        kg.add_entity("c", "node", {"z": 3})
        kg.add_relation("a", "b", "linked")
        kg.add_relation("b", "c", "linked")

        raw = kg.export_subgraph(["a", "b", "c"])
        data = json.loads(raw)
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) == 3
        assert len(data["edges"]) == 2

    def test_export_subgraph_excludes_outside_nodes(self, kg):
        kg.add_entity("a", "node")
        kg.add_entity("b", "node")
        kg.add_entity("c", "node")
        kg.add_relation("a", "b", "linked")
        kg.add_relation("b", "c", "linked")

        raw = kg.export_subgraph(["a", "b"])
        data = json.loads(raw)
        assert len(data["nodes"]) == 2
        # Edge b->c should be excluded
        assert len(data["edges"]) == 1
        assert data["edges"][0]["source"] == "a"

    def test_export_subgraph_empty(self, kg):
        raw = kg.export_subgraph([])
        data = json.loads(raw)
        assert data == {"nodes": [], "edges": []}


class TestMemoryIntegration:
    """Test memory_store and memory_search integration hooks."""

    def test_memory_store_integration(self, kg):
        """index_memory_fact creates entity + auto-links topic."""
        fact_id = kg.index_memory_fact(
            "fact-1",
            "User prefers dark mode",
            metadata={"topic": "preferences", "user": "alice"},
        )
        assert fact_id == "fact-1"

        # Fact entity exists
        facts = kg.query_entities({"type": "memory_fact"})
        assert len(facts) == 1
        assert facts[0]["properties"]["text"] == "User prefers dark mode"

        # Auto-created topic entity
        topics = kg.query_entities({"type": "topic"})
        assert len(topics) == 1
        assert topics[0]["id"] == "topic:preferences"

        # Auto-created user entity
        users = kg.query_entities({"type": "user"})
        assert len(users) == 1
        assert users[0]["id"] == "user:alice"

        # Relations: fact --about--> topic, fact --mentioned_by--> user
        rels = kg.query_relations(source="fact-1")
        rel_types = {r["relation"] for r in rels}
        assert "about" in rel_types
        assert "mentioned_by" in rel_types

    def test_memory_search_uses_kg(self, kg):
        """search_with_context traverses the KG for related context."""
        kg.index_memory_fact("f1", "Alice likes Rust", metadata={"topic": "languages"})
        kg.index_memory_fact("f2", "Alice uses Linux", metadata={"topic": "systems"})
        kg.add_relation("f1", "f2", "related_to", {"strength": 0.7})

        ctx = kg.search_with_context("f1", max_depth=2, max_tokens=2000)
        assert ctx["token_count"] > 0
        assert "f1" in ctx["entities"]
        # f2 should appear as a neighbor
        neighbor_ids = {p["entity"] for p in ctx["graph_paths"]}
        assert "f2" in neighbor_ids


class TestParallelEdgePreservation:
    """Parallel edges between the same node pair must not overwrite each other."""

    def test_parallel_relations_preserved(self, kg):
        """A→works_at→B and A→founded→B must coexist (BUG gw-068 #1)."""
        kg.add_entity("alice", "person")
        kg.add_entity("acme", "company")
        kg.add_relation("alice", "acme", "works_at")
        kg.add_relation("alice", "acme", "founded")

        rels = kg.query_relations(source="alice", target="acme")
        rel_types = {r["relation"] for r in rels}
        assert rel_types == {"works_at", "founded"}
        assert kg.relation_count == 2

    def test_parallel_relations_in_export(self, kg):
        """export_subgraph must include all parallel edges."""
        kg.add_entity("a", "node")
        kg.add_entity("b", "node")
        kg.add_relation("a", "b", "r1")
        kg.add_relation("a", "b", "r2")

        raw = kg.export_subgraph(["a", "b"])
        data = json.loads(raw)
        assert len(data["edges"]) == 2


class TestBackendValidation:
    """Test backend parameter validation."""

    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            KnowledgeGraphAdapter(backend="invalid")

    def test_neo4j_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="Neo4j"):
            KnowledgeGraphAdapter(backend="neo4j")
