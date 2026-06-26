"""Tests for KazmaKG — the core knowledge graph engine."""

from __future__ import annotations

import json

import pytest

from kazma_core.kg_engine import KazmaKG


@pytest.fixture
def kg(tmp_path):
    """In-memory KazmaKG (no persistence)."""
    return KazmaKG()


@pytest.fixture
def kg_persist(tmp_path):
    """KazmaKG backed by a JSON file."""
    p = str(tmp_path / "graph.json")
    return KazmaKG(persist_path=p)


# ------------------------------------------------------------------
# Node tests
# ------------------------------------------------------------------


class TestAddAndGetNode:
    """Create a node and retrieve it."""

    def test_add_and_get_node(self, kg):
        kg.add_node("alice", "person", {"role": "engineer"})
        node = kg.get_node("alice")
        assert node is not None
        assert node["id"] == "alice"
        assert node["type"] == "person"
        assert node["properties"]["role"] == "engineer"
        assert node["created_at"] is not None

    def test_get_node_missing(self, kg):
        assert kg.get_node("nonexistent") is None

    def test_node_count(self, kg):
        kg.add_node("a", "t")
        kg.add_node("b", "t")
        assert kg.node_count == 2


class TestUpdateNodeProperties:
    """Merge properties into an existing node."""

    def test_update_node_properties(self, kg):
        kg.add_node("bob", "person", {"role": "designer"})
        kg.update_node("bob", {"team": "ux", "role": "lead"})
        node = kg.get_node("bob")
        assert node["properties"]["team"] == "ux"
        # Role was overwritten by merge
        assert node["properties"]["role"] == "lead"

    def test_update_node_missing_raises(self, kg):
        with pytest.raises(KeyError, match="not found"):
            kg.update_node("ghost", {"x": 1})


class TestDeleteNodeRemovesEdges:
    """Deleting a node must cascade-remove all connected edges."""

    def test_delete_node_removes_edges(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_node("c", "node")
        kg.add_edge("a", "b", "linked")
        kg.add_edge("b", "c", "linked")

        kg.delete_node("b")
        assert kg.get_node("b") is None
        assert kg.node_count == 2
        # Edges involving b are gone
        edges = kg.get_edges()
        assert all(e["source"] != "b" and e["target"] != "b" for e in edges)
        assert kg.edge_count == 0

    def test_delete_node_missing_raises(self, kg):
        with pytest.raises(KeyError, match="not found"):
            kg.delete_node("ghost")


class TestFindNodes:
    """Search by type and properties."""

    def test_find_nodes_by_type(self, kg):
        kg.add_node("a", "person", {"name": "Alice"})
        kg.add_node("b", "document", {"title": "Spec"})
        kg.add_node("c", "person", {"name": "Bob"})
        people = kg.find_nodes(node_type="person")
        assert len(people) == 2
        assert all(n["type"] == "person" for n in people)

    def test_find_nodes_by_property(self, kg):
        kg.add_node("a", "person", {"role": "engineer"})
        kg.add_node("b", "person", {"role": "designer"})
        kg.add_node("c", "person", {"role": "engineer"})
        engineers = kg.find_nodes(property_filters={"role": "engineer"})
        assert len(engineers) == 2
        assert all(n["properties"]["role"] == "engineer" for n in engineers)

    def test_find_nodes_by_type_and_property(self, kg):
        kg.add_node("x", "concept", {"topic": "ml"})
        kg.add_node("y", "person", {"topic": "ml"})
        kg.add_node("z", "concept", {"topic": "nlp"})
        results = kg.find_nodes(node_type="concept", property_filters={"topic": "ml"})
        assert len(results) == 1
        assert results[0]["id"] == "x"

    def test_find_nodes_empty(self, kg):
        assert kg.find_nodes(node_type="nothing") == []


# ------------------------------------------------------------------
# Edge tests
# ------------------------------------------------------------------


class TestAddAndGetEdge:
    """Create edges and query them."""

    def test_add_and_get_edge(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "related_to", weight=0.8)
        edges = kg.get_edges(source="a", target="b")
        assert len(edges) == 1
        assert edges[0]["relation"] == "related_to"
        assert edges[0]["weight"] == 0.8

    def test_get_edges_filter_relation(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "knows")
        kg.add_edge("a", "b", "likes")
        knows = kg.get_edges(source="a", target="b", relation="knows")
        assert len(knows) == 1
        assert knows[0]["relation"] == "knows"

    def test_edge_count(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "x")
        assert kg.edge_count == 1


class TestEdgeWeightUpdate:
    """Reinforcement-style weight adjustment."""

    def test_edge_weight_update(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "rel", weight=1.0)

        new_w = kg.update_edge_weight("a", "b", 0.5)
        assert new_w == pytest.approx(1.5)

        new_w = kg.update_edge_weight("a", "b", -0.3)
        assert new_w == pytest.approx(1.2)

    def test_update_weight_missing_edge(self, kg):
        kg.add_node("a", "node")
        with pytest.raises(KeyError, match="not found"):
            kg.update_edge_weight("a", "z", 1.0)


class TestDeleteEdge:
    """Edge removal."""

    def test_delete_edge_basic(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "rel")
        kg.delete_edge("a", "b")
        assert kg.edge_count == 0

    def test_delete_edge_by_relation(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "knows")
        kg.add_edge("a", "b", "likes")
        kg.delete_edge("a", "b", relation="likes")
        assert kg.edge_count == 1
        assert kg.get_edges()[0]["relation"] == "knows"

    def test_delete_edge_missing_raises(self, kg):
        kg.add_node("a", "node")
        with pytest.raises(KeyError, match="not found"):
            kg.delete_edge("a", "z")


class TestDeleteEdgeParallel:
    """Targeted delete must only remove the matching edge, not all parallels (BUG gw-068 #2)."""

    def test_delete_by_relation_preserves_other_parallels(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "works_at")
        kg.add_edge("a", "b", "founded")

        kg.delete_edge("a", "b", relation="works_at")
        assert kg.edge_count == 1
        remaining = kg.get_edges(source="a", target="b")
        assert remaining[0]["relation"] == "founded"

    def test_delete_all_without_relation(self, kg):
        """delete_edge(u, v) without relation removes all parallel edges."""
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "r1")
        kg.add_edge("a", "b", "r2")

        kg.delete_edge("a", "b")
        assert kg.edge_count == 0


