"""Nightly long-term export — JSONL + GraphML.

Plain-text dumps of the cognitive state so the knowledge base survives
even if the binary SQLite format changes. networkx is a declared core
dependency (``pyproject.toml``), so the GraphML export is safe to import
directly.
"""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["export_nightly_snapshots"]

#: Rows read at a time: every table is written whole, never a newest slice
#: (Memory audit M30 -- the audit log stopped at its newest 20,000 rows).
_PAGE = 2000


class _ExportResult(dict):
    """The files written (``{piece: Path}``), plus what failed (``failed``)
    and how many rows each piece holds (``counts``). A failed piece is a
    failed export: the handler returns False and the queue retries it
    (Memory audit M20 -- failures were logged at DEBUG and counted done)."""

    def __init__(self) -> None:
        super().__init__()
        self.failed: list[str] = []
        self.counts: dict[str, int] = {}


def _portable(row: Any) -> dict[str, Any]:
    """A row as JSON can hold it: no vector (``embedding``: a rebuilt index,
    not data), and no other bytes as a Python literal (Memory audit M30:
    ``default=str`` wrote ``b'...'``)."""
    out: dict[str, Any] = {}
    for key in row.keys():
        if key == "embedding":
            continue
        value = row[key]
        if isinstance(value, (bytes, bytearray, memoryview)):
            value = {"base64": base64.b64encode(bytes(value)).decode("ascii")}
        out[key] = value
    return out


def _dump_query_jsonl(path: Path, conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    """Every row of *sql*, a page at a time; returns how many."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        cur = conn.execute(sql, params)
        while True:
            rows = cur.fetchmany(_PAGE)
            if not rows:
                break
            for r in rows:
                f.write(json.dumps(_portable(r), ensure_ascii=False, default=str) + "\n")
                n += 1
    return n


def _atomic_write(path: Path, writer) -> Any:
    """Write via tmp + rename so a crash never leaves a truncated "latest"
    snapshot (audit L-15) — every other backup path in the repo already
    does tmp+rename; this was the odd one out."""
    import os

    tmp = path.with_name(path.name + ".tmp")
    out = writer(tmp)
    os.replace(tmp, path)
    return out


def _name(table: str, tenant_id: str, ext: str = "jsonl") -> str:
    # The "default" tenant keeps the legacy name (existing tooling reads it).
    return f"kazma_{table}_latest.{ext}" if tenant_id == "default" else f"kazma_{table}_{tenant_id}.{ext}"


def export_nightly_snapshots(*, tenant_id: str = "default") -> _ExportResult:
    """Export the tenant's memory as portable text: current facts, memories,
    archived facts and merge ledgers (JSON-L), the entity graph (GraphML),
    and the audit trail (JSON-L), each whole. Native ``.db`` backups remain
    the restore source; this is a snapshot a person or another tool can read.

    Returns an :class:`_ExportResult`; a piece that failed is named in
    ``failed`` (a partial export still lands what it could).
    """
    from kazma_core.paths import exports_dir, memory_ops_db, primary_memory_db

    out_dir = exports_dir()
    result = _ExportResult()

    def piece(key: str, path: Path, conn: sqlite3.Connection, sql: str) -> None:
        try:
            result.counts[key] = _atomic_write(path, lambda p: _dump_query_jsonl(p, conn, sql, (tenant_id,)))
            result[key] = path
        except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
            result.failed.append(key)
            logger.warning("[export] %s of %s failed: %s", key, tenant_id, exc)

    try:
        conn = sqlite3.connect(primary_memory_db())
        conn.row_factory = sqlite3.Row
        try:
            piece("jsonl", out_dir / _name("beliefs", tenant_id), conn,
                  "SELECT * FROM beliefs WHERE tenant_id=? AND valid_until IS NULL AND invalidated_at IS NULL")
            # M-16: episodes / archive / merge ledger.
            for table in ("episodes", "beliefs_archive", "entity_merges", "entity_merges_archive"):
                piece(table, out_dir / _name(table, tenant_id), conn, f"SELECT * FROM {table} WHERE tenant_id=?")
            # ── GraphML dump of entity graph ──
            try:
                import networkx as nx

                g = nx.DiGraph()
                for e in conn.execute("SELECT id, name, type FROM entities WHERE tenant_id=?", (tenant_id,)):
                    g.add_node(e["id"], label=e["name"], type=e["type"])
                for edge in conn.execute(
                    "SELECT subject, object, predicate FROM beliefs WHERE tenant_id=? AND valid_until IS NULL",
                    (tenant_id,),
                ):
                    g.add_edge(edge["subject"], edge["object"], predicate=edge["predicate"])
                graphml_path = out_dir / _name("graph", tenant_id, "graphml")
                _atomic_write(graphml_path, lambda p: nx.write_graphml(g, p))
                result["graphml"] = graphml_path
                result.counts["graphml"] = g.number_of_nodes()
            except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
                result.failed.append("graphml")
                logger.warning("[export] GraphML of %s failed: %s", tenant_id, exc)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        result.failed.append("memory database")
        logger.warning("[export] the memory database of %s could not be read: %s", tenant_id, exc)

    # Ops-db audit trail, whole (queue is operational noise — skip it).
    try:
        from kazma_core.memory.schema_v2 import ensure_ops_schema

        ops = sqlite3.connect(memory_ops_db())
        ops.row_factory = sqlite3.Row
        try:
            ensure_ops_schema(ops)
            piece("audit", out_dir / _name("audit", tenant_id), ops,
                  "SELECT * FROM memory_audit_log WHERE tenant_id=? ORDER BY timestamp DESC")
        finally:
            ops.close()
    except sqlite3.Error as exc:
        result.failed.append("audit")
        logger.warning("[export] the audit trail of %s could not be read: %s", tenant_id, exc)
    return result
