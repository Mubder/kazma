"""Belief graph backend — SQLite default + Neo4j optional dual-write (P2-3).

Dashboard V2 Belief Topology paints from SQLite (entity types, bi-temporal).
Neo4j receives dual-written triples for scale / neighbors and optional
``?source=neo4j`` probes. Beliefs remain SoT in SQLite.

The dual-write is best effort; ``memory/graph_sync.py`` keeps the copy whole
(the maintenance sweep "memory graph sync", 2026-11-13). Every edge Kazma
writes carries the fact's id (``r.belief_id``) and the writing install's id
(``r.install``); an edge without a fact id, or with another install's tag, is
never changed or removed by this install (two installs can share one Neo4j:
the dev checkout and the live install both default to ``bolt://127.0.0.1:7687``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import threading
import time
from collections.abc import Iterator
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_driver_missing_lock = threading.Lock()
_driver_missing_warned = False

#: How long a call waits for a Neo4j connection. The driver's own default is
#: 30 s, and every dual-write after a commit would wait that long on a Neo4j
#: that does not answer (a host that drops packets, rather than refusing).
_CONNECT_TIMEOUT_S = 5.0


class _GraphError(RuntimeError):
    """A Neo4j statement that did not take effect. The graph sync brings the
    copy up to date later; the message names the error type, never a value."""


class GraphUnavailable(_GraphError):
    """Neo4j does not answer, or refuses the sign-in: an outage, waited out."""


class GraphWriteRefused(_GraphError):
    """Neo4j answered and refused this statement (its rows are tried one at a time)."""


def _neo4j_driver_installed() -> bool:
    """Whether the ``neo4j`` Python driver is installed (the ``neo4j`` extra)."""
    return importlib.util.find_spec("neo4j") is not None


def _make_driver(url: str, auth: tuple[str, str]) -> Any:
    """The official driver for *url* (raises ImportError without the extra)."""
    from neo4j import GraphDatabase

    return GraphDatabase.driver(
        url, auth=auth,
        connection_timeout=_CONNECT_TIMEOUT_S,
        connection_acquisition_timeout=2 * _CONNECT_TIMEOUT_S,
    )


def _graph_errors() -> tuple[type[BaseException], ...]:
    """What a Neo4j call raises when the call did not take effect: the
    driver's and the server's errors, the network's, and our own."""
    errors: list[type[BaseException]] = [_GraphError, OSError]
    try:
        from neo4j.exceptions import DriverError, Neo4jError
    except ImportError:
        return tuple(errors)
    return (*errors, DriverError, Neo4jError)


def _as_graph_error(exc: BaseException) -> _GraphError:
    """*exc* as an outage (waited out) or a refusal (this statement's rows)."""
    if isinstance(exc, _GraphError):
        return exc
    if isinstance(exc, OSError):
        return GraphUnavailable(type(exc).__name__)
    try:
        from neo4j.exceptions import AuthError, DriverError, TransientError
    except ImportError:
        return GraphWriteRefused(type(exc).__name__)
    if isinstance(exc, AuthError):
        return GraphUnavailable("the sign-in was refused")
    if isinstance(exc, (DriverError, TransientError)):
        return GraphUnavailable(type(exc).__name__)
    return GraphWriteRefused(f"{type(exc).__name__} {getattr(exc, 'code', '') or ''}".strip())


def this_install() -> str | None:
    """This install's id, tagged on every edge it writes (``r.install``).

    Read, never minted, as the Postgres mirror's tag (``<data dir>/install_id``,
    written at boot): a script that never booted a server writes untagged
    edges, which the sync of the install whose facts they are takes as its own.
    """
    from kazma_core.db.shared_store_peers import install_id

    return install_id(create=False)


