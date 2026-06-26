"""Knowledge Graph Memory Adapter — bridges memory system to a KG backend.

Provides structured entity/relation storage with graph traversal,
context window generation, and subgraph export.  Ships with a
NetworkX backend; Neo4j adapter is a future extension point.

Usage:
    kg = KnowledgeGraphAdapter(backend='networkx')
    kg.add_entity('alice', 'person', {'role': 'engineer'})
    kg.add_relation('alice', 'bob', 'collaborates_with')
    ctx = kg.get_context_window('alice', max_tokens=2000)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token estimation helper (rough char/4 heuristic, no external deps)
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# KnowledgeGraphAdapter
# ---------------------------------------------------------------------------

class KnowledgeGraphAdapter:
    """Integration layer for KG-backed memory.

    Args:
        backend:       'networkx' (default, in-memory + optional SQLite)
                       or 'neo4j' (future — raises NotImplementedError).
        persist_path:  Optional path for SQLite persistence.
                       Pass ``None`` to skip persistence.
    """

    def __init__(
        self,
        backend: str = "networkx",
        persist_path: str | None = None,
        engine: Any | None = None,
    ) -> None:
        if backend not in ("networkx", "neo4j"):
            raise ValueError(f"Unknown backend: {backend!r}")
        if backend == "neo4j":
            raise NotImplementedError("Neo4j backend not yet implemented")

        self._backend = backend
        self._persist_path = persist_path

        # Wire to a KazmaKG engine if provided, else create internal graph
        if engine is not None:
            self._engine = engine
            self._graph = engine.graph
        else:
            import networkx as nx
            self._engine = None
            self._graph = nx.MultiDiGraph()

        # Optional SQLite persistence
        self._db: sqlite3.Connection | None = None
        if persist_path:
            db_path = Path(persist_path).expanduser().resolve()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(db_path))
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    properties TEXT,
                    created_at REAL NOT NULL
                )"""
            )
            self._db.execute(
                """CREATE TABLE IF NOT EXISTS relations (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    properties TEXT,
                    created_at REAL NOT NULL
                )"""
            )
            self._db.commit()
            self._load_from_db()

        logger.info(
            "[KG] Initialized (backend=%s, persist=%s, engine=%s)",
            backend,
            persist_path or "memory-only",
            type(self._engine).__name__ if self._engine else "none",
        )

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_from_db(self) -> None:
        """Replay SQLite tables into the in-memory graph."""
        if self._db is None:
            return
        cur = self._db.execute("SELECT id, type, properties, created_at FROM entities")
        for row in cur:
            props = json.loads(row[2]) if row[2] else {}
            self._graph.add_node(
                row[0],
                type=row[1],
                properties=props,
                created_at=row[3],
            )
        cur = self._db.execute(
            "SELECT source, target, relation_type, properties, created_at FROM relations"
        )
        for row in cur:
            props = json.loads(row[3]) if row[3] else {}
            self._graph.add_edge(
                row[0],
                row[1],
                relation=row[2],
                properties=props,
                created_at=row[4],
            )

    def _persist_entity(self, entity_id: str, etype: str, props: dict, ts: float) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO entities (id, type, properties, created_at) VALUES (?, ?, ?, ?)",
            (entity_id, etype, json.dumps(props), ts),
        )
        self._db.commit()

    def _persist_relation(
        self, rel_id: str, source: str, target: str, rtype: str, props: dict, ts: float
    ) -> None:
        if self._db is None:
            return
        self._db.execute(
            "INSERT OR REPLACE INTO relations (id, source, target, relation_type, properties, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (rel_id, source, target, rtype, json.dumps(props), ts),
        )
        self._db.commit()

    # ------------------------------------------------------------------
    # Entity operations
    # ------------------------------------------------------------------

    def add_entity(
        self,
        entity_id: str,
        entity_type: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """Add an entity (node) to the knowledge graph.

        Returns the entity ID.
        """
        props = properties or {}
        ts = time.time()
        self._graph.add_node(
            entity_id,
            type=entity_type,
            properties=props,
            created_at=ts,
        )
        self._persist_entity(entity_id, entity_type, props, ts)
        logger.debug("[KG] Added entity %s (type=%s)", entity_id, entity_type)
        return entity_id

    def add_relation(
        self,
        source: str,
        target: str,
        relation_type: str,
        properties: dict[str, Any] | None = None,
    ) -> str:
        """Add a directed relation (edge) between two entities.

        Returns the relation ID.
        """
        props = properties or {}
        ts = time.time()
        rel_id = str(uuid.uuid4())
        self._graph.add_edge(
            source,
            target,
            relation_id=rel_id,
            relation=relation_type,
            properties=props,
            created_at=ts,
        )
        self._persist_relation(rel_id, source, target, relation_type, props, ts)
        logger.debug("[KG] Added relation %s --%s--> %s", source, relation_type, target)
        return rel_id

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def query_entities(
        self,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search entities by type and/or property filters.

        Supported filter keys:
          - ``type``: exact match on entity type
          - Any other key is matched against ``properties``
        """
        filters = filters or {}
        entity_type = filters.get("type")
        prop_filters = {k: v for k, v in filters.items() if k != "type"}

        results: list[dict[str, Any]] = []
        for nid, data in self._graph.nodes(data=True):
            if entity_type and data.get("type") != entity_type:
                continue
            node_props = data.get("properties", {})
            if prop_filters and not all(node_props.get(k) == v for k, v in prop_filters.items()):
                continue
            results.append({
                "id": nid,
                "type": data.get("type"),
                "properties": node_props,
                "created_at": data.get("created_at"),
            })
        return results

    def query_relations(
        self,
        source: str | None = None,
        target: str | None = None,
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search relations by source, target, and/or relation type."""
        results: list[dict[str, Any]] = []
        for u, v, data in self._graph.edges(data=True):
            if source and u != source:
                continue
            if target and v != target:
                continue
            if relation_type and data.get("relation") != relation_type:
                continue
            results.append({
                "source": u,
                "target": v,
                "relation": data.get("relation"),
                "properties": data.get("properties", {}),
                "created_at": data.get("created_at"),
            })
        return results

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def get_neighbors(
        self,
        entity_id: str,
        depth: int = 1,
    ) -> list[dict[str, Any]]:
        """BFS traversal returning neighbor nodes up to *depth* hops.

        Returns a list of entity dicts (excluding the origin entity).
        """
        if entity_id not in self._graph:
            return []

        import networkx as nx

        visited: set[str] = {entity_id}
        frontier: set[str] = {entity_id}
        results: list[dict[str, Any]] = []

        for _ in range(depth):
            next_frontier: set[str] = set()
            for node in frontier:
                # successors (outgoing) + predecessors (incoming) for undirected feel
                neighbors = set(self._graph.successors(node)) | set(
                    self._graph.predecessors(node)
                )
                for nb in neighbors:
                    if nb not in visited:
                        visited.add(nb)
                        next_frontier.add(nb)
                        data = self._graph.nodes[nb]
                        results.append({
                            "id": nb,
                            "type": data.get("type"),
                            "properties": data.get("properties", {}),
                            "created_at": data.get("created_at"),
                        })
            frontier = next_frontier

        return results

    # ------------------------------------------------------------------
    # Context window
    # ------------------------------------------------------------------

    def get_context_window(
        self,
        entity_id: str,
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        """Build a text context window around an entity.

        Includes the entity itself, its relations, and neighbors —
        truncated to fit within *max_tokens*.
        """
        if entity_id not in self._graph:
            return {"text": "", "token_count": 0, "entities": []}

        parts: list[str] = []
        included_entities: list[str] = []
        token_budget = max_tokens

        # 1. Origin entity
        node_data = self._graph.nodes[entity_id]
        entity_text = (
            f"Entity: {entity_id} (type={node_data.get('type')})\n"
            f"Properties: {json.dumps(node_data.get('properties', {}))}"
        )
        tokens = _estimate_tokens(entity_text)
        if tokens > token_budget:
            return {"text": "", "token_count": 0, "entities": []}
        parts.append(entity_text)
        included_entities.append(entity_id)
        token_budget -= tokens

        # 2. Relations from/to this entity
        for u, v, data in self._graph.edges(data=True):
            if u != entity_id and v != entity_id:
                continue
            rel_text = (
                f"  {u} --[{data.get('relation')}]--> {v}"
            )
            if data.get("properties"):
                rel_text += f"  props={json.dumps(data['properties'])}"
            rel_tokens = _estimate_tokens(rel_text)
            if rel_tokens > token_budget:
                break
            parts.append(rel_text)
            token_budget -= rel_tokens

        # 3. Neighbor context (depth 1)
        neighbors = self.get_neighbors(entity_id, depth=1)
        for nb in neighbors:
            nb_text = (
                f"Neighbor: {nb['id']} (type={nb['type']}) "
                f"props={json.dumps(nb['properties'])}"
            )
            nb_tokens = _estimate_tokens(nb_text)
            if nb_tokens > token_budget:
                break
            parts.append(nb_text)
            included_entities.append(nb["id"])
            token_budget -= nb_tokens

        full_text = "\n".join(parts)
        return {
            "text": full_text,
            "token_count": _estimate_tokens(full_text),
            "entities": included_entities,
        }

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_subgraph(self, entity_ids: list[str]) -> str:
        """Serialize the subgraph induced by *entity_ids* as a JSON string.

        Includes nodes and all edges between them.
        """
        nodes = []
        for eid in entity_ids:
            if eid not in self._graph:
                continue
            data = self._graph.nodes[eid]
            nodes.append({
                "id": eid,
                "type": data.get("type"),
                "properties": data.get("properties", {}),
                "created_at": data.get("created_at"),
            })

        edges = []
        id_set = set(entity_ids)
        for u, v, data in self._graph.edges(data=True):
            if u in id_set and v in id_set:
                edges.append({
                    "source": u,
                    "target": v,
                    "relation": data.get("relation"),
                    "properties": data.get("properties", {}),
                    "created_at": data.get("created_at"),
                })

        return json.dumps({"nodes": nodes, "edges": edges}, indent=2)

    # ------------------------------------------------------------------
    # Memory integration hooks
    # ------------------------------------------------------------------

    def index_memory_fact(
        self,
        fact_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Hook for memory_store — index a memory fact into the KG.

        Creates a ``memory_fact`` entity and, if the metadata contains
        recognized keys (e.g. ``topic``, ``user``), adds typed relations.

        Returns the entity ID.
        """
        meta = metadata or {}
        props = {"text": text, **meta}
        self.add_entity(fact_id, "memory_fact", props)

        # Auto-link to topic entity if present
        topic = meta.get("topic")
        if topic:
            topic_id = f"topic:{topic}"
            if not self._graph.has_node(topic_id):
                self.add_entity(topic_id, "topic", {"name": topic})
            self.add_relation(fact_id, topic_id, "about")

        # Auto-link to user entity if present
        user = meta.get("user")
        if user:
            user_id = f"user:{user}"
            if not self._graph.has_node(user_id):
                self.add_entity(user_id, "user", {"name": user})
            self.add_relation(fact_id, user_id, "mentioned_by")

        logger.debug("[KG] Indexed memory fact %s", fact_id)
        return fact_id

    def search_with_context(
        self,
        entity_id: str,
        max_depth: int = 2,
        max_tokens: int = 2000,
    ) -> dict[str, Any]:
        """Hook for memory_search — use KG traversal for related context.

        Returns the context window plus graph-distance metadata.
        """
        if entity_id not in self._graph:
            return {"text": "", "token_count": 0, "entities": [], "graph_paths": []}

        ctx = self.get_context_window(entity_id, max_tokens=max_tokens)

        # Enrich with graph path info
        neighbors_1 = self.get_neighbors(entity_id, depth=1)
        neighbors_2 = self.get_neighbors(entity_id, depth=2)

        ctx["graph_paths"] = [
            {"entity": nb["id"], "depth": 1} for nb in neighbors_1
        ] + [
            {"entity": nb["id"], "depth": 2}
            for nb in neighbors_2
            if nb["id"] not in {n["id"] for n in neighbors_1}
        ]

        return ctx

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def entity_count(self) -> int:
        """Number of entities (nodes) in the graph."""
        return self._graph.number_of_nodes()

    @property
    def relation_count(self) -> int:
        """Number of relations (edges) in the graph."""
        return self._graph.number_of_edges()
