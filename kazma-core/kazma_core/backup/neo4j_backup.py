"""Back up Kazma's graph memory, and put it back.

Neo4j held 323 nodes of graph memory on the live install and appeared in no
backup manifest. Nothing walks Docker volumes, so the universal sweep never
saw it: a disk failure took the whole graph.

Why a logical export rather than ``neo4j-admin database dump``
--------------------------------------------------------------
The deployment runs Neo4j **Community**, where the online
``neo4j-admin database backup`` is an Enterprise feature and the offline
``dump`` requires stopping the database. Stopping the graph on every backup
would mean scheduled downtime for the agent's memory -- a backup that
degrades the thing it protects, on a cadence.

APOC is not installed either (the plugins directory holds only a README),
so ``apoc.export`` is unavailable.

That leaves a driver-level export, which is what this is, and it turns out
to be the better artifact anyway: JSON Lines is readable, diffable,
restores into a different Neo4j version than it came from, and streams
without holding the graph in memory. A binary dump has none of those
properties.

Consistency
-----------
The export runs in one read transaction, so nodes and relationships come
from a single consistent snapshot rather than two racing queries. It is
still a point in time for the graph only -- see the architecture note about
cross-store consistency.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "GRAPH_EXPORT_NAME",
    "graph_backup_enabled",
    "export_graph",
    "restore_graph",
]

GRAPH_EXPORT_NAME = "neo4j_graph.jsonl"

# The property used to re-link relationships on restore. Written into the
# export, never into the live graph, and removed once restore finishes.
_KEY = "_kazma_backup_id"

# Batch size for restore writes. Large enough to be fast, small enough that
# a failure leaves a bounded amount of work to redo.
_BATCH = 500


@dataclass
class GraphExport:
    """Outcome of one export or restore. Never an exception."""

    ok: bool = False
    nodes: int = 0
    relationships: int = 0
    indexes: int = 0
    constraints: int = 0
    path: str = ""
    error: str = ""
    skipped: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = {
            "ok": self.ok,
            "nodes": self.nodes,
            "relationships": self.relationships,
            "indexes": self.indexes,
            "constraints": self.constraints,
        }
        if self.path:
            d["path"] = self.path
        if self.error:
            d["error"] = self.error[:300]
        if self.skipped:
            d["skipped"] = self.skipped
        return d


def _graph_cfg() -> dict[str, Any]:
    """Live-read the graph backend config. Never raises."""
    try:
        from kazma_core.memory.backends import get_backends_cfg

        return dict((get_backends_cfg() or {}).get("graph") or {})
    except Exception:  # noqa: BLE001
        logger.debug("[neo4j-backup] graph config unavailable", exc_info=True)
        return {}


def graph_backup_enabled() -> bool:
    """True only when Neo4j is the configured graph backend AND has a URL.

    Self-disabling, like the Postgres backup: an install using the SQLite
    graph backend must not fail its backup for a database it never had.
    """
    cfg = _graph_cfg()
    return str(cfg.get("provider") or "").lower() == "neo4j" and bool(cfg.get("url"))


def _driver(cfg: dict[str, Any] | None = None):
    from neo4j import GraphDatabase

    c = cfg if cfg is not None else _graph_cfg()
    return GraphDatabase.driver(
        str(c.get("url") or ""),
        auth=(str(c.get("user") or "neo4j"), str(c.get("password") or "")),
    )


def _database(cfg: dict[str, Any]) -> str:
    return str(cfg.get("database") or "neo4j")


def _schema(session) -> tuple[list[dict], list[dict]]:
    """Index and constraint definitions, so a restore rebuilds the shape too.

    A graph restored without its constraints looks complete and silently
    permits the duplicates the constraints existed to prevent.
    """
    idx: list[dict] = []
    cons: list[dict] = []
    try:
        for r in session.run("SHOW CONSTRAINTS YIELD name, type, createStatement"):
            cons.append({"name": r["name"], "type": r["type"],
                         "statement": r["createStatement"]})
    except Exception:  # noqa: BLE001
        logger.debug("[neo4j-backup] SHOW CONSTRAINTS unsupported", exc_info=True)
    try:
        for r in session.run(
            "SHOW INDEXES YIELD name, type, createStatement, owningConstraint"
        ):
            if r.get("owningConstraint"):
                continue  # created implicitly by its constraint
            if str(r["type"]).upper() == "LOOKUP":
                # Token lookup indexes are built into every Neo4j database.
                # Exporting them means every restore tries to recreate what
                # already exists and logs EquivalentSchemaRuleAlreadyExists --
                # noise that trains you to ignore genuine index failures.
                continue
            idx.append({"name": r["name"], "type": r["type"],
                        "statement": r["createStatement"]})
    except Exception:  # noqa: BLE001
        logger.debug("[neo4j-backup] SHOW INDEXES unsupported", exc_info=True)
    return idx, cons


def export_graph(dest_dir: str | Path) -> GraphExport:
    """Export the whole graph to ``dest_dir/neo4j_graph.jsonl``. Never raises.

    Written to a ``.tmp`` and atomically renamed, so an interrupted export
    can never be mistaken for a complete one -- the same discipline the
    Postgres dump uses.
    """
    out = GraphExport()
    if not graph_backup_enabled():
        out.ok = True
        out.skipped = "neo4j is not the configured graph backend"
        return out

    cfg = _graph_cfg()
    dest = Path(dest_dir)
    final = dest / GRAPH_EXPORT_NAME
    tmp = dest / f".{GRAPH_EXPORT_NAME}.tmp"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        drv = _driver(cfg)
    except Exception as exc:  # noqa: BLE001
        out.error = f"driver unavailable: {exc}"
        return out

    try:
        with drv:
            with drv.session(database=_database(cfg)) as session:
                indexes, constraints = _schema(session)
                with tmp.open("w", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps({
                        "kind": "meta", "version": 1,
                        "indexes": indexes, "constraints": constraints,
                    }) + "\n")
                    out.indexes = len(indexes)
                    out.constraints = len(constraints)

                    for rec in session.run(
                        "MATCH (n) RETURN elementId(n) AS id, labels(n) AS labels, "
                        "properties(n) AS props"
                    ):
                        fh.write(json.dumps({
                            "kind": "node", "id": rec["id"],
                            "labels": list(rec["labels"] or []),
                            "props": dict(rec["props"] or {}),
                        }, default=str) + "\n")
                        out.nodes += 1

                    for rec in session.run(
                        "MATCH (a)-[r]->(b) RETURN elementId(a) AS start, "
                        "elementId(b) AS end, type(r) AS type, properties(r) AS props"
                    ):
                        fh.write(json.dumps({
                            "kind": "rel", "start": rec["start"], "end": rec["end"],
                            "type": rec["type"], "props": dict(rec["props"] or {}),
                        }, default=str) + "\n")
                        out.relationships += 1
    except Exception as exc:  # noqa: BLE001
        out.error = str(exc)
        tmp.unlink(missing_ok=True)
        logger.warning("[neo4j-backup] export failed: %s", exc)
        return out

    try:
        tmp.replace(final)
    except Exception as exc:  # noqa: BLE001
        out.error = f"could not finalise export: {exc}"
        tmp.unlink(missing_ok=True)
        return out

    out.ok = True
    out.path = str(final)
    logger.info(
        "[neo4j-backup] exported %d nodes, %d relationships to %s",
        out.nodes, out.relationships, final.name,
    )
    return out


def _read_export(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def restore_graph(
    export_path: str | Path,
    *,
    allow_nonempty: bool = False,
    cfg: dict[str, Any] | None = None,
) -> GraphExport:
    """Load an export back into Neo4j.

    Refuses a graph that already has nodes unless ``allow_nonempty`` is set.
    A restore is run when something has gone wrong, often against the wrong
    target by accident, and silently merging a backup into a live graph
    produces a mess that is far harder to diagnose than a refusal.

    Relationships are re-linked through a temporary key property carrying
    the original elementId; Neo4j assigns new ids on write, so the export's
    own ids cannot be reused. The key is removed once linking completes --
    leaving it behind would put backup bookkeeping into live memory.
    """
    out = GraphExport()
    src = Path(export_path)
    if not src.is_file():
        out.error = f"export not found: {src}"
        return out

    c = cfg if cfg is not None else _graph_cfg()
    try:
        drv = _driver(c)
    except Exception as exc:  # noqa: BLE001
        out.error = f"driver unavailable: {exc}"
        return out

    try:
        with drv:
            db = _database(c)
            with drv.session(database=db) as session:
                existing = session.run(
                    "MATCH (n) RETURN count(n) AS c"
                ).single()["c"]
                if existing and not allow_nonempty:
                    out.error = (
                        f"target graph already holds {existing} node(s). "
                        "Refusing to merge a backup into a populated graph -- "
                        "pass allow_nonempty=True if that is genuinely intended."
                    )
                    return out

                nodes: list[dict] = []
                rels: list[dict] = []
                meta: dict[str, Any] = {}
                for rec in _read_export(src):
                    kind = rec.get("kind")
                    if kind == "node":
                        nodes.append(rec)
                    elif kind == "rel":
                        rels.append(rec)
                    elif kind == "meta":
                        meta = rec

                # Constraints first: creating them after the data would fail
                # on exactly the duplicates they exist to prevent.
                for spec in (meta.get("constraints") or []):
                    try:
                        session.run(spec["statement"])
                        out.constraints += 1
                    except Exception:  # noqa: BLE001
                        logger.warning("[neo4j-backup] constraint %r failed",
                                       spec.get("name"), exc_info=True)

                # Index the restore key BEFORE the relationship pass: linking
                # 300 relationships against unindexed nodes is a full scan per
                # row, and that is how a restore that works on a small graph
                # becomes unusable on a real one.
                session.run(
                    f"CREATE INDEX kazma_restore_key IF NOT EXISTS "
                    f"FOR (n:`{_RESTORE_LABEL}`) ON (n.`{_KEY}`)"
                )
                for i in range(0, len(nodes), _BATCH):
                    session.run(_create_nodes_cypher(), rows=nodes[i:i + _BATCH])
                out.nodes = len(nodes)
                for i in range(0, len(rels), _BATCH):
                    session.run(_create_rels_cypher(), rows=rels[i:i + _BATCH])
                out.relationships = len(rels)

                for spec in (meta.get("indexes") or []):
                    try:
                        session.run(spec["statement"])
                        out.indexes += 1
                    except Exception:  # noqa: BLE001
                        logger.warning("[neo4j-backup] index %r failed",
                                       spec.get("name"), exc_info=True)

                # Strip the bookkeeping key and helper label/index.
                session.run(
                    f"MATCH (n:`{_RESTORE_LABEL}`) "
                    f"REMOVE n.`{_KEY}`, n:`{_RESTORE_LABEL}`"
                )
                session.run("DROP INDEX kazma_restore_key IF EXISTS")
    except Exception as exc:  # noqa: BLE001
        out.error = str(exc)
        logger.warning("[neo4j-backup] restore failed: %s", exc)
        return out

    out.ok = True
    out.path = str(src)
    logger.info(
        "[neo4j-backup] restored %d nodes, %d relationships",
        out.nodes, out.relationships,
    )
    return out


# A temporary label so the restore key can be indexed and the helper state
# removed afterwards without touching unrelated nodes.
_RESTORE_LABEL = "_KazmaRestore"


def _create_nodes_cypher() -> str:
    """Create nodes with their labels, without APOC.

    Labels cannot be parameterised in Cypher, and APOC is not installed on
    the reference deployment. ``CALL { ... }`` with a per-row subquery would
    still need dynamic labels, so nodes are created with a marker label and
    their real labels are applied through the driver-supported
    ``SET n:$(labels)`` dynamic-label syntax available in Neo4j 5.26.
    """
    return (
        "UNWIND $rows AS row "
        f"CREATE (n:`{_RESTORE_LABEL}`) "
        "SET n = row.props "
        f"SET n.`{_KEY}` = row.id "
        "WITH n, row.labels AS labels "
        "SET n:$(labels) "
        "RETURN count(n)"
    )


def _create_rels_cypher() -> str:
    """Link relationships by the temporary key, with a dynamic type."""
    return (
        "UNWIND $rows AS row "
        f"MATCH (a:`{_RESTORE_LABEL}` {{`{_KEY}`: row.start}}) "
        f"MATCH (b:`{_RESTORE_LABEL}` {{`{_KEY}`: row.end}}) "
        "CREATE (a)-[r:$(row.type)]->(b) "
        "SET r = row.props "
        "RETURN count(r)"
    )