def _warn_driver_missing() -> None:
    """Say once per process that Neo4j is chosen and its driver is not there.

    Without the driver every dual-write failed at DEBUG and the graph stayed
    in SQLite with nothing above it to say why: the driver was in no extra,
    so a fresh install set to Neo4j never had it (2026-10-28).
    """
    global _driver_missing_warned
    with _driver_missing_lock:
        if _driver_missing_warned:
            return
        _driver_missing_warned = True
    from kazma_core.install_hint import extra_install_hint

    logger.warning(
        "[graph_backend] Neo4j is the memory graph store, but the neo4j driver is not "
        "installed in Kazma's Python: no fact reaches Neo4j, and the graph is served "
        "from SQLite until it is. Install it (%s), then restart Kazma.",
        extra_install_hint("neo4j"),
    )


def report_graph_driver() -> bool:
    """Boot check: True unless Neo4j is chosen and its driver is missing (warned once)."""
    try:
        from kazma_core.memory.backends import get_backends_cfg

        g = get_backends_cfg().get("graph") or {}
    except Exception:
        logger.debug("[graph_backend] backends config unreadable at the driver check", exc_info=True)
        return True
    if str(g.get("provider") or "").lower() != "neo4j" or _neo4j_driver_installed():
        return True
    _warn_driver_missing()
    return False

__all__ = [
    "GraphBackend",
    "GraphUnavailable",
    "GraphWriteRefused",
    "edge_row",
    "get_graph_backend",
    "graph_capability",
    "neo4j_target",
    "rel_name",
    "report_graph_driver",
    "upsert_belief_edge",
    "delete_belief_edge",
    "clear_tenant_edges",
    "test_neo4j_connection",
    "reset_graph_backend_cache",
    "this_install",
]

# Process-level cache so available/unavailable re-probes after Save / Test
_cached_backend: Any | None = None
_cached_key: str = ""
#: When a cached SQLite stand-in for an unreachable Neo4j expires (monotonic
#: seconds; 0 = never). It was cached for the life of the process: a Neo4j
#: that started after Kazma (a reboot, the container a minute behind) got no
#: dual-write until the next restart or Settings save.
_cached_until: float = 0.0
_FALLBACK_RETRY_S = 60.0
_fallback_said = False
#: The one Neo4j backend (and driver) of this process: (settings key, backend).
_shared: tuple[str, Neo4jGraphBackend] | None = None
_shared_lock = threading.Lock()


def rel_name(predicate: str) -> str:
    """The Cypher relationship type of *predicate*, as ``type(r)`` returns it.

    Letters, digits and ``_`` are kept, everything else becomes ``_``.
    """
    pred = (predicate or "related_to").replace(" ", "_")
    return "".join(c if c.isalnum() or c == "_" else "_" for c in pred).upper() or "RELATED"


def _rel_type(predicate: str) -> str:
    """The Cypher relationship type of *predicate*, backtick-quoted.

    The type is quoted because an unquoted Cypher name must not start with a
    digit: ``2fa_enabled`` made ``-[r:2FA_ENABLED]->``, a syntax error the
    driver raised, logged at DEBUG, and the fact's edge was never written
    (audit 2026-10-26 AUD-049). No backtick survives the sanitizing, so the
    quoting cannot be broken out of.
    """
    return f"`{rel_name(predicate)}`"


def edge_row(subject: str, predicate: str, obj: str, belief_id: str, confidence: Any) -> dict[str, Any]:
    """One fact's edge as the dual-write and the graph sync write it."""
    try:
        conf = float(confidence or 0.5)
    except (TypeError, ValueError):
        conf = 0.5
    return {"sub": subject, "obj": obj, "pred": predicate or "", "bid": belief_id or "", "conf": conf}


# Every statement the graph sync sends starts with a ``// kazma:<name>`` line:
# a Cypher comment, so the statement is self-describing in Neo4j's query log
# and the tests' stand-in server can tell them apart.

