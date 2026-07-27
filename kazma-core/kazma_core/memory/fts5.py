"""FTS5 Memory — SQLite Full-Text Search for conversation memory.

Canonical schema matches ``kazma_memory.SQLiteMemoryBackend`` / adapter L3:

- ``memories`` table (SoT for content + metadata)
- ``memories_fts`` FTS5 virtual table (BM25)

The legacy ``memory_fts`` table (pre-unify) is migrated once into
``memories`` on init so VectorMemory's degrade path and L3 share one index.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["FTS5Memory"]

logger = logging.getLogger(__name__)


class FTS5Memory:
    """SQLite FTS5-backed memory for keyword search (canonical ``memories`` schema).

    Args:
        db_path: Path to SQLite database. Defaults to paths.fts5_memory_path().
        table_name: Deprecated — ignored. Kept for call-site compatibility.
    """

    def __init__(
        self,
        db_path: str | None = None,
        table_name: str = "memory_fts",  # noqa: ARG002 — legacy kwarg
    ) -> None:
        from kazma_core.paths import fts5_memory_path

        if db_path is None:
            db_path = fts5_memory_path()
        self._db_path = str(Path(db_path).expanduser().resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._table_name = "memories_fts"  # canonical
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(self._conn)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._create_canonical_schema()
            self._migrate_legacy_memory_fts()
        logger.info(
            "[FTS5Memory] Initialized at %s (schema=memories/memories_fts)",
            self._db_path,
        )

    def _create_canonical_schema(self) -> None:
        """Create memories + memories_fts + safe triggers (shared with L3 backend)."""
        from kazma_core.memory.schema import ensure_memories_schema_sync

        ensure_memories_schema_sync(self._conn)

    def _migrate_legacy_memory_fts(self) -> None:
        """One-shot: copy legacy memory_fts rows into memories if present.

        P2: Always retire the legacy table name even when empty — an empty
        ``memory_fts`` next to live ``memories_fts`` confuses diagnostics.
        Renamed to ``memory_fts_migrated`` (or dropped if rename fails).
        """
        try:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'"
            ).fetchone()
            if not row:
                return
            legacy: list = []
            try:
                legacy = self._conn.execute(
                    "SELECT text, metadata, doc_id, timestamp FROM memory_fts"
                ).fetchall()
            except Exception:
                # Schema may differ; still retire the table name below.
                legacy = []
            migrated = 0
            for r in legacy:
                try:
                    text = r["text"] if "text" in r.keys() else r[0]
                    meta_raw = r["metadata"] if "metadata" in r.keys() else r[1]
                    doc_id = r["doc_id"] if "doc_id" in r.keys() else r[2]
                except Exception:
                    continue
                if not text or not doc_id:
                    continue
                exists = self._conn.execute(
                    "SELECT 1 FROM memories WHERE id = ?", (doc_id,)
                ).fetchone()
                if exists:
                    continue
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO memories
                    (id, content, content_arabic, metadata, timestamp, source, relevance)
                    VALUES (?, ?, '', ?, 0, 'legacy_memory_fts', 1.0)
                    """,
                    (doc_id, text, meta_raw or "{}"),
                )
                migrated += 1
            if migrated:
                self._conn.commit()
                logger.info(
                    "[FTS5Memory] Migrated %d rows from legacy memory_fts → memories",
                    migrated,
                )
            # Always retire legacy name (empty or migrated) so health scans
            # don't report a dead empty FTS table.
            try:
                # Drop prior _migrated if present so rename always succeeds
                self._conn.execute("DROP TABLE IF EXISTS memory_fts_migrated")
                self._conn.execute(
                    "ALTER TABLE memory_fts RENAME TO memory_fts_migrated"
                )
                self._conn.commit()
                logger.info(
                    "[FTS5Memory] Retired legacy memory_fts → memory_fts_migrated "
                    "(rows_migrated=%d)",
                    migrated,
                )
            except Exception:
                try:
                    self._conn.execute("DROP TABLE IF EXISTS memory_fts")
                    self._conn.commit()
                    logger.info("[FTS5Memory] Dropped empty/legacy memory_fts table")
                except Exception:
                    pass
        except Exception:
            logger.debug("[FTS5Memory] legacy migrate skipped", exc_info=True)

    def add(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
        *,
        embedding: bytes | None = None,
        timestamp: int | None = None,
    ) -> str:
        """Store a text fragment with metadata into the canonical memories table.

        Always writes a real unix ``timestamp`` (never 0 for new rows). When
        *embedding* is omitted, encodes via the shared embedder so L3 BLOB
        semantic search works without Chroma.
        """
        doc_id = doc_id or str(uuid.uuid4())
        meta = dict(metadata or {"source": "agent"})
        source = str(meta.get("source", "agent") if isinstance(meta, dict) else "agent")
        tenant_id = meta.get("tenant_id") if isinstance(meta, dict) else None

        try:
            from kazma_core.swarm.memory.embedder import (
                encode_text_to_blob,
                resolve_unix_timestamp,
            )

            ts = int(timestamp) if timestamp and int(timestamp) > 0 else resolve_unix_timestamp(meta)
            meta.setdefault("timestamp", ts)
            emb = embedding if embedding is not None else encode_text_to_blob(text)
        except Exception:
            ts = int(timestamp) if timestamp and int(timestamp) > 0 else int(datetime.now(UTC).timestamp())
            meta.setdefault("timestamp", ts)
            emb = embedding

        meta_json = json.dumps(meta, ensure_ascii=False, default=str)

        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memories
                (id, content, content_arabic, metadata, timestamp, source, relevance, embedding, tenant_id)
                VALUES (?, ?, '', ?, ?, ?, 1.0, ?, ?)
                """,
                (doc_id, text, meta_json, ts, source, emb, tenant_id),
            )
            self._conn.commit()
        logger.debug("[FTS5Memory] Stored doc %s ts=%s emb=%s: %.80s", doc_id, ts, bool(emb), text)
        return doc_id

    def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.0,
        tenant_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search via FTS5 BM25 on memories_fts, join back to memories.

        When *tenant_id* is set, apply a **hard** tenant filter (P6): only rows
        whose ``tenant_id`` column equals that value. Rows with NULL/empty
        tenant are excluded from tenant-scoped queries.
        """
        try:
            safe_query = (query or "").strip().replace('"', '""')
            safe_query = f'"{safe_query}"' if safe_query else '""'
            with self._lock:
                if tenant_id:
                    rows = self._conn.execute(
                        """
                        SELECT m.id AS doc_id, m.content AS text, m.metadata,
                               m.tenant_id AS tenant_id, bm25(memories_fts) AS rank
                        FROM memories_fts
                        JOIN memories m ON m.id = memories_fts.memory_id
                        WHERE memories_fts MATCH ?
                          AND m.tenant_id = ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (safe_query, tenant_id, limit),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        """
                        SELECT m.id AS doc_id, m.content AS text, m.metadata,
                               m.tenant_id AS tenant_id, bm25(memories_fts) AS rank
                        FROM memories_fts
                        JOIN memories m ON m.id = memories_fts.memory_id
                        WHERE memories_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                        """,
                        (safe_query, limit),
                    ).fetchall()

            results = []
            for row in rows:
                score = -float(row["rank"])  # FTS5/bm25 rank: lower = better
                if score < min_score:
                    continue
                meta_raw = row["metadata"]
                try:
                    meta = json.loads(meta_raw) if meta_raw else {}
                except Exception:
                    meta = {}
                tid = row["tenant_id"] if "tenant_id" in row.keys() else None
                if tid and "tenant_id" not in meta:
                    meta["tenant_id"] = tid
                results.append(
                    {
                        "text": row["text"],
                        "metadata": meta,
                        "doc_id": row["doc_id"],
                        "score": score,
                        "tenant_id": tid,
                    }
                )
            return results
        except sqlite3.OperationalError as e:
            logger.warning("[FTS5Memory] Search error: %s", e)
            return []

    def delete(self, doc_id: str) -> bool:
        """Delete a document by ID."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM memories WHERE id = ?",
                (doc_id,),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def count(self) -> int:
        """Number of stored fragments."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
            return int(row[0]) if row else 0

    def clear(self) -> int:
        """Delete all documents. Returns count deleted."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM memories")
            self._conn.commit()
            return cursor.rowcount

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
