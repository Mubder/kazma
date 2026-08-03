"""Belief graph backend — SQLite default + Neo4j optional dual-write (P2-3).

Dashboard V2 Belief Topology paints from SQLite (entity types, bi-temporal).
Neo4j receives dual-written triples for scale / neighbors and optional
``?source=neo4j`` probes. Beliefs remain SoT in SQLite.
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
    "delete_belief_edge",
    "test_neo4j_connection",
    "sync_beliefs_to_neo4j",
    "reset_graph_backend_cache",
]

# Process-level cache so available/unavailable re-probes after Save / Test
_cached_backend: Any | None = None
_cached_key: str = ""


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

    def delete_triple(
        self,
        *,
        subject: str = "",
        predicate: str = "",
        obj: str = "",
        belief_id: str = "",
        tenant_id: str = "default",
    ) -> bool:
        """Remove a dual-written edge (by belief_id preferred, else S-P-O)."""
        drv = self._get_driver()
        if drv is None:
            return False
        try:
            with drv.session(database=self._database) as session:
                if belief_id:
                    session.run(
                        """
                        MATCH ()-[r]->()
                        WHERE r.belief_id = $bid
                        DELETE r
                        """,
                        bid=belief_id,
                    )
                    return True
                if subject and obj:
                    pred = (predicate or "related_to").replace(" ", "_")
                    rel = (
                        "".join(c if c.isalnum() or c == "_" else "_" for c in pred).upper()
                        or "RELATED"
                    )
                    session.run(
                        f"""
                        MATCH (a:Entity {{name: $sub, tenant_id: $tid}})
                              -[r:{rel}]->
                              (b:Entity {{name: $obj, tenant_id: $tid}})
                        DELETE r
                        """,
                        sub=subject,
                        obj=obj,
                        tid=tenant_id,
                    )
                    return True
            return False
        except Exception:
            logger.debug("[graph_backend] neo4j delete failed", exc_info=True)
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

    def export_topology(
        self,
        *,
        tenant_id: str = "default",
        limit: int = 200,
    ) -> dict[str, Any]:
        """Export nodes/links for Dashboard when Neo4j is primary graph."""
        drv = self._get_driver()
        if drv is None:
            return {"nodes": [], "links": [], "stats": {"source": "neo4j", "empty": True}}
        try:
            with drv.session(database=self._database) as session:
                result = session.run(
                    """
                    MATCH (a:Entity {tenant_id: $tid})-[r]->(b:Entity {tenant_id: $tid})
                    RETURN a.name AS subject, type(r) AS predicate, b.name AS object,
                           coalesce(r.confidence, 0.5) AS confidence,
                           coalesce(r.belief_id, '') AS id,
                           coalesce(r.predicate, type(r)) AS pred_label
                    LIMIT $lim
                    """,
                    tid=tenant_id,
                    lim=max(10, min(int(limit), 2000)),
                )
                rows = [dict(rec) for rec in result]
            # Optional SQLite entity-type enrich so probe path matches canvas schema
            ent_types: dict[str, str] = {}
            try:
                import sqlite3 as _sq

                from kazma_core.paths import primary_memory_db

                c = _sq.connect(primary_memory_db(), check_same_thread=False)
                for er in c.execute("SELECT id, type FROM entities").fetchall():
                    ent_types[str(er[0])] = str(er[1] or "concept")
                c.close()
            except Exception:
                pass
            # Belief id → predicate_type for edge colors
            bid_ptype: dict[str, str] = {}
            try:
                import sqlite3 as _sq2

                from kazma_core.paths import primary_memory_db as _pdb

                c2 = _sq2.connect(_pdb(), check_same_thread=False)
                for br in c2.execute(
                    "SELECT id, predicate_type FROM beliefs WHERE invalidated_at IS NULL"
                ).fetchall():
                    bid_ptype[str(br[0])] = str(br[1] or "set")
                c2.close()
            except Exception:
                pass
            nodes: dict[str, dict] = {}
            links = []
            belief_count: dict[str, int] = {}
            for r in rows:
                sub = r.get("subject") or ""
                obj = r.get("object") or ""
                if sub:
                    belief_count[sub] = belief_count.get(sub, 0) + 1
                for ent, is_obj in ((sub, False), (obj, True)):
                    if not ent or ent in nodes:
                        continue
                    etype = ent_types.get(ent) or (
                        "person" if ent == "user" else ("concept" if is_obj else "concept")
                    )
                    long_obj = is_obj and len(str(ent)) > 80
                    nodes[ent] = {
                        "id": ent,
                        "name": ent,
                        "label": ent,
                        "type": etype,
                        "beliefCount": 0,
                        "isHighStakes": False,
                        "isVirtual": bool(long_obj),
                        "group": "neo4j",
                    }
                pred = r.get("pred_label") or r.get("predicate") or "related_to"
                bid = str(r.get("id") or "")
                links.append(
                    {
                        "id": bid,
                        "source": sub,
                        "target": obj,
                        "label": pred,
                        "predicate": pred,
                        "object_text": obj,
                        "type": bid_ptype.get(bid) or "set",
                        "confidence": float(r.get("confidence") or 0.5),
                        "superseded": False,
                    }
                )
            for n in nodes.values():
                n["beliefCount"] = belief_count.get(n["id"], 0) or (1 if n.get("isVirtual") else 0)
            return {
                "nodes": list(nodes.values()),
                "links": links,
                "stats": {
                    "source": "neo4j",
                    "node_count": len(nodes),
                    "link_count": len(links),
                    "primary": False,
                    "paint_source": "neo4j",
                },
            }
        except Exception:
            logger.debug("[graph_backend] neo4j export_topology failed", exc_info=True)
            return {"nodes": [], "links": [], "stats": {"source": "neo4j", "error": True}}


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
            "status": "primary_when_available",
            "detail": (
                "Neo4j is primary for Dashboard topology when online; "
                "beliefs remain bi-temporal in SQLite; dual-write on mutate"
            ),
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


def reset_graph_backend_cache() -> None:
    """Clear cached backend so next get_graph_backend re-reads config."""
    global _cached_backend, _cached_key
    _cached_backend = None
    _cached_key = ""


def get_graph_backend(conn: Any | None = None) -> Any:
    global _cached_backend, _cached_key
    try:
        from kazma_core.memory.backends import get_backends_cfg

        c = get_backends_cfg()
    except Exception:
        return SqliteGraphBackend(conn)
    g = c.get("graph") or {}
    provider = str(g.get("provider") or "sqlite").lower()
    url = str(g.get("url") or "").strip()
    user = str(g.get("user") or g.get("username") or "neo4j")
    password = str(g.get("password") or g.get("api_key") or "")
    cache_key = f"{provider}|{url}|{user}|{bool(password)}"
    if _cached_backend is not None and _cached_key == cache_key and conn is None:
        return _cached_backend

    if provider == "neo4j" and url:
        neo = Neo4jGraphBackend(url=url, user=user, password=password)
        if neo.available:
            if conn is None:
                _cached_backend = neo
                _cached_key = cache_key
            return neo
        logger.warning(
            "[graph_backend] neo4j unavailable (url=%s) — falling back to sqlite",
            url,
        )
    be = SqliteGraphBackend(conn)
    if conn is None:
        _cached_backend = be
        _cached_key = cache_key
    return be


def test_neo4j_connection(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe Neo4j connectivity for Settings UI. Never raises."""
    import time as _time

    t0 = _time.perf_counter()
    try:
        from kazma_core.memory.backends import get_backends_cfg

        g = (cfg or get_backends_cfg()).get("graph") or {}
    except Exception:
        g = cfg or {}
    url = str(g.get("url") or "").strip()
    user = str(g.get("user") or "neo4j")
    password = str(g.get("password") or g.get("api_key") or "")
    if not url:
        return {
            "ok": False,
            "error": "Set Graph store to Neo4j and enter bolt URL, then Save backends",
            "latency_ms": 0,
        }
    try:
        from neo4j import GraphDatabase  # type: ignore
    except ImportError:
        return {
            "ok": False,
            "error": "Python package missing: pip install neo4j  (in the same venv as Kazma)",
            "latency_ms": 0,
            "hint": "Server Neo4j ≠ Python driver. Install both.",
        }
    try:
        drv = GraphDatabase.driver(url, auth=(user, password))
        try:
            drv.verify_connectivity()
            # Optional light query
            with drv.session() as session:
                rec = session.run("RETURN 1 AS n").single()
                _ = rec["n"] if rec else None
        finally:
            drv.close()
        ms = (_time.perf_counter() - t0) * 1000
        reset_graph_backend_cache()
        return {
            "ok": True,
            "latency_ms": round(ms, 1),
            "url": url,
            "user": user,
            "detail": "Connected. Click Sync beliefs → Neo4j, then refresh Dashboard graph.",
        }
    except Exception as exc:
        ms = (_time.perf_counter() - t0) * 1000
        err = str(exc)[:400]
        hint = ""
        low = err.lower()
        if "unauthorized" in low or "authentication" in low:
            hint = "Check username/password (Neo4j default user is neo4j)."
        elif "refused" in low or "failed to establish" in low:
            hint = (
                "Cannot reach bolt port. If Neo4j runs in WSL and Kazma on Windows, "
                "use the WSL IP or ensure port 7687 is published. From WSL: hostname -I"
            )
        elif "module" in low:
            hint = "pip install neo4j in Kazma venv"
        return {
            "ok": False,
            "error": err,
            "latency_ms": round(ms, 1),
            "hint": hint,
        }