#: Write fact edges of ONE relationship type (``%s``, quoted). A new edge
#: gets the fact's properties; an existing one only when it is Kazma's
#: (``belief_id`` set) and this install's or untagged -- an edge Kazma did not
#: write, or another install's, keeps its properties.
_UPSERT_EDGES = """// kazma:upsert_edges
UNWIND $rows AS row
MERGE (a:Entity {name: row.sub, tenant_id: $tid})
MERGE (b:Entity {name: row.obj, tenant_id: $tid})
MERGE (a)-[r:%s]->(b)
ON CREATE SET r.belief_id = row.bid, r.confidence = row.conf, r.predicate = row.pred, r.install = $inst
WITH r, row
WHERE coalesce(r.belief_id, '') <> '' AND (r.install IS NULL OR r.install = $inst)
SET r.belief_id = row.bid, r.confidence = row.conf, r.predicate = row.pred, r.install = $inst"""

#: Remove edges by element id, each only while it still carries the fact id
#: it was planned for (a dual-write may have given it to a newer fact since),
#: then each end left with no edge (a forgotten value is a node, AUD-063).
_DELETE_EDGES = """// kazma:delete_edges
UNWIND $rows AS row
MATCH (a:Entity)-[r]->(b:Entity)
WHERE elementId(r) = row.eid AND r.belief_id = row.bid
  AND a.tenant_id = $tid AND b.tenant_id = $tid
DELETE r
WITH a, b
UNWIND [a, b] AS n
WITH DISTINCT n
WHERE NOT (n)--()
DELETE n"""

_EDGE_FIELDS = """elementId(r) AS eid, coalesce(r.belief_id, '') AS bid, a.name AS sub, type(r) AS rel,
       b.name AS obj, r.confidence AS conf, r.predicate AS pred, r.install AS install"""

_TENANT_EDGES = f"""// kazma:tenant_edges
MATCH (a:Entity {{tenant_id: $tid}})-[r]->(b:Entity {{tenant_id: $tid}})
RETURN {_EDGE_FIELDS}"""

_EDGES_FOR_BELIEFS = f"""// kazma:edges_for_beliefs
MATCH (a:Entity {{tenant_id: $tid}})-[r]->(b:Entity {{tenant_id: $tid}})
WHERE r.belief_id IN $bids
RETURN {_EDGE_FIELDS}"""

_EDGES_AT_KEYS = f"""// kazma:edges_at_keys
UNWIND $keys AS k
MATCH (a:Entity {{name: k.sub, tenant_id: $tid}})-[r]->(b:Entity {{name: k.obj, tenant_id: $tid}})
WHERE type(r) = k.rel
RETURN {_EDGE_FIELDS}"""

_EDGE_TENANTS = """// kazma:edge_tenants
MATCH (a:Entity)-[r]->(b:Entity)
WHERE coalesce(r.belief_id, '') <> '' AND a.tenant_id IS NOT NULL AND a.tenant_id = b.tenant_id
RETURN DISTINCT a.tenant_id AS tid"""

_ISOLATED_NODES = """// kazma:isolated_nodes
MATCH (n:Entity {tenant_id: $tid})
WHERE NOT (n)--()
WITH n LIMIT $lim
DELETE n
RETURN count(*) AS removed"""


