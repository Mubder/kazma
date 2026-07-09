"""FTS5 Memory — SQLite Full-Text Search for conversation memory.

Uses SQLite FTS5 for keyword-based search with BM25 ranking.
Complements vector memory (ChromaDB) for hybrid retrieval.

Usage:
    memory = FTS5Memory()
    memory.add("User prefers dark mode", {"topic": "preferences"})
    results = memory.search("dark mode")
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FTS5Memory:
    """SQLite FTS5-backed memory for keyword search.

    Args:
        db_path: Path to SQLite database. Defaults to ~/.kazma/memory.db.
        table_name: Name of the FTS5 virtual table.
    """

    def __init__(
        self,
        db_path: str = "~/.kazma/memory.db",
        table_name: str = "memory_fts",
    ) -> None:
        self._db_path = str(Path(db_path).expanduser().resolve())
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._table_name = table_name
        self._conn = sqlite3.connect(self._db_path)
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(self._conn)
        self._conn.row_factory = sqlite3.Row
        self._create_table()
        logger.info("[FTS5Memory] Initialized at %s (table=%s)", self._db_path, table_name)

    def _create_table(self) -> None:
        """Create FTS5 virtual table if not exists."""
        self._conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {self._table_name}
            USING fts5(
                text,
                metadata,
                doc_id UNINDEXED,
                timestamp UNINDEXED,
                tokenize='porter unicode61'
            )
        """)
        self._conn.commit()

    def add(
        self,
        text: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """Store a text fragment with metadata.

        Args:
            text: The text to store.
            metadata: Optional metadata dict.
            doc_id: Optional ID. Generated if not provided.

        Returns:
            The document ID used for storage.
        """
        doc_id = doc_id or str(uuid.uuid4())
        meta_json = json.dumps(metadata or {"source": "agent"})
        timestamp = datetime.now(UTC).isoformat()

        self._conn.execute(
            f"INSERT INTO {self._table_name} (text, metadata, doc_id, timestamp) VALUES (?, ?, ?, ?)",
            (text, meta_json, doc_id, timestamp),
        )
        self._conn.commit()
        logger.debug("[FTS5Memory] Stored doc %s: %.80s", doc_id, text)
        return doc_id

    def search(
        self,
        query: str,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Search for fragments matching the query.

        Args:
            query: Search query (supports FTS5 syntax).
            limit: Maximum results to return.
            min_score: Minimum BM25 score threshold.

        Returns:
            List of dicts with 'text', 'metadata', 'doc_id', 'score' keys.
        """
        try:
            rows = self._conn.execute(
                f"""
                SELECT text, metadata, doc_id, rank
                FROM {self._table_name}
                WHERE {self._table_name} MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, limit),
            ).fetchall()

            results = []
            for row in rows:
                score = -row["rank"]  # FTS5 rank is negative (lower = better)
                if score < min_score:
                    continue
                results.append({
                    "text": row["text"],
                    "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                    "doc_id": row["doc_id"],
                    "score": score,
                })
            return results

        except sqlite3.OperationalError as e:
            logger.warning("[FTS5Memory] Search error: %s", e)
            return []

    def delete(self, doc_id: str) -> bool:
        """Delete a document by ID."""
        cursor = self._conn.execute(
            f"DELETE FROM {self._table_name} WHERE doc_id = ?",
            (doc_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def count(self) -> int:
        """Number of stored fragments."""
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM {self._table_name}"
        ).fetchone()
        return row[0] if row else 0

    def clear(self) -> int:
        """Delete all documents. Returns count deleted."""
        cursor = self._conn.execute(f"DELETE FROM {self._table_name}")
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