def sync_beliefs_to_neo4j(
    *,
    tenant_id: str = "default",
    limit: int = 500,
) -> dict[str, Any]:
    """One-shot: push active SQLite beliefs into Neo4j (backfill dual-write)."""
    import sqlite3

    reset_graph_backend_cache()
    be = get_graph_backend()
    if getattr(be, "name", "") != "neo4j" or not getattr(be, "available", False):
        return {
            "ok": False,
            "synced": 0,
            "error": "Neo4j backend not available — Test connection first; provider must be neo4j",
        }
    try:
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        ensure_primary_schema(conn)
        rows = conn.execute(
            """
            SELECT id, subject, predicate, object, confidence, tenant_id
            FROM beliefs
            WHERE valid_until IS NULL AND invalidated_at IS NULL
              AND tenant_id = ?
            ORDER BY structural_importance DESC, confidence DESC
            LIMIT ?
            """,
            (tenant_id, max(1, min(int(limit), 5000))),
        ).fetchall()
        conn.close()
    except Exception as exc:
        return {"ok": False, "synced": 0, "error": f"read sqlite failed: {exc}"[:300]}

    ok_n = 0
    fail_n = 0
    for r in rows:
        if be.upsert_triple(
            subject=r["subject"] or "",
            predicate=r["predicate"] or "related",
            obj=r["object"] or "",
            belief_id=r["id"] or "",
            tenant_id=r["tenant_id"] or tenant_id,
            confidence=float(r["confidence"] or 0.5),
        ):
            ok_n += 1
        else:
            fail_n += 1
    return {
        "ok": ok_n > 0 or (ok_n == 0 and fail_n == 0),
        "synced": ok_n,
        "failed": fail_n,
        "total_read": len(rows),
        "detail": (
            f"Synced {ok_n}/{len(rows)} active beliefs to Neo4j. "
            "Refresh Dashboard → V2 Belief Topology."
        ),
    }


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


def delete_belief_edge(
    *,
    belief_id: str = "",
    subject: str = "",
    predicate: str = "",
    obj: str = "",
    tenant_id: str = "default",
) -> bool:
    """Best-effort remove a dual-written edge after soft-invalidate."""
    try:
        be = get_graph_backend()
        if getattr(be, "name", "") != "neo4j":
            return False
        if not getattr(be, "available", False):
            return False
        delete = getattr(be, "delete_triple", None)
        if not callable(delete):
            return False
        return bool(
            delete(
                subject=subject,
                predicate=predicate,
                obj=obj,
                belief_id=belief_id,
                tenant_id=tenant_id,
            )
        )
    except Exception:
        return False
