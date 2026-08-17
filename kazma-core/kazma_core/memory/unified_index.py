"""Physical unified index over beliefs + Knowledge chunks.

Source stores stay separate (V2 beliefs, Knowledge Library). This table
is a single recall surface in ``memory_ops.db`` so operators can search
both without collapsing schemas. Dual-write is best-effort.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

__all__ = ["search_unified", "upsert_unified"]

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS unified_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    text TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_unified_tenant ON unified_items(tenant_id);
CREATE INDEX IF NOT EXISTS idx_unified_kind ON unified_items(kind);
"""


def _conn() -> sqlite3.Connection:
    from kazma_core.paths import memory_ops_db

    conn = sqlite3.connect(str(memory_ops_db()), check_same_thread=False)
    from kazma_core.config_store import apply_sqlite_pragmas

    apply_sqlite_pragmas(conn)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def upsert_unified(
    *,
    item_id: str,
    kind: str,
    text: str,
    tenant_id: str = "default",
) -> None:
    if not item_id or not (text or "").strip():
        return
    try:
        conn = _conn()
        try:
            conn.execute(
                """
                INSERT INTO unified_items (id, kind, tenant_id, text, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  kind = excluded.kind,
                  tenant_id = excluded.tenant_id,
                  text = excluded.text,
                  updated_at = excluded.updated_at
                """,
                (
                    str(item_id),
                    str(kind or "unknown"),
                    str(tenant_id or "default"),
                    str(text).strip()[:4000],
                    time.time(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.debug("[unified_index] upsert failed", exc_info=True)


def search_unified(
    query: str,
    *,
    tenant_id: str = "default",
    limit: int = 10,
) -> list[dict[str, Any]]:
    terms = [t for t in (query or "").lower().split() if len(t) >= 2][:8]
    if not terms:
        return []
    try:
        conn = _conn()
        try:
            clauses = " AND ".join(["LOWER(text) LIKE ?" for _ in terms])
            params: list[Any] = [tenant_id]
            params.extend([f"%{t}%" for t in terms])
            params.append(max(1, min(int(limit), 50)))
            rows = conn.execute(
                f"""
                SELECT id, kind, text, updated_at
                FROM unified_items
                WHERE tenant_id = ? AND ({clauses})
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "text": r["text"],
                    "updated_at": r["updated_at"],
                    "store": "unified",
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception:
        logger.debug("[unified_index] search failed", exc_info=True)
        return []
