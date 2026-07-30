"""Layer 2 — SQLite property graph (structural + relational memory).

A real on-disk property graph (nodes + directed edges + FTS), not an
in-memory NetworkX toy.  Survives restarts, supports multi-hop traversal,
text search, and tenant isolation.

Legacy ``kazma-data/knowledge_graph.json`` (NetworkX node-link) is imported
once on first open, then renamed to ``.migrated``.

Public API stays compatible with the old NetworkX wrapper so swarm callers
(``add_entity`` / ``add_relation`` / ``query_related`` / ``query_by_type``)
keep working. New entry points:

- :meth:`search` — FTS over labels/content
- :meth:`upsert_triple` — subject–predicate–object fact
- :meth:`get_node` / :meth:`neighbors`
- :func:`get_knowledge_graph` — process singleton (shared with adapter + API)
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

__all__ = ["KnowledgeGraph", "get_knowledge_graph", "reset_knowledge_graph", "set_knowledge_graph"]

logger = logging.getLogger(__name__)

def _resolve_default_db() -> str:
    """Resolve the default KG db path via the centralized paths module.

    Falls back to the legacy hard-coded relative path only if the import
    fails (e.g. during early bootstrap), preserving prior behavior.
    """
    try:
        from kazma_core.paths import knowledge_graph_db

        return knowledge_graph_db()
    except Exception:
        return "kazma-data/knowledge_graph.db"


def _resolve_legacy_json() -> str:
    """Resolve the legacy NetworkX node-link JSON path (sibling of the db)."""
    try:
        from pathlib import Path

        from kazma_core.paths import knowledge_graph_db

        return str(Path(knowledge_graph_db()).with_suffix(".json"))
    except Exception:
        return "kazma-data/knowledge_graph.json"


_DEFAULT_DB = _resolve_default_db()
_LEGACY_JSON = _resolve_legacy_json()
_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _slug(text: str, *, prefix: str = "e") -> str:
    raw = (text or "").strip().lower()
    s = _SLUG_RE.sub("_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"
    return s[:80]


class KnowledgeGraph:
    """SQLite-backed property graph (Layer 2).

    Args:
        path: Path to the SQLite database file (or legacy JSON path for
            backward-compatible construction — ``.json`` is remapped to ``.db``).
    """

    def __init__(self, path: str | None = None) -> None:
        raw = path or _DEFAULT_DB
        p = Path(raw)
        # Old callers passed knowledge_graph.json — use sibling .db
        if p.suffix.lower() == ".json":
            p = p.with_suffix(".db")
        self._path = p
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        apply_sqlite_pragmas(self._conn)
        self._ready = False
        with self._lock:
            self._init_schema()
            self._migrate_legacy_json()
            self._ready = True
        stats = self.stats()
        logger.info(
            "[KnowledgeGraph] SQLite ready path=%s nodes=%s edges=%s",
            self._path,
            stats.get("nodes", 0),
            stats.get("edges", 0),
        )

    # ── Schema ──────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS kg_nodes (
                id          TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                label       TEXT NOT NULL DEFAULT '',
                content     TEXT NOT NULL DEFAULT '',
                properties  TEXT NOT NULL DEFAULT '{}',
                tenant_id   TEXT,
                updated_at  REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_kg_nodes_type ON kg_nodes(entity_type);
            CREATE INDEX IF NOT EXISTS idx_kg_nodes_label ON kg_nodes(label);
            CREATE INDEX IF NOT EXISTS idx_kg_nodes_tenant ON kg_nodes(tenant_id);

            CREATE TABLE IF NOT EXISTS kg_edges (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id     TEXT NOT NULL,
                target_id     TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                properties    TEXT NOT NULL DEFAULT '{}',
                tenant_id     TEXT,
                created_at    REAL NOT NULL,
                UNIQUE(source_id, target_id, relation_type)
            );
            CREATE INDEX IF NOT EXISTS idx_kg_edges_src ON kg_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_kg_edges_tgt ON kg_edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_kg_edges_rel ON kg_edges(relation_type);

            CREATE VIRTUAL TABLE IF NOT EXISTS kg_nodes_fts USING fts5(
                node_id UNINDEXED,
                label,
                content,
                entity_type,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        self._conn.commit()

    def _migrate_legacy_json(self) -> None:
        """Import NetworkX node-link JSON once if present."""
        legacy = Path(_LEGACY_JSON)
        # Also check path sibling if custom
        candidates = [legacy]
        json_sibling = self._path.with_suffix(".json")
        if json_sibling != legacy:
            candidates.append(json_sibling)
        for cand in candidates:
            if not cand.exists():
                continue
            try:
                raw = json.loads(cand.read_text(encoding="utf-8"))
                nodes = raw.get("nodes") or []
                links = raw.get("links") or raw.get("edges") or []
                n_in = 0
                e_in = 0
                for node in nodes:
                    if not isinstance(node, dict):
                        continue
                    nid = str(node.get("id") or "")
                    if not nid:
                        continue
                    # NetworkX node-link stores attrs on the node dict
                    ntype = str(node.get("type") or node.get("entity_type") or "entity")
                    props = {
                        k: v
                        for k, v in node.items()
                        if k not in ("id", "type", "entity_type")
                    }
                    self._upsert_node(
                        nid,
                        ntype,
                        label=str(props.get("label") or props.get("content") or nid)[:120],
                        content=str(props.get("content") or "")[:2000],
                        properties=props,
                        tenant_id=props.get("tenant_id"),
                    )
                    n_in += 1
                for link in links:
                    if not isinstance(link, dict):
                        continue
                    src = str(link.get("source") or link.get("from") or "")
                    tgt = str(link.get("target") or link.get("to") or "")
                    rel = str(
                        link.get("type")
                        or link.get("relation")
                        or link.get("label")
                        or "related"
                    )
                    if not src or not tgt:
                        continue
                    props = {
                        k: v
                        for k, v in link.items()
                        if k
                        not in (
                            "source",
                            "target",
                            "from",
                            "to",
                            "type",
                            "relation",
                            "label",
                            "key",
                        )
                    }
                    self._upsert_edge(src, tgt, rel, properties=props)
                    e_in += 1
                self._conn.commit()
                migrated = cand.with_suffix(cand.suffix + ".migrated")
                try:
                    cand.rename(migrated)
                except OSError:
                    pass
                logger.info(
                    "[KnowledgeGraph] Migrated legacy JSON %s → nodes=%d edges=%d",
                    cand,
                    n_in,
                    e_in,
                )
            except Exception:
                logger.warning(
                    "[KnowledgeGraph] Legacy JSON migrate failed for %s",
                    cand,
                    exc_info=True,
                )

    @property
    def available(self) -> bool:
        return self._ready

    # ── Internal upserts ────────────────────────────────────────────────

    def _upsert_node(
        self,
        entity_id: str,
        entity_type: str,
        *,
        label: str = "",
        content: str = "",
        properties: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> None:
        props = dict(properties or {})
        if not label:
            label = str(props.get("label") or props.get("name") or entity_id)[:120]
        if not content:
            content = str(props.get("content") or "")[:2000]
        # Keep content/label out of properties blob when already columns
        props.pop("content", None)
        # keep label in props for vis.js if useful
        now = time.time()
        tid = tenant_id or props.get("tenant_id")
        meta_json = json.dumps(props, ensure_ascii=False, default=str)
        self._conn.execute(
            """
            INSERT INTO kg_nodes (id, entity_type, label, content, properties, tenant_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                entity_type = excluded.entity_type,
                label = CASE
                    WHEN excluded.label != '' AND excluded.label != excluded.id
                    THEN excluded.label ELSE kg_nodes.label END,
                content = CASE
                    WHEN length(excluded.content) > 0 THEN excluded.content
                    ELSE kg_nodes.content END,
                properties = excluded.properties,
                tenant_id = COALESCE(excluded.tenant_id, kg_nodes.tenant_id),
                updated_at = excluded.updated_at
            """,
            (entity_id, entity_type, label, content, meta_json, tid, now),
        )
        # FTS refresh
        self._conn.execute("DELETE FROM kg_nodes_fts WHERE node_id = ?", (entity_id,))
        self._conn.execute(
            """
            INSERT INTO kg_nodes_fts (node_id, label, content, entity_type)
            VALUES (?, ?, ?, ?)
            """,
            (entity_id, label, content, entity_type),
        )

    def _upsert_edge(
        self,
        source: str,
        target: str,
        relation_type: str,
        *,
        properties: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> None:
        # Ensure endpoints exist (stub nodes if missing)
        for nid, etype in ((source, "entity"), (target, "entity")):
            row = self._conn.execute(
                "SELECT 1 FROM kg_nodes WHERE id = ?", (nid,)
            ).fetchone()
            if not row:
                self._upsert_node(nid, etype, label=nid)
        props = dict(properties or {})
        tid = tenant_id or props.get("tenant_id")
        now = time.time()
        self._conn.execute(
            """
            INSERT INTO kg_edges
                (source_id, target_id, relation_type, properties, tenant_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, target_id, relation_type) DO UPDATE SET
                properties = excluded.properties,
                tenant_id = COALESCE(excluded.tenant_id, kg_edges.tenant_id)
            """,
            (
                source,
                target,
                relation_type,
                json.dumps(props, ensure_ascii=False, default=str),
                tid,
                now,
            ),
        )

    # ── Public CRUD (compat + new) ──────────────────────────────────────

    def add_entity(
        self,
        entity_id: str,
        entity_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Add or update a node in the graph."""
        if not self._ready or not entity_id:
            return
        props = dict(properties or {})
        with self._lock:
            self._upsert_node(
                str(entity_id),
                str(entity_type or "entity"),
                label=str(props.get("label") or props.get("name") or entity_id)[:120],
                content=str(props.get("content") or "")[:2000],
                properties=props,
                tenant_id=props.get("tenant_id"),
            )
            self._conn.commit()

    def add_relation(
        self,
        source: str,
        target: str,
        relation_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Add a directed edge between two entities."""
        if not self._ready or not source or not target:
            return
        with self._lock:
            self._upsert_edge(
                str(source),
                str(target),
                str(relation_type or "related"),
                properties=properties,
            )
            self._conn.commit()

    def upsert_triple(
        self,
        subject: str,
        predicate: str,
        obj: str,
        *,
        subject_type: str = "entity",
        object_type: str = "entity",
        fact: str = "",
        tenant_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Insert a subject–predicate–object fact; returns node ids.

        Creates/updates subject and object nodes and a directed edge
        ``subject -predicate→ object``. Optional ``fact`` text is stored
        on the subject (and a memory_chunk child) for FTS recall.
        """
        sub_id = _slug(subject, prefix="s")
        obj_id = _slug(obj, prefix="o")
        pred = _slug(predicate, prefix="rel") or "related"
        meta = dict(extra or {})
        if tenant_id:
            meta["tenant_id"] = tenant_id
        with self._lock:
            self._upsert_node(
                sub_id,
                subject_type,
                label=subject[:120],
                content=(fact or subject)[:2000],
                properties={**meta, "name": subject},
                tenant_id=tenant_id,
            )
            self._upsert_node(
                obj_id,
                object_type,
                label=obj[:120],
                content=obj[:2000],
                properties={**meta, "name": obj},
                tenant_id=tenant_id,
            )
            self._upsert_edge(
                sub_id,
                obj_id,
                pred,
                properties={**meta, "predicate": predicate, "fact": fact[:500]},
                tenant_id=tenant_id,
            )
            if fact:
                chunk_id = _slug(fact[:48], prefix="fact")
                self._upsert_node(
                    chunk_id,
                    "memory_chunk",
                    label=fact[:80],
                    content=fact[:2000],
                    properties={**meta, "subject": subject, "predicate": predicate, "object": obj},
                    tenant_id=tenant_id,
                )
                self._upsert_edge(sub_id, chunk_id, "has_fact", properties=meta, tenant_id=tenant_id)
            self._conn.commit()
        return {"subject_id": sub_id, "object_id": obj_id, "predicate": pred}

    def get_node(self, entity_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM kg_nodes WHERE id = ?", (entity_id,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_entity(row)

    def _row_to_entity(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            props = json.loads(row["properties"] or "{}")
        except Exception:
            props = {}
        if not isinstance(props, dict):
            props = {}
        content = row["content"] or ""
        if content and "content" not in props:
            props["content"] = content
        if row["label"] and "label" not in props:
            props["label"] = row["label"]
        if row["tenant_id"]:
            props.setdefault("tenant_id", row["tenant_id"])
        return {
            "id": row["id"],
            "type": row["entity_type"],
            "properties": props,
            "label": row["label"] or row["id"],
            "content": content,
        }

    def query_related(
        self,
        entity_id: str,
        depth: int = 1,
        relation_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """BFS outward from *entity_id* up to *depth* hops."""
        if not self._ready or not entity_id:
            return []
        depth = max(1, min(int(depth), 5))
        results: list[dict[str, Any]] = []
        seen: set[str] = {entity_id}
        frontier = [entity_id]
        with self._lock:
            # Resolve by id OR label slug match
            start = self._conn.execute(
                "SELECT id FROM kg_nodes WHERE id = ? OR label = ? COLLATE NOCASE LIMIT 1",
                (entity_id, entity_id),
            ).fetchone()
            if start:
                frontier = [start["id"]]
                seen = {start["id"]}
            elif entity_id not in seen:
                # try slug
                sid = _slug(entity_id)
                start = self._conn.execute(
                    "SELECT id FROM kg_nodes WHERE id = ?", (sid,)
                ).fetchone()
                if start:
                    frontier = [start["id"]]
                    seen = {start["id"]}
                else:
                    return []

            for d in range(depth):
                next_frontier: list[str] = []
                for node in frontier:
                    sql = (
                        "SELECT e.target_id, e.relation_type, e.properties, "
                        "n.entity_type, n.label, n.content, n.properties AS nprops, n.tenant_id "
                        "FROM kg_edges e JOIN kg_nodes n ON n.id = e.target_id "
                        "WHERE e.source_id = ?"
                    )
                    params: list[Any] = [node]
                    if relation_type:
                        sql += " AND e.relation_type = ?"
                        params.append(relation_type)
                    for erow in self._conn.execute(sql, params).fetchall():
                        nid = erow["target_id"]
                        if nid in seen:
                            continue
                        seen.add(nid)
                        try:
                            nprops = json.loads(erow["nprops"] or "{}")
                        except Exception:
                            nprops = {}
                        if not isinstance(nprops, dict):
                            nprops = {}
                        content = erow["content"] or ""
                        if content:
                            nprops.setdefault("content", content)
                        results.append(
                            {
                                "id": nid,
                                "type": erow["entity_type"],
                                "relation": erow["relation_type"],
                                "depth": d + 1,
                                "properties": nprops,
                                "label": erow["label"] or nid,
                                "content": content,
                            }
                        )
                        next_frontier.append(nid)
                frontier = next_frontier
                if not frontier:
                    break
        return results

    def query_by_type(self, entity_type: str) -> list[dict[str, Any]]:
        """Return all entities of a given type."""
        if not self._ready or not entity_type:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM kg_nodes WHERE entity_type = ? COLLATE NOCASE LIMIT 500",
                (entity_type,),
            ).fetchall()
        return [self._row_to_entity(r) for r in rows]

    def query_dependencies(self, entity_id: str) -> list[dict[str, Any]]:
        """Return direct upstream dependencies (incoming edges)."""
        if not self._ready or not entity_id:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT e.source_id, e.relation_type, n.*
                FROM kg_edges e
                JOIN kg_nodes n ON n.id = e.source_id
                WHERE e.target_id = ?
                """,
                (entity_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            ent = self._row_to_entity(r)
            ent["relation"] = r["relation_type"]
            out.append(ent)
        return out

    def search(
        self,
        query: str,
        limit: int = 10,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search over node labels and content (FTS5)."""
        if not self._ready:
            return []
        q = (query or "").strip()
        if not q:
            return []
        safe = q.replace('"', '""')
        match = f'"{safe}"'
        with self._lock:
            try:
                rows = self._conn.execute(
                    """
                    SELECT n.*, bm25(kg_nodes_fts) AS rank
                    FROM kg_nodes_fts
                    JOIN kg_nodes n ON n.id = kg_nodes_fts.node_id
                    WHERE kg_nodes_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (match, limit * 2),
                ).fetchall()
            except sqlite3.OperationalError:
                # Fallback LIKE
                like = f"%{q}%"
                rows = self._conn.execute(
                    """
                    SELECT *, 0.0 AS rank FROM kg_nodes
                    WHERE label LIKE ? OR content LIKE ?
                    LIMIT ?
                    """,
                    (like, like, limit * 2),
                ).fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            if tenant_id and r["tenant_id"] not in (None, tenant_id):
                continue
            ent = self._row_to_entity(r)
            # bm25 is negative; convert to positive-ish score
            rank = float(r["rank"] or 0.0)
            ent["score"] = -rank if rank < 0 else (1.0 / (1.0 + abs(rank)))
            results.append(ent)
            if len(results) >= limit:
                break
        return results

    def stats(self) -> dict[str, Any]:
        if not self._ready:
            return {"nodes": 0, "edges": 0, "path": str(self._path), "backend": "sqlite"}
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM kg_nodes").fetchone()[0]
            e = self._conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]
        return {
            "nodes": int(n),
            "edges": int(e),
            "path": str(self._path),
            "backend": "sqlite",
        }

    def to_json(self) -> dict[str, Any]:
        """Export graph as vis.js-compatible JSON {nodes, edges}."""
        if not self._ready:
            return {"nodes": [], "edges": []}
        with self._lock:
            nodes_rows = self._conn.execute(
                "SELECT id, entity_type, label, content, properties FROM kg_nodes LIMIT 2000"
            ).fetchall()
            edge_rows = self._conn.execute(
                "SELECT source_id, target_id, relation_type FROM kg_edges LIMIT 5000"
            ).fetchall()
        nodes = []
        for r in nodes_rows:
            nodes.append(
                {
                    "id": r["id"],
                    "label": (r["label"] or r["id"])[:60],
                    "group": r["entity_type"] or "unknown",
                    "title": (r["content"] or r["properties"] or "")[:200],
                }
            )
        edges = []
        for r in edge_rows:
            edges.append(
                {
                    "from": r["source_id"],
                    "to": r["target_id"],
                    "label": (r["relation_type"] or "")[:40],
                }
            )
        return {"nodes": nodes, "edges": edges}

    def clear(self) -> None:
        """Reset the graph to empty."""
        with self._lock:
            self._conn.execute("DELETE FROM kg_edges")
            self._conn.execute("DELETE FROM kg_nodes")
            try:
                self._conn.execute("DELETE FROM kg_nodes_fts")
            except Exception:
                pass
            self._conn.commit()
        logger.info("[KnowledgeGraph] Cleared")

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
            self._ready = False


# ── Singleton ───────────────────────────────────────────────────────────

_kg: KnowledgeGraph | None = None
_kg_lock = threading.Lock()


def get_knowledge_graph(path: str | None = None) -> KnowledgeGraph:
    """Process-wide KnowledgeGraph singleton."""
    global _kg
    with _kg_lock:
        if _kg is None:
            _kg = KnowledgeGraph(path=path)
        return _kg


def set_knowledge_graph(graph: KnowledgeGraph | None) -> None:
    """Replace singleton (tests)."""
    global _kg
    with _kg_lock:
        _kg = graph


def reset_knowledge_graph() -> None:
    """Close and drop singleton."""
    global _kg
    with _kg_lock:
        if _kg is not None:
            try:
                _kg.close()
            except Exception:
                pass
        _kg = None
