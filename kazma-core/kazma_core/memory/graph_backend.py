"""Belief graph backend — SQLite default + Neo4j optional (P2-3).

The live Dashboard graph reads SQLite beliefs today. This module adds a
pluggable surface so Neo4j (or another graph DB) can receive dual-written
triples and serve probes without rewriting the canvas immediately.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "GraphBackend",
    "get_graph_backend",
    "graph_capability",
    "upsert_belief_edge",
]


@runtime_checkable
class GraphBackend(Protocol):
    name: str
    write_ready: bool

    @property
    def available(self) -> bool: ...

    def upsert_triple(
        self,
        *,
        subject: str,
        predicate: str,
        obj: str,
        belief_id: str = "",
        tenant_id: str = "default",
        confidence: float = 0.5,
    ) -> bool: ...

    def neighbors(
        self,
        entity: str,
        *,
        tenant_id: str = "default",
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...


class SqliteGraphBackend:
    """Default: graph is the beliefs table in memory_state.db (read via SQL)."""

    name = "sqlite"
    write_ready = True

    def __init__(self, conn: Any | None = None) -> None:
        self._conn = conn

    def _conn_open(self) -> Any:
        if self._conn is not None:
            return self._conn
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        c = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        c.row_factory = sqlite3.Row
        ensure_primary_schema(c)
        return c

    @property
    def available(self) -> bool:
        try:
            c = self._conn_open()
            c.execute("SELECT 1 FROM beliefs LIMIT 1")
            return True
        except Exception:
            return True  # empty table still "available"

    def upsert_triple(
        self,
        *,
        subject: str,
        predicate: str,
        obj: str,
        belief_id: str = "",
        tenant_id: str = "default",
        confidence: float = 0.5,
    ) -> bool:
        # Beliefs are written by mutate_belief / dual_write — no-op here.
        del subject, predicate, obj, belief_id, tenant_id, confidence
        return True

    def neighbors(
        self,
        entity: str,
        *,
        tenant_id: str = "default",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        try:
            c = self._conn_open()
            rows = c.execute(
                """
                SELECT id, subject, predicate, object, confidence
                FROM beliefs
                WHERE tenant_id = ?
                  AND valid_until IS NULL AND invalidated_at IS NULL
                  AND (LOWER(subject) = LOWER(?) OR LOWER(object) = LOWER(?))
                LIMIT ?
                """,
                (tenant_id, entity, entity, max(1, min(limit, 100))),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


class Neo4jGraphBackend:
    """Optional Neo4j dual-write via official driver (``neo4j`` package).

    Bolt URL example: ``bolt://localhost:7687``. Username/password via
    ``memory.backends.graph.user`` / ``.password`` (or api_key as password).
    """

    name = "neo4j"
    write_ready = True

    def __init__(
        self,
        *,
        url: str,
        user: str = "neo4j",
        password: str = "",
        database: str = "neo4j",
    ) -> None:
        self._url = url or ""
        self._user = user or "neo4j"
        self._password = password or ""
        self._database = database or "neo4j"
        self._driver: Any = None
        self._ready: bool | None = None

    def _get_driver(self) -> Any | None:
        if self._driver is not None:
            return self._driver
        if not self._url:
            return None
        try:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(
                self._url, auth=(self._user, self._password)
            )
            return self._driver
        except Exception:
            logger.debug("[graph_backend] neo4j driver unavailable", exc_info=True)
            return None

    @property
    def available(self) -> bool:
        if self._ready is not None:
            return self._ready
        drv = self._get_driver()
        if drv is None:
            self._ready = False
            return False
        try:
            drv.verify_connectivity()
            self._ready = True
            return True
        except Exception:
            self._ready = False
            return False

    def upsert_triple(
        self,
        *,
        subject: str,
        predicate: str,
        obj: str,
        belief_id: str = "",
        tenant_id: str = "default",
        confidence: float = 0.5,
    ) -> bool:
        drv = self._get_driver()
        if drv is None or not subject or not obj:
            return False
        pred = (predicate or "related_to").replace(" ", "_")
        # Sanitize relationship type for Cypher
        rel = "".join(c if c.isalnum() or c == "_" else "_" for c in pred).upper() or "RELATED"
        try:
            with drv.session(database=self._database) as session:
                session.run(
                    f"""
                    MERGE (a:Entity {{name: $sub, tenant_id: $tid}})
                    MERGE (b:Entity {{name: $obj, tenant_id: $tid}})
                    MERGE (a)-[r:{rel}]->(b)
                    SET r.belief_id = $bid, r.confidence = $conf, r.predicate = $pred
                    """,
                    sub=subject,
                    obj=obj,
                    tid=tenant_id,
                    bid=belief_id or "",
                    conf=float(confidence or 0.5),
                    pred=predicate or "",
                )
            return True
        except Exception:
            logger.debug("[graph_backend] neo4j upsert failed", exc_info=True)
            return False

    def neighbors(
        self,
        entity: str,
        *,
        tenant_id: str = "default",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        drv = self._get_driver()
        if drv is None:
            return []
        try:
            with drv.session(database=self._database) as session:
                result = session.run(
                    """
                    MATCH (a:Entity {name: $name, tenant_id: $tid})-[r]-(b:Entity)
                    RETURN a.name AS subject, type(r) AS predicate, b.name AS object,
                           r.confidence AS confidence, r.belief_id AS id
                    LIMIT $lim
                    """,
                    name=entity,
                    tid=tenant_id,
                    lim=max(1, min(limit, 100)),
                )
                return [dict(rec) for rec in result]
        except Exception:
            return []


def graph_capability(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        from kazma_core.memory.backends import get_backends_cfg

        c = cfg or get_backends_cfg()
    except Exception:
        c = cfg or {}
    g = c.get("graph") or {}
    provider = str(g.get("provider") or "sqlite").lower()
    url = str(g.get("url") or "").strip()
    if provider in ("sqlite", "local", ""):
        return {
            "provider": "sqlite",
            "write_ready": True,
            "status": "local",
            "detail": "Belief graph served from SQLite beliefs table",
        }
    if provider == "neo4j" and url:
        return {
            "provider": "neo4j",
            "write_ready": True,
            "status": "dual_write",
            "detail": "Neo4j dual-write when driver + credentials available",
        }
    if provider == "neo4j":
        return {
            "provider": "neo4j",
            "write_ready": False,
            "status": "needs_url",
            "detail": "Set memory.backends.graph.url (bolt://…)",
        }
    return {
        "provider": provider,
        "write_ready": False,
        "status": "unknown",
        "detail": f"Unknown graph provider {provider!r}",
    }


def get_graph_backend(conn: Any | None = None) -> Any:
    try:
        from kazma_core.memory.backends import get_backends_cfg

        c = get_backends_cfg()
    except Exception:
        return SqliteGraphBackend(conn)
    g = c.get("graph") or {}
    provider = str(g.get("provider") or "sqlite").lower()
    url = str(g.get("url") or "").strip()
    if provider == "neo4j" and url:
        user = str(g.get("user") or g.get("username") or "neo4j")
        password = str(g.get("password") or g.get("api_key") or "")
        neo = Neo4jGraphBackend(url=url, user=user, password=password)
        if neo.available:
            return neo
        logger.info("[graph_backend] neo4j unavailable — falling back to sqlite")
    return SqliteGraphBackend(conn)


def upsert_belief_edge(
    *,
    subject: str,
    predicate: str,
    obj: str,
    belief_id: str = "",
    tenant_id: str = "default",
    confidence: float = 0.5,
) -> bool:
    """Best-effort dual-write of a belief triple to the active graph backend."""
    try:
        return bool(
            get_graph_backend().upsert_triple(
                subject=subject,
                predicate=predicate,
                obj=obj,
                belief_id=belief_id,
                tenant_id=tenant_id,
                confidence=confidence,
            )
        )
    except Exception:
        return False
