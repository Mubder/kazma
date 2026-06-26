"""Knowledge Graph Engine — NetworkX-powered directed graph backend.

Provides a thread-safe, optionally persistent knowledge graph with
node/edge CRUD, BFS traversal, weighted shortest-path, and JSON
serialization.  Designed as the core backend for ``KnowledgeGraphAdapter``.

Usage::

    kg = KazmaKG()
    kg.add_node("alice", "person", {"role": "engineer"})
    kg.add_edge("alice", "bob", "collaborates_with")
    nb = kg.neighbors("alice", depth=2)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

try:
    import networkx as nx
except ImportError as e:
    raise ImportError(
        "networkx is required for the knowledge-graph engine. "
        "Install with: pip install networkx"
    ) from e

logger = logging.getLogger(__name__)


class KazmaKG:
    """Core knowledge graph engine backed by ``networkx.MultiDiGraph``.

    Args:
        persist_path: Optional file path for JSON persistence.
            Pass ``None`` for a purely in-memory graph.
    """

    def __init__(self, persist_path: str | None = None) -> None:
        self._graph = nx.MultiDiGraph()
        self._lock = threading.RLock()
        self._persist_path = persist_path

        # Auto-load from disk if the file exists
        if persist_path:
            p = Path(persist_path).expanduser().resolve()
            if p.exists():
                self.load(str(p))

        logger.info(
            "[KazmaKG] Initialized (persist=%s)",
            persist_path or "memory-only",
        )

    # ------------------------------------------------------------------
    # Node operations
    # ------------------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        node_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Create or overwrite an entity node."""
        with self._lock:
            self._graph.add_node(
                node_id,
                type=node_type,
                properties=properties or {},
                created_at=time.time(),
            )
            logger.debug("[KazmaKG] Added node %s (type=%s)", node_id, node_type)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Retrieve a node with all metadata, or ``None`` if absent."""
        with self._lock:
            if node_id not in self._graph:
                return None
            data = self._graph.nodes[node_id]
            return {
                "id": node_id,
                "type": data.get("type"),
                "properties": dict(data.get("properties", {})),
                "created_at": data.get("created_at"),
            }

    def update_node(self, node_id: str, properties: dict[str, Any]) -> None:
        """Merge *properties* into the existing node's property dict.

        Raises ``KeyError`` if the node does not exist.
        """
        with self._lock:
            if node_id not in self._graph:
                raise KeyError(f"Node {node_id!r} not found")
            existing = self._graph.nodes[node_id].get("properties", {})
            existing.update(properties)
            self._graph.nodes[node_id]["properties"] = existing

    def delete_node(self, node_id: str) -> None:
        """Remove a node and **all** connected edges (cascading delete)."""
        with self._lock:
            if node_id not in self._graph:
                raise KeyError(f"Node {node_id!r} not found")
            self._graph.remove_node(node_id)
            logger.debug("[KazmaKG] Deleted node %s (with edges)", node_id)

    def find_nodes(
        self,
        node_type: str | None = None,
        property_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Search nodes by type and/or property filters.

        Each filter value must match exactly.
        """
        with self._lock:
            results: list[dict[str, Any]] = []
            for nid, data in self._graph.nodes(data=True):
                if node_type and data.get("type") != node_type:
                    continue
                props = data.get("properties", {})
                if property_filters and not all(
                    props.get(k) == v for k, v in property_filters.items()
                ):
                    continue
                results.append(
                    {
                        "id": nid,
                        "type": data.get("type"),
                        "properties": dict(props),
                        "created_at": data.get("created_at"),
                    }
                )
            return results

    # ------------------------------------------------------------------
    # Edge operations
    # ------------------------------------------------------------------

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        weight: float = 1.0,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Create a directed edge with a relation label and optional weight."""
        with self._lock:
            self._graph.add_edge(
                source,
                target,
                relation=relation,
                weight=weight,
                properties=properties or {},
                created_at=time.time(),
            )
            logger.debug(
                "[KazmaKG] Added edge %s --[%s]--> %s (w=%.2f)",
                source,
                relation,
                target,
                weight,
            )

    def get_edges(
        self,
        source: str | None = None,
        target: str | None = None,
        relation: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query edges by source, target, and/or relation label."""
        with self._lock:
            results: list[dict[str, Any]] = []
            for u, v, data in self._graph.edges(data=True):
                if source is not None and u != source:
                    continue
                if target is not None and v != target:
                    continue
                if relation is not None and data.get("relation") != relation:
                    continue
                results.append(
                    {
                        "source": u,
                        "target": v,
                        "relation": data.get("relation"),
                        "weight": data.get("weight", 1.0),
                        "properties": dict(data.get("properties", {})),
                        "created_at": data.get("created_at"),
                    }
                )
            return results

    def update_edge_weight(
        self,
        source: str,
        target: str,
        weight_delta: float,
        relation: str | None = None,
    ) -> float:
        """Adjust the weight of an edge by *weight_delta* (reinforcement).

        Returns the new weight.
        If *relation* is given, only updates edges matching that relation;
        otherwise updates *all* parallel edges between source and target.

        Raises ``KeyError`` if the edge does not exist (or no matching relation).
        """
        with self._lock:
            if not self._graph.has_edge(source, target):
                raise KeyError(f"Edge ({source!r} -> {target!r}) not found")
            # MultiDiGraph: self._graph[u][v] is {key: data, ...}
            edge_data = self._graph[source][target]
            if relation is not None:
                targets = {k: d for k, d in edge_data.items() if d.get("relation") == relation}
                if not targets:
                    raise KeyError(
                        f"Edge ({source!r} -> {target!r}, relation={relation!r}) not found"
                    )
            else:
                targets = edge_data
            first_key = next(iter(targets))
            current = targets[first_key].get("weight", 1.0)
            new_weight = current + weight_delta
            for key in targets:
                self._graph[source][target][key]["weight"] = new_weight
            return new_weight

    def delete_edge(
        self,
        source: str,
        target: str,
        relation: str | None = None,
    ) -> None:
        """Remove an edge.  If *relation* is given, only remove edges
        whose ``relation`` attribute matches.

        Raises ``KeyError`` if no matching edge exists.
        """
        with self._lock:
            if relation is None:
                if not self._graph.has_edge(source, target):
                    raise KeyError(f"Edge ({source!r} -> {target!r}) not found")
                # Collect all keys to remove (remove_edge without key only
                # removes one edge in MultiDiGraph)
                keys = list(self._graph[source][target].keys())
                for key in keys:
                    self._graph.remove_edge(source, target, key)
            else:
                removed = False
                # Collect edges to avoid modifying during iteration
                to_remove = []
                for u, v, key, data in self._graph.edges(data=True, keys=True):
                    if u == source and v == target and data.get("relation") == relation:
                        to_remove.append((u, v, key))
                for u, v, key in to_remove:
                    self._graph.remove_edge(u, v, key)
                    removed = True
                if not removed:
                    raise KeyError(
                        f"Edge ({source!r} -> {target!r}, relation={relation!r}) not found"
                    )
            logger.debug("[KazmaKG] Deleted edge %s -> %s", source, target)

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def neighbors(
        self,
        node_id: str,
        depth: int = 1,
        relation: str | None = None,
    ) -> list[dict[str, Any]]:
        """BFS traversal returning neighbor nodes up to *depth* hops.

        Follows both outgoing and incoming edges.  If *relation* is set,
        only edges matching that relation label are traversed.

        Returns node dicts excluding the origin node.
        """
        with self._lock:
            if node_id not in self._graph:
                return []

            visited: set[str] = {node_id}
            frontier: set[str] = {node_id}
            results: list[dict[str, Any]] = []

            for _ in range(depth):
                next_frontier: set[str] = set()
                for node in frontier:
                    # Outgoing
                    for succ in self._graph.successors(node):
                        if succ in visited:
                            continue
                        if relation:
                            # MultiDiGraph: edges(u,v) is {key: data, ...}
                            edges = self._graph[node][succ]
                            if not any(
                                d.get("relation") == relation for d in edges.values()
                            ):
                                continue
                        visited.add(succ)
                        next_frontier.add(succ)
                        data = self._graph.nodes[succ]
                        results.append(
                            {
                                "id": succ,
                                "type": data.get("type"),
                                "properties": dict(data.get("properties", {})),
                                "created_at": data.get("created_at"),
                            }
                        )
                    # Incoming
                    for pred in self._graph.predecessors(node):
                        if pred in visited:
                            continue
                        if relation:
                            edges = self._graph[pred][node]
                            if not any(
                                d.get("relation") == relation for d in edges.values()
                            ):
                                continue
                        visited.add(pred)
                        next_frontier.add(pred)
                        data = self._graph.nodes[pred]
                        results.append(
                            {
                                "id": pred,
                                "type": data.get("type"),
                                "properties": dict(data.get("properties", {})),
                                "created_at": data.get("created_at"),
                            }
                        )
                frontier = next_frontier

            return results

    def shortest_path(self, source: str, target: str) -> list[str]:
        """Compute the weighted shortest path from *source* to *target*.

        Uses Dijkstra with edge weights.  Returns the list of node IDs
        forming the path (inclusive of both endpoints).

        Raises ``KeyError`` if no path exists.
        """
        with self._lock:
            try:
                path = nx.shortest_path(
                    self._graph, source, target, weight="weight", method="dijkstra"
                )
                return list(path)
            except nx.NetworkXNoPath:
                raise KeyError(
                    f"No path from {source!r} to {target!r}"
                )
            except nx.NodeNotFound as exc:
                raise KeyError(str(exc))

    def subgraph(self, node_ids: list[str], depth: int = 0) -> dict[str, Any]:
        """Export the induced subgraph as a JSON-serializable dict.

        If *depth* > 0, expand each seed node's neighborhood first.

        Returns ``{"nodes": [...], "edges": [...]}``.
        """
        with self._lock:
            id_set: set[str] = set(node_ids)

            if depth > 0:
                frontier = set(node_ids)
                for _ in range(depth):
                    next_frontier: set[str] = set()
                    for nid in frontier:
                        if nid not in self._graph:
                            continue
                        for succ in self._graph.successors(nid):
                            if succ not in id_set:
                                id_set.add(succ)
                                next_frontier.add(succ)
                        for pred in self._graph.predecessors(nid):
                            if pred not in id_set:
                                id_set.add(pred)
                                next_frontier.add(pred)
                    frontier = next_frontier

            nodes = []
            for nid in id_set:
                if nid not in self._graph:
                    continue
                data = self._graph.nodes[nid]
                nodes.append(
                    {
                        "id": nid,
                        "type": data.get("type"),
                        "properties": dict(data.get("properties", {})),
                        "created_at": data.get("created_at"),
                    }
                )

            edges = []
            for u, v, data in self._graph.edges(data=True):
                if u in id_set and v in id_set:
                    edges.append(
                        {
                            "source": u,
                            "target": v,
                            "relation": data.get("relation"),
                            "weight": data.get("weight", 1.0),
                            "properties": dict(data.get("properties", {})),
                            "created_at": data.get("created_at"),
                        }
                    )

            return {"nodes": nodes, "edges": edges}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full graph to a JSON-compatible dict."""
        with self._lock:
            nodes = []
            for nid, data in self._graph.nodes(data=True):
                nodes.append(
                    {
                        "id": nid,
                        "type": data.get("type"),
                        "properties": dict(data.get("properties", {})),
                        "created_at": data.get("created_at"),
                    }
                )
            edges = []
            for u, v, data in self._graph.edges(data=True):
                edges.append(
                    {
                        "source": u,
                        "target": v,
                        "relation": data.get("relation"),
                        "weight": data.get("weight", 1.0),
                        "properties": dict(data.get("properties", {})),
                        "created_at": data.get("created_at"),
                    }
                )
            return {"nodes": nodes, "edges": edges}

    def save(self, path: str | None = None) -> None:
        """Serialize the graph to a JSON file.

        Uses *path* if provided, else falls back to ``self._persist_path``.

        Raises ``ValueError`` if no path is available.
        """
        target = path or self._persist_path
        if not target:
            raise ValueError("No persistence path specified")
        p = Path(target).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = self.to_dict()
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        logger.info("[KazmaKG] Saved graph to %s", p)

    def load(self, path: str | None = None) -> None:
        """Load a graph from a JSON file, replacing the current graph.

        Uses *path* if provided, else falls back to ``self._persist_path``.

        Raises ``ValueError`` if no path is available.
        """
        target = path or self._persist_path
        if not target:
            raise ValueError("No persistence path specified")
        p = Path(target).expanduser().resolve()
        if not p.exists():
            logger.warning("[KazmaKG] Load skipped — file not found: %s", p)
            return
        raw = json.loads(p.read_text())
        self._load_dict(raw)
        logger.info("[KazmaKG] Loaded graph from %s", p)

    def _load_dict(self, data: dict[str, Any]) -> None:
        """Populate the graph from a parsed dict (inverse of ``to_dict``)."""
        with self._lock:
            self._graph.clear()
            for node in data.get("nodes", []):
                self._graph.add_node(
                    node["id"],
                    type=node.get("type"),
                    properties=node.get("properties", {}),
                    created_at=node.get("created_at"),
                )
            for edge in data.get("edges", []):
                self._graph.add_edge(
                    edge["source"],
                    edge["target"],
                    relation=edge.get("relation"),
                    weight=edge.get("weight", 1.0),
                    properties=edge.get("properties", {}),
                    created_at=edge.get("created_at"),
                )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    @property
    def node_count(self) -> int:
        """Number of nodes in the graph."""
        return self._graph.number_of_nodes()

    @property
    def edge_count(self) -> int:
        """Number of edges in the graph."""
        return self._graph.number_of_edges()

    def has_node(self, node_id: str) -> bool:
        """Check whether a node exists."""
        return self._graph.has_node(node_id)

    def has_edge(self, source: str, target: str) -> bool:
        """Check whether a directed edge exists."""
        return self._graph.has_edge(source, target)

    @property
    def graph(self) -> nx.MultiDiGraph:
        """Direct access to the underlying ``networkx.MultiDiGraph``.

        Prefer using the typed methods above for thread safety.
        """
        return self._graph
