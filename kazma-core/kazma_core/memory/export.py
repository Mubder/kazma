"""Nightly long-term export — JSONL + GraphML.

Plain-text dumps of the cognitive state so the knowledge base survives
even if the binary SQLite format changes. networkx is a declared core
dependency (``pyproject.toml``), so the GraphML export is safe to import
directly.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["export_nightly_snapshots"]


def export_nightly_snapshots(*, tenant_id: str = "default") -> dict[str, Path]:
    """Export active beliefs (JSON-L) + entity graph (GraphML).

    Returns ``{"jsonl": Path, "graphml": Path}``. Best-effort: missing
    pieces are skipped (no exception) so a partial export still lands.
    """
    from kazma_core.paths import exports_dir, primary_memory_db

    out_dir = exports_dir()
    result: dict[str, Path] = {}

    try:
        conn = sqlite3.connect(primary_memory_db())
        conn.row_factory = sqlite3.Row
        try:
            # ── JSON-L dump of active beliefs ──
            beliefs = conn.execute(
                """SELECT * FROM beliefs
                   WHERE tenant_id=? AND valid_until IS NULL AND invalidated_at IS NULL""",
                (tenant_id,),
            ).fetchall()
            # Include tenant_id in the filename so per-tenant exports don't
            # overwrite each other (the "default" tenant keeps the legacy
            # name for backward compat with existing tooling).
            jsonl_name = "kazma_beliefs_latest.jsonl" if tenant_id == "default" else f"kazma_beliefs_{tenant_id}.jsonl"
            jsonl_path = out_dir / jsonl_name
            with open(jsonl_path, "w", encoding="utf-8") as f:
                for b in beliefs:
                    f.write(json.dumps(dict(b), ensure_ascii=False, default=str) + "\n")
            result["jsonl"] = jsonl_path

            # M-16: episodes / archive / merge ledger (native .db backups
            # still SoT; these dumps make a portable text snapshot).
            extra_tables = (
                ("episodes", "episodes", "SELECT * FROM episodes WHERE tenant_id=?"),
                (
                    "beliefs_archive",
                    "beliefs_archive",
                    "SELECT * FROM beliefs_archive WHERE tenant_id=?",
                ),
                (
                    "entity_merges",
                    "entity_merges",
                    "SELECT * FROM entity_merges WHERE tenant_id=?",
                ),
                (
                    "entity_merges_archive",
                    "entity_merges_archive",
                    "SELECT * FROM entity_merges_archive WHERE tenant_id=?",
                ),
            )
            for key, table, sql in extra_tables:
                try:
                    rows = conn.execute(sql, (tenant_id,)).fetchall()
                except Exception:
                    logger.debug("[export] %s dump skipped", table, exc_info=True)
                    continue
                fname = (
                    f"kazma_{table}_latest.jsonl"
                    if tenant_id == "default"
                    else f"kazma_{table}_{tenant_id}.jsonl"
                )
                path = out_dir / fname
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        for r in rows:
                            f.write(json.dumps(dict(r), ensure_ascii=False, default=str) + "\n")
                    result[key] = path
                except Exception:
                    logger.debug("[export] %s write skipped", table, exc_info=True)

            # ── GraphML dump of entity graph ──
            try:
                import networkx as nx

                g = nx.DiGraph()
                entities = conn.execute(
                    "SELECT id, name, type FROM entities WHERE tenant_id=?", (tenant_id,)
                ).fetchall()
                for e in entities:
                    g.add_node(e["id"], label=e["name"], type=e["type"])
                edges = conn.execute(
                    """SELECT subject, object, predicate FROM beliefs
                       WHERE tenant_id=? AND valid_until IS NULL""",
                    (tenant_id,),
                ).fetchall()
                for edge in edges:
                    g.add_edge(edge["subject"], edge["object"], predicate=edge["predicate"])
                graphml_name = "kazma_graph_latest.graphml" if tenant_id == "default" else f"kazma_graph_{tenant_id}.graphml"
                graphml_path = out_dir / graphml_name
                nx.write_graphml(g, graphml_path)
                result["graphml"] = graphml_path
            except Exception:
                logger.debug("[export] GraphML step skipped", exc_info=True)
        finally:
            conn.close()
    except Exception:
        logger.debug("[export] nightly snapshot failed", exc_info=True)

    # Ops-db audit trail (queue is operational noise — skip it).
    try:
        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        ops = sqlite3.connect(memory_ops_db())
        ops.row_factory = sqlite3.Row
        try:
            ensure_ops_schema(ops)
            audits = ops.execute(
                "SELECT * FROM memory_audit_log WHERE tenant_id=? "
                "ORDER BY timestamp DESC LIMIT 20000",
                (tenant_id,),
            ).fetchall()
            audit_name = (
                "kazma_audit_latest.jsonl"
                if tenant_id == "default"
                else f"kazma_audit_{tenant_id}.jsonl"
            )
            audit_path = out_dir / audit_name
            with open(audit_path, "w", encoding="utf-8") as f:
                for r in audits:
                    f.write(json.dumps(dict(r), ensure_ascii=False, default=str) + "\n")
            result["audit"] = audit_path
        finally:
            ops.close()
    except Exception:
        logger.debug("[export] ops audit dump skipped", exc_info=True)
    return result
