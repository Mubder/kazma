"""Layer 2 — NetworkX Knowledge Graph (structural memory).

Tracks entity relationships: code dependencies, worker lineage,
task→output chains, and handoff records.  Persisted as JSON to
``kazma-data/knowledge_graph.json``.

Uses a MultiDiGraph so multiple edge types can exist between the
same two nodes (e.g., ``calls`` and ``imports``).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

__all__ = ["KnowledgeGraph"]

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "kazma-data/knowledge_graph.json"
_FLUSH_DELAY = 2.0  # seconds before auto-flush


class KnowledgeGraph:
    """NetworkX-backed structural memory (Layer 2).

    Persists to JSON with timer-based batching — mutations set a dirty flag
    and a background timer flushes after ``_FLUSH_DELAY`` seconds of
    inactivity.  Falls back to an empty graph if the file is corrupted or
    missing.

    Args:
        path:  Path to the JSON persistence file.
    """

    def __init__(self, path: str = _DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._graph: Any = None
        self._ready: bool = False
        self._dirty: bool = False
        self._flush_timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._load()

    # ── Persistence ─────────────────────────────────────────────────────

    def _graph_class(self) -> Any:
        """Lazy import NetworkX — optional dep."""
        import networkx
        return networkx.MultiDiGraph

    def _load(self) -> None:
        """Load graph from JSON or create empty graph."""
        GClass = self._graph_class()
        if not self._path.exists():
            self._graph = GClass()
            self._ready = True
            self._save()
            logger.info("[KnowledgeGraph] Created empty graph at %s", self._path)
            return
        try:
            import networkx
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._graph = networkx.node_link_graph(raw, multigraph=True)
            self._ready = True
            logger.info("[KnowledgeGraph] Loaded %d nodes, %d edges", len(self._graph.nodes), len(self._graph.edges))
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.warning("[KnowledgeGraph] Load failed: %s — starting empty", exc)
            self._graph = GClass()
            self._ready = True

    def _save(self) -> None:
        """Persist graph to JSON."""
        if self._graph is None:
            return
        import networkx
        data = networkx.node_link_data(self._graph)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _mark_dirty(self) -> None:
        """Mark graph as changed and schedule a delayed flush."""
        with self._lock:
            self._dirty = True
            # Cancel any pending timer
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            # Schedule a new flush
            self._flush_timer = threading.Timer(_FLUSH_DELAY, self._flush)
            self._flush_timer.daemon = True
            self._flush_timer.start()

    def _flush(self) -> None:
        """Flush dirty graph to disk if still dirty."""
        with self._lock:
            if not self._dirty:
                return
            self._dirty = False
            try:
                self._save()
            except Exception as exc:
                logger.warning("[KnowledgeGraph] Flush failed: %s", exc)

    @property
    def available(self) -> bool:
        return self._ready

    # ── CRUD ────────────────────────────────────────────────────────────

    def add_entity(
        self,
        entity_id: str,
        entity_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Add or update a node in the graph."""
        if not self._ready:
            return
        self._graph.add_node(entity_id, type=entity_type, **(properties or {}))
        self._mark_dirty()

    def add_relation(
        self,
        source: str,
        target: str,
        relation_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Add a directed edge between two entities."""
        if not self._ready:
            return
        self._graph.add_edge(source, target, type=relation_type, **(properties or {}))
        self._mark_dirty()

    def query_related(
        self,
        entity_id: str,
        depth: int = 1,
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find entities connected to *entity_id* up to *depth* hops.

        Returns list of entity dicts with keys: id, type, relation, depth.
        """
        if not self._ready or entity_id not in self._graph:
            return []
        results: list[dict[str, Any]] = []
        seen: set[str] = {entity_id}
        frontier = [entity_id]
        for d in range(depth):
            next_frontier: list[str] = []
            for node in frontier:
                for _, neighbor, edge_data in self._graph.out_edges(node, data=True):
                    if neighbor in seen:
                        continue
                    if relation_type and edge_data.get("type") != relation_type:
                        continue
                    seen.add(neighbor)
                    results.append({
                        "id": neighbor,
                        "type": self._graph.nodes[neighbor].get("type", ""),
                        "relation": edge_data.get("type", ""),
                        "depth": d + 1,
                    })
                    next_frontier.append(neighbor)
            frontier = next_frontier
        return results

    def query_by_type(self, entity_type: str) -> list[dict[str, Any]]:
        """Return all entities of a given type."""
        if not self._ready:
            return []
        return [
            {"id": n, "type": entity_type, "properties": dict(self._graph.nodes[n])}
            for n, data in self._graph.nodes(data=True)
            if data.get("type") == entity_type
        ]

    def query_dependencies(self, entity_id: str) -> list[dict[str, Any]]:
        """Return all direct upstream dependencies (incoming edges)."""
        if not self._ready:
            return []
        results: list[dict[str, Any]] = []
        for src, _, edge_data in self._graph.in_edges(entity_id, data=True):
            results.append({
                "id": src,
                "type": self._graph.nodes[src].get("type", ""),
                "relation": edge_data.get("type", ""),
                "properties": dict(self._graph.nodes[src]),
            })
        return results

    def stats(self) -> dict[str, Any]:
        """Return graph statistics."""
        if not self._ready:
            return {"nodes": 0, "edges": 0}
        return {
            "nodes": len(self._graph.nodes),
            "edges": len(self._graph.edges),
            "path": str(self._path),
        }

    def to_json(self) -> dict[str, Any]:
        """Export graph as vis.js-compatible JSON {nodes, edges}."""
        if not self._ready:
            return {"nodes": [], "edges": []}
        nodes = []
        for node_id, data in self._graph.nodes(data=True):
            nodes.append({
                "id": str(node_id),
                "label": str(data.get("label", node_id))[:60],
                "group": str(data.get("type", "unknown")),
                "title": str(data.get("properties", {}))[:200],
            })
        edges = []
        for u, v, data in self._graph.edges(data=True):
            edges.append({
                "from": str(u),
                "to": str(v),
                "label": str(data.get("label", data.get("relationship", "")))[:40],
            })
        return {"nodes": nodes, "edges": edges}

    def clear(self) -> None:
        """Reset the graph to empty."""
        with self._lock:
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            self._dirty = False
        self._graph = self._graph_class()()
        self._save()
        logger.info("[KnowledgeGraph] Cleared")
