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
        """Create memories + memories_fts + triggers (aligned with L3 backend)."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                content_arabic TEXT,
                metadata TEXT DEFAULT '{}',
                timestamp INTEGER DEFAULT 0,
                source TEXT DEFAULT '',
                relevance REAL DEFAULT 1.0,
                embedding BLOB,
                tenant_id TEXT
            )
            """
        )
        try:
            self._conn.execute("ALTER TABLE memories ADD COLUMN tenant_id TEXT")
        except Exception:
            pass
        self._conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(memory_id, content, content_arabic)
            """
        )
        # Triggers keep FTS in sync (idempotent IF NOT EXISTS)
        self._conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(memory_id, content, content_arabic)
                VALUES (new.id, new.content, new.content_arabic);
            END
            """
        )
        self._conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                DELETE FROM memories_fts WHERE memory_id = old.id;
            END
            """
        )
        self._conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                DELETE FROM memories_fts WHERE memory_id = old.id;
                INSERT INTO memories_fts(memory_id, content, content_arabic)
                VALUES (new.id, new.content, new.content_arabic);
            END
            """
        )
        self._conn.commit()

    def _migrate_legacy_memory_fts(self) -> None:
        """One-shot: copy legacy memory_fts rows into memories if present."""
        try:
            row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'"
            ).fetchone()
            if not row:
                return
            legacy = self._conn.execute(
                "SELECT text, metadata, doc_id, timestamp FROM memory_fts"
            ).fetchall()
            if not legacy:
                return
            migrated = 0
            for r in legacy:
                text = r["text"] if "text" in r.keys() else r[0]
                meta_raw = r["metadata"] if "metadata" in r.keys() else r[1]
                doc_id = r["doc_id"] if "doc_id" in r.keys() else r[2]
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
            self._conn.commit()
            if migrated:
                logger.info(
                    "[FTS5Memory] Migrated %d rows from legacy memory_fts → memories",
                    migrated,
                )
            # Rename legacy table so we don't double-migrate
            try:
                self._conn.execute(
                    "ALTER TABLE memory_fts RENAME TO memory_fts_migrated"
                )
                self._conn.commit()
            except Exception:
                pass
        except Exception:
            logger.debug("[FTS5Memory] legacy migrate skipped", exc_info=True)

    def add(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """Store a text fragment with metadata into the canonical memories table."""
        doc_id = doc_id or str(uuid.uuid4())
        meta = metadata or {"source": "agent"}
        meta_json = json.dumps(meta)
        source = str(meta.get("source", "agent") if isinstance(meta, dict) else "agent")
        ts = int(datetime.now(UTC).timestamp())
        tenant_id = None
        if isinstance(meta, dict):
            tenant_id = meta.get("tenant_id")

        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO memories
                (id, content, content_arabic, metadata, timestamp, source, relevance, tenant_id)
                VALUES (?, ?, '', ?, ?, ?, 1.0, ?)
                """,
                (doc_id, text, meta_json, ts, source, tenant_id),
            )
            self._conn.commit()
        logger.debug("[FTS5Memory] Stored doc %s: %.80s", doc_id, text)
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