class TestEdgeWeightUpdateParallel:
    """Targeted weight update must only affect the matching relation (BUG gw-068 #3)."""

    def test_update_specific_relation_weight(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "knows", weight=1.0)
        kg.add_edge("a", "b", "likes", weight=2.0)

        new_w = kg.update_edge_weight("a", "b", 0.5, relation="knows")
        assert new_w == pytest.approx(1.5)

        knows = kg.get_edges(source="a", target="b", relation="knows")
        likes = kg.get_edges(source="a", target="b", relation="likes")
        assert knows[0]["weight"] == pytest.approx(1.5)
        assert likes[0]["weight"] == pytest.approx(2.0)  # untouched

    def test_update_missing_relation_raises(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("a", "b", "knows", weight=1.0)

        with pytest.raises(KeyError, match="relation"):
            kg.update_edge_weight("a", "b", 0.5, relation="nonexistent")


# ------------------------------------------------------------------
# Traversal tests
# ------------------------------------------------------------------


class TestNeighbors:
    """BFS neighbor traversal."""

    def test_neighbors_depth_1(self, kg):
        for n in ("a", "b", "c"):
            kg.add_node(n, "node")
        kg.add_edge("a", "b", "linked")
        kg.add_edge("b", "c", "linked")

        nb = kg.neighbors("a", depth=1)
        ids = {n["id"] for n in nb}
        assert ids == {"b"}

    def test_neighbors_depth_2(self, kg):
        for n in ("a", "b", "c", "d"):
            kg.add_node(n, "node")
        kg.add_edge("a", "b", "linked")
        kg.add_edge("b", "c", "linked")
        kg.add_edge("c", "d", "linked")

        nb = kg.neighbors("a", depth=2)
        ids = {n["id"] for n in nb}
        assert ids == {"b", "c"}

    def test_neighbors_with_relation_filter(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_node("c", "node")
        kg.add_edge("a", "b", "knows")
        kg.add_edge("a", "c", "likes")

        nb = kg.neighbors("a", depth=1, relation="likes")
        ids = {n["id"] for n in nb}
        assert ids == {"c"}

    def test_neighbors_undirected_feel(self, kg):
        """Predecessors are also discovered."""
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_edge("b", "a", "points_to")
        nb = kg.neighbors("a", depth=1)
        ids = {n["id"] for n in nb}
        assert "b" in ids

    def test_neighbors_missing_node(self, kg):
        assert kg.neighbors("ghost") == []


class TestShortestPath:
    """Weighted Dijkstra shortest path."""

    def test_shortest_path(self, kg):
        # a --(1)--> b --(1)--> c     total = 2
        # a --(5)--> c                total = 5
        for n in ("a", "b", "c"):
            kg.add_node(n, "node")
        kg.add_edge("a", "b", "r", weight=1.0)
        kg.add_edge("b", "c", "r", weight=1.0)
        kg.add_edge("a", "c", "r", weight=5.0)

        path = kg.shortest_path("a", "c")
        assert path == ["a", "b", "c"]

    def test_shortest_path_direct(self, kg):
        for n in ("a", "b"):
            kg.add_node(n, "node")
        kg.add_edge("a", "b", "r", weight=1.0)
        assert kg.shortest_path("a", "b") == ["a", "b"]

    def test_shortest_path_no_path(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        with pytest.raises(KeyError, match="No path"):
            kg.shortest_path("a", "b")


# ------------------------------------------------------------------
# Subgraph export
# ------------------------------------------------------------------


class TestSubgraphExport:
    """Export induced subgraph as JSON-serializable dict."""

    def test_subgraph_export(self, kg):
        kg.add_node("a", "node", {"x": 1})
        kg.add_node("b", "node", {"y": 2})
        kg.add_node("c", "node", {"z": 3})
        kg.add_edge("a", "b", "linked")
        kg.add_edge("b", "c", "linked")

        sg = kg.subgraph(["a", "b", "c"])
        assert len(sg["nodes"]) == 3
        assert len(sg["edges"]) == 2

    def test_subgraph_export_excludes_outside(self, kg):
        kg.add_node("a", "node")
        kg.add_node("b", "node")
        kg.add_node("c", "node")
        kg.add_edge("a", "b", "linked")
        kg.add_edge("b", "c", "linked")

        sg = kg.subgraph(["a", "b"])
        assert len(sg["nodes"]) == 2
        assert len(sg["edges"]) == 1
        assert sg["edges"][0]["source"] == "a"

    def test_subgraph_json_roundtrip(self, kg):
        kg.add_node("x", "t", {"k": "v"})
        kg.add_node("y", "t")
        kg.add_edge("x", "y", "rel")
        sg = kg.subgraph(["x", "y"])
        raw = json.dumps(sg)
        parsed = json.loads(raw)
        assert len(parsed["nodes"]) == 2
        assert len(parsed["edges"]) == 1

    def test_subgraph_empty(self, kg):
        sg = kg.subgraph([])
        assert sg == {"nodes": [], "edges": []}


# ------------------------------------------------------------------
# Persistence
# ------------------------------------------------------------------


class TestPersistence:
    """JSON save/load roundtrip."""

    def test_persistence_roundtrip(self, kg_persist):
        kg_persist.add_node("alice", "person", {"role": "engineer"})
        kg_persist.add_node("bob", "person", {"role": "designer"})
        kg_persist.add_edge("alice", "bob", "works_with", weight=0.9)

        kg_persist.save()

        # Reload from same file
        kg2 = KazmaKG(persist_path=kg_persist._persist_path)
        assert kg2.node_count == 2
        assert kg2.edge_count == 1

        node = kg2.get_node("alice")
        assert node["type"] == "person"
        assert node["properties"]["role"] == "engineer"

        edges = kg2.get_edges(source="alice")
        assert edges[0]["weight"] == pytest.approx(0.9)

    def test_save_without_path_raises(self, kg):
        with pytest.raises(ValueError, match="No persistence path"):
            kg.save()

    def test_load_missing_file_is_noop(self, tmp_path):
        p = str(tmp_path / "nonexistent.json")
        kg = KazmaKG(persist_path=p)
        assert kg.node_count == 0  # loaded nothing, no crash


# ------------------------------------------------------------------
# to_dict
# ------------------------------------------------------------------


class TestToDict:
    """Full graph serialization."""

    def test_to_dict_roundtrip(self, kg):
        kg.add_node("a", "t", {"x": 1})
        kg.add_node("b", "t")
        kg.add_edge("a", "b", "rel", weight=2.5)

        d = kg.to_dict()
        assert len(d["nodes"]) == 2
        assert len(d["edges"]) == 1
        assert d["edges"][0]["weight"] == pytest.approx(2.5)

        # Reload
        kg2 = KazmaKG()
        kg2._load_dict(d)
        assert kg2.node_count == 2
        assert kg2.edge_count == 1


# ------------------------------------------------------------------
# Adapter integration
# ------------------------------------------------------------------


class TestKgAdapterUsesEngine:
    """Verify KnowledgeGraphAdapter can delegate to KazmaKG engine."""

    def test_kg_adapter_uses_engine(self):
        """Adapter with engine=KazmaKG() delegates node creation."""
        from kazma_core.memory.kg_adapter import KnowledgeGraphAdapter

        engine = KazmaKG()
        adapter = KnowledgeGraphAdapter(engine=engine)

        adapter.add_entity("alice", "person", {"role": "engineer"})
        # Engine should have the node
        assert engine.has_node("alice")
        node = engine.get_node("alice")
        assert node["type"] == "person"
        assert node["properties"]["role"] == "engineer"

        # Adapter entity count should reflect the engine's state
        assert adapter.entity_count == 1