#: Appended after ``DELETE r`` where ``a``/``b`` are the edge's ends: an end
#: left with no relationship goes too. A forgotten fact's value is a node
#: (``Entity.name`` = the value), and deleting the edge alone kept a codeword
#: in Neo4j after Forget (audit 2026-10-26 AUD-063). An end another fact
#: still uses keeps its edges and stays; ``upsert_triple`` MERGEs it back.
_DELETE_ORPHANED_ENDS = """
                        WITH a, b
                        UNWIND [a, b] AS n
                        WITH DISTINCT n
                        WHERE NOT (n)--()
                        DELETE n"""


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
            self._driver = _make_driver(self._url, (self._user, self._password))
            return self._driver
        except ImportError:
            _warn_driver_missing()
            return None
        except Exception:
            logger.debug("[graph_backend] neo4j driver unavailable", exc_info=True)
            return None

    @property
    def available(self) -> bool:
        if self._ready is not None:
            return self._ready
        return self.reachable()[0]

    def reachable(self) -> tuple[bool, str]:
        """Ask Neo4j now (never a cached answer): (answers, why not)."""
        drv = self._get_driver()
        if drv is None:
            self._ready = False
            return False, ("driver_missing" if not _neo4j_driver_installed() else "no driver")
        try:
            drv.verify_connectivity()
        except _graph_errors() as exc:
            self._ready = False
            return False, str(_as_graph_error(exc)) or "unreachable"
        self._ready = True
        return True, ""

    def close(self) -> None:
        """Close the driver (a backend replaced by new settings)."""
        drv, self._driver = self._driver, None
        if drv is None:
            return
        try:
            drv.close()
        except _graph_errors():
            logger.debug("[graph_backend] closing the replaced neo4j driver failed", exc_info=True)

    # ── the statements the graph sync uses (each raises an outage or a refusal) ──

    def _run(self, query: str, *, fetch: bool = False, **params: Any) -> list[dict[str, Any]]:
        """Send one statement in a transaction of its own; its rows when *fetch*."""
        drv = self._get_driver()
        if drv is None:
            raise GraphUnavailable("no neo4j driver")
        try:
            with drv.session(database=self._database) as session:
                result = session.run(query, **params)
                if fetch:
                    return [dict(rec) for rec in result]
                result.consume()
                return []
        except _graph_errors() as exc:
            raise _as_graph_error(exc) from exc

    def iter_tenant_edges(self, tenant_id: str) -> Iterator[dict[str, Any]]:
        """Every edge between the tenant's Entity nodes, streamed (Kazma's or not)."""
        drv = self._get_driver()
        if drv is None:
            raise GraphUnavailable("no neo4j driver")
        try:
            with drv.session(database=self._database) as session:
                for rec in session.run(_TENANT_EDGES, tid=tenant_id):
                    yield dict(rec)
        except _graph_errors() as exc:
            raise _as_graph_error(exc) from exc

    def edges_for_beliefs(self, tenant_id: str, belief_ids: list[str]) -> list[dict[str, Any]]:
        """The tenant's edges carrying one of *belief_ids*."""
        return self._run(_EDGES_FOR_BELIEFS, fetch=True, tid=tenant_id, bids=list(belief_ids))

    def edges_at_keys(self, tenant_id: str, keys: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
        """Every edge at each (subject, relationship type, value), Kazma's or not."""
        params = [{"sub": s, "rel": r, "obj": o} for s, r, o in keys]
        return self._run(_EDGES_AT_KEYS, fetch=True, tid=tenant_id, keys=params)

    def edge_tenants(self) -> list[str]:
        """The tenants holding at least one fact edge."""
        return [str(r["tid"]) for r in self._run(_EDGE_TENANTS, fetch=True) if r.get("tid")]

    def upsert_edges(self, tenant_id: str, rows: list[dict[str, Any]], *, install: str | None) -> int:
        """Write fact edges (:func:`edge_row`), one statement per relationship type."""
        by_type: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if row.get("sub") and row.get("obj"):
                by_type.setdefault(_rel_type(str(row.get("pred") or "")), []).append(row)
        for rel, group in by_type.items():
            self._run(_UPSERT_EDGES % rel, tid=tenant_id, rows=group, inst=install)
        return sum(len(g) for g in by_type.values())

    def delete_edges(self, tenant_id: str, rows: list[dict[str, str]]) -> int:
        """Remove edges ``{"eid", "bid"}`` and the ends they leave with no edge."""
        if rows:
            self._run(_DELETE_EDGES, tid=tenant_id, rows=list(rows))
        return len(rows)

    def delete_isolated_nodes(self, tenant_id: str, limit: int) -> int:
        """Remove up to *limit* of the tenant's Entity nodes that have no edge."""
        out = self._run(_ISOLATED_NODES, fetch=True, tid=tenant_id, lim=max(1, int(limit)))
        return int((out[0] if out else {}).get("removed") or 0)

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
        if not subject or not obj:
            return False
        try:
            self.upsert_edges(tenant_id, [edge_row(subject, predicate, obj, belief_id, confidence)],
                              install=this_install())
            return True
        except _GraphError:
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
                        f"""
                        MATCH (a)-[r]->(b)
                        WHERE r.belief_id = $bid
                        DELETE r
                        {_DELETE_ORPHANED_ENDS}
                        """,
                        bid=belief_id,
                    )
                    return True
                if subject and obj:
                    rel = _rel_type(predicate)
                    session.run(
                        f"""
                        MATCH (a:Entity {{name: $sub, tenant_id: $tid}})
                              -[r:{rel}]->
                              (b:Entity {{name: $obj, tenant_id: $tid}})
                        DELETE r
                        {_DELETE_ORPHANED_ENDS}
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

    def clear_tenant_edges(self, *, tenant_id: str = "default") -> int:
        """Delete this install's fact edges for a tenant (graph-clear).

        Kazma's (a ``belief_id``) and this install's or untagged: an edge
        Kazma did not write, or another install sharing the Neo4j wrote, is
        not this install's to clear (2026-11-13; it deleted every edge).
        """
        drv = self._get_driver()
        if drv is None:
            return 0
        try:
            with drv.session(database=self._database) as session:
                result = session.run(
                    """
                    MATCH (a:Entity {tenant_id: $tid})-[r]->(:Entity {tenant_id: $tid})
                    WHERE coalesce(r.belief_id, '') <> '' AND (r.install IS NULL OR r.install = $inst)
                    DELETE r
                    """,
                    tid=tenant_id,
                    inst=this_install(),
                )
                summary = result.consume()
                counters = getattr(summary, "counters", None)
                deleted = int(getattr(counters, "relationships_deleted", 0) or 0)
                # The nodes go too: each holds a subject or value as its name
                # (audit 2026-10-26 AUD-063).
                session.run(
                    """
                    MATCH (n:Entity {tenant_id: $tid})
                    WHERE NOT (n)--()
                    DELETE n
                    """,
                    tid=tenant_id,
                )
                return deleted
        except Exception:
            logger.debug("[graph_backend] neo4j tenant-clear failed", exc_info=True)
            return 0

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
                for er in c.execute(
                    "SELECT id, type FROM entities WHERE tenant_id = ?", (tenant_id,)
                ).fetchall():
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
    if provider == "neo4j" and url and not _neo4j_driver_installed():
        return {
            "provider": "neo4j",
            "write_ready": False,
            "status": "driver_missing",
            "detail": (
                "The neo4j Python driver is not installed: the graph is served from "
                'SQLite. Install the neo4j extra (uv pip install -e ".[neo4j]") and restart.'
            ),
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
    global _cached_backend, _cached_key, _cached_until, _shared, _fallback_said
    _cached_backend = None
    _cached_key = ""
    _cached_until = 0.0
    _fallback_said = False
    with _shared_lock:
        old, _shared = _shared, None
    if old is not None:
        old[1].close()


def _graph_settings(cfg: dict[str, Any]) -> tuple[str, str, str, str]:
    """(provider, url, user, password) of the graph store in *cfg*."""
    g = cfg.get("graph") or {}
    return (
        str(g.get("provider") or "sqlite").lower(),
        str(g.get("url") or "").strip(),
        str(g.get("user") or g.get("username") or "neo4j"),
        str(g.get("password") or g.get("api_key") or ""),
    )


def _settings_key(provider: str, url: str, user: str, password: str) -> str:
    # A digest of the password, so a changed password is a new backend (the
    # truthiness of it, the key until 2026-11-13, kept the old driver).
    secret = hashlib.sha256(password.encode("utf-8")).hexdigest()[:16] if password else ""
    return f"{provider}|{url}|{user}|{secret}"


def _shared_neo4j(url: str, user: str, password: str) -> Neo4jGraphBackend:
    """The one Neo4j backend of this process for these settings: the
    dual-write and the graph sync share its driver (and connection pool)."""
    global _shared
    key = _settings_key("neo4j", url, user, password)
    with _shared_lock:
        if _shared is not None and _shared[0] == key:
            return _shared[1]
        old = _shared
        be = Neo4jGraphBackend(url=url, user=user, password=password)
        _shared = (key, be)
    if old is not None:
        old[1].close()
    return be


def neo4j_target() -> tuple[Neo4jGraphBackend | None, str, str]:
    """(backend, state, target) of the graph store as configured now.

    ``state`` is ``ok`` (Neo4j chosen, its driver installed: the backend is
    returned, not yet asked), ``not_configured``, ``needs_url`` or
    ``driver_missing``. ``target`` names the Neo4j (a digest of its address
    and user): another one is a new copy to compare in full.
    """
    from kazma_core.memory.backends import get_backends_cfg

    provider, url, user, password = _graph_settings(get_backends_cfg())
    if provider != "neo4j":
        return None, "not_configured", ""
    if not url:
        return None, "needs_url", ""
    target = hashlib.sha256(f"{url}|{user}".encode()).hexdigest()[:16]
    if not _neo4j_driver_installed():
        return None, "driver_missing", target
    return _shared_neo4j(url, user, password), "ok", target


def _say_fallback(down: bool, url: str) -> None:
    """Say once per outage that the dual-write waits for Neo4j (the graph
    sync's own line names the outage at WARNING), and once when it is back."""
    global _fallback_said
    if down and not _fallback_said and _neo4j_driver_installed():  # a missing driver said so already
        logger.info("[graph_backend] neo4j unavailable (url=%s): facts are not dual-written "
                    "until it answers (asked again every %d s; the graph sync catches up)",
                    url, int(_FALLBACK_RETRY_S))
    elif not down and _fallback_said:
        logger.info("[graph_backend] neo4j answers again (url=%s): dual-write resumed", url)
    _fallback_said = down


def get_graph_backend(conn: Any | None = None) -> Any:
    global _cached_backend, _cached_key, _cached_until
    try:
        from kazma_core.memory.backends import get_backends_cfg

        c = get_backends_cfg()
    except Exception:
        return SqliteGraphBackend(conn)
    provider, url, user, password = _graph_settings(c)
    cache_key = _settings_key(provider, url, user, password)
    if (_cached_backend is not None and _cached_key == cache_key and conn is None
            and (not _cached_until or time.monotonic() < _cached_until)):
        return _cached_backend

    until = 0.0
    if provider == "neo4j" and url:
        neo = _shared_neo4j(url, user, password)
        if neo.reachable()[0]:
            _say_fallback(False, url)
            if conn is None:
                _cached_backend, _cached_key, _cached_until = neo, cache_key, 0.0
            return neo
        _say_fallback(True, url)
        # A stand-in for a while, then Neo4j is asked again.
        until = time.monotonic() + _FALLBACK_RETRY_S
    be = SqliteGraphBackend(conn)
    if conn is None:
        _cached_backend, _cached_key, _cached_until = be, cache_key, until
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
            "error": 'The neo4j driver is not installed: uv pip install -e ".[neo4j]" in the Kazma folder, then restart',
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
            "detail": ("Connected. Kazma brings the copy up to date by itself (every 15 minutes); "
                       "Sync beliefs → Neo4j does it now."),
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


def clear_tenant_edges(*, tenant_id: str = "default") -> dict[str, Any]:
    """Best-effort mass Neo4j edge cleanup after a tenant graph-clear (M-15).

    SQLite-only installs no-op (beliefs SoT already invalidated). Never raises.
    """
    try:
        be = get_graph_backend()
        if getattr(be, "name", "") != "neo4j":
            return {"ok": True, "cleared": 0, "skipped": "not_neo4j"}
        if not getattr(be, "available", False):
            return {"ok": True, "cleared": 0, "skipped": "neo4j_offline"}
        clearer = getattr(be, "clear_tenant_edges", None)
        if not callable(clearer):
            return {"ok": True, "cleared": 0, "skipped": "no_clear_method"}
        n = int(clearer(tenant_id=tenant_id) or 0)
        return {"ok": True, "cleared": n}
    except Exception as exc:
        logger.debug("[graph_backend] clear_tenant_edges failed", exc_info=True)
        return {"ok": False, "cleared": 0, "error": str(exc)[:300]}
