"""Layer 4 — sqlite-vec local vector store.

Zero-dependency local embeddings using sqlite-vec virtual tables.
Each worker gets its own table so embeddings stay isolated.  Shares
the same ``get_encoder()`` singleton from ``vector.py``.

Falls back gracefully when sqlite-vec is not installed: all queries
return empty lists — no crashes.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/vector.db"
_TABLE_PREFIX = "worker_vectors"


class SQLiteVectorStore:
    """sqlite-vec backed local vector store (Layer 4).

    Manages per-worker embedding tables in a single SQLite database.
    Embeddings are produced by the shared ``get_encoder()`` singleton.

    Args:
        db_path: Path to the SQLite database file.

    Usage::

        store = SQLiteVectorStore()
        store.ensure_table("core")
        store.index("core", "doc-1", "def authenticate(): ...")
        results = store.query("core", "auth function", limit=5)
    """

    def __init__(self, db_path: str = _DEFAULT_DB) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._vec_available: bool | None = None  # None = not checked yet

    # ── Connection ──────────────────────────────────────────────────────

    def _ensure_conn(self) -> sqlite3.Connection | None:
        """Return the SQLite connection, creating if needed."""
        if self._conn is not None:
            return self._conn
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute("PRAGMA journal_mode=WAL")
            return self._conn
        except Exception as exc:
            logger.warning("[SQLiteVector] Connection failed: %s", exc)
            return None

    # ── sqlite-vec detection ────────────────────────────────────────────

    def _check_vec_available(self) -> bool:
        """Check if sqlite-vec extension can be loaded.  Caches result."""
        if self._vec_available is not None:
            return self._vec_available
        conn = self._ensure_conn()
        if conn is None:
            self._vec_available = False
            return False
        try:
            conn.execute("SELECT vec_version()")
            self._vec_available = True
            logger.info("[SQLiteVector] sqlite-vec extension loaded")
            return True
        except Exception:
            try:
                # Try loading the extension explicitly
                conn.load_extension("vec0")
                conn.execute("SELECT vec_version()")
                self._vec_available = True
                logger.info("[SQLiteVector] sqlite-vec loaded via load_extension")
                return True
            except Exception:
                self._vec_available = False
                logger.warning("[SQLiteVector] sqlite-vec not available — local vector disabled")
                return False

    @property
    def available(self) -> bool:
        """Whether sqlite-vec is available for queries."""
        return self._check_vec_available()

    # ── Table management ────────────────────────────────────────────────

    def _table_name(self, worker_name: str) -> str:
        """Sanitise and return the table name for a worker."""
        safe = worker_name.replace("-", "_").replace(".", "_")
        return f"{_TABLE_PREFIX}_{safe}"

    def ensure_table(self, worker_name: str) -> bool:
        """Create the vector table for a worker if it doesn't exist."""
        if not self._check_vec_available():
            return False
        conn = self._ensure_conn()
        if conn is None:
            return False
        table = self._table_name(worker_name)
        try:
            conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {table}
                USING vec0(
                    id TEXT PRIMARY KEY,
                    embedding FLOAT[384]
                )
            """)
            conn.commit()
            return True
        except Exception as exc:
            logger.warning("[SQLiteVector] Table creation failed for %s: %s", worker_name, exc)
            return False

    # ── CRUD ────────────────────────────────────────────────────────────

    def index(
        self,
        worker_name: str,
        doc_id: str,
        text: str,
    ) -> bool:
        """Index a document into the worker's vector table.

        Returns True on success.
        """
        from kazma_core.swarm.memory.vector import get_encoder

        if not self._check_vec_available():
            return False
        model = get_encoder()
        if model is None:
            return False

        try:
            embedding = model.encode(text, convert_to_numpy=False)
            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()
            emb_bytes = _serialize_embedding(embedding)
        except Exception as exc:
            logger.warning("[SQLiteVector] Encode failed: %s", exc)
            return False

        if not self.ensure_table(worker_name):
            return False

        conn = self._ensure_conn()
        if conn is None:
            return False
        table = self._table_name(worker_name)
        try:
            conn.execute(
                f"INSERT OR REPLACE INTO {table} (id, embedding) VALUES (?, ?)",
                (doc_id, emb_bytes),
            )
            conn.commit()
            return True
        except Exception as exc:
            logger.warning("[SQLiteVector] Index failed for %s/%s: %s", worker_name, doc_id, exc)
            return False

    def query(
        self,
        worker_name: str,
        text: str,
        limit: int = 10,
    ) -> list[tuple[str, float]]:
        """Semantic search via cosine similarity in the worker's vector table.

        Returns list of (doc_id, similarity_score) tuples.
        """
        from kazma_core.swarm.memory.vector import get_encoder

        if not self._check_vec_available():
            return []
        model = get_encoder()
        if model is None:
            return []

        try:
            embedding = model.encode(text, convert_to_numpy=False)
            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()
            emb_bytes = _serialize_embedding(embedding)
        except Exception:
            return []

        if not self.ensure_table(worker_name):
            return []

        conn = self._ensure_conn()
        if conn is None:
            return []
        table = self._table_name(worker_name)
        try:
            cursor = conn.execute(
                f"""
                SELECT id, vec_distance_cosine(embedding, ?) AS distance
                FROM {table}
                ORDER BY distance ASC
                LIMIT ?
                """,
                (emb_bytes, limit),
            )
            rows = cursor.fetchall()
            return [(r[0], 1.0 - float(r[1])) for r in rows if r[1] is not None]
        except Exception as exc:
            logger.warning("[SQLiteVector] Query failed for %s: %s", worker_name, exc)
            return []

    def delete(self, worker_name: str, doc_id: str) -> bool:
        """Remove a document from the worker's vector table."""
        conn = self._ensure_conn()
        if conn is None:
            return False
        table = self._table_name(worker_name)
        try:
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (doc_id,))
            conn.commit()
            return True
        except Exception as exc:
            logger.warning("[SQLiteVector] Delete failed: %s", exc)
            return False

    def count(self, worker_name: str) -> int:
        """Number of documents in a worker's vector table."""
        conn = self._ensure_conn()
        if conn is None:
            return 0
        table = self._table_name(worker_name)
        try:
            cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ── Helpers ───────────────────────────────────────────────────────────────


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Pack a float list into a compact binary blob for sqlite-vec."""
    import struct
    return struct.pack(f"{len(embedding)}f", *embedding)
