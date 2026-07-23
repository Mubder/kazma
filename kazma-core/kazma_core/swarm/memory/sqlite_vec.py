"""Layer 4 — sqlite-vec local vector store.

Zero-dependency local embeddings using sqlite-vec virtual tables.
Each worker gets its own table so embeddings stay isolated.  Shares
the same ``get_encoder()`` singleton from ``vector.py``.

Falls back gracefully when sqlite-vec is not installed: all queries
return empty lists — no crashes.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

__all__ = ["SQLiteVectorStore"]

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/vector.db"
_TABLE_PREFIX = "worker_vectors"
# SQLite can't parameterize identifiers, so table names are built via
# f-string interpolation below. Worker names can originate from the
# ``spawn_agent`` tool (LLM-controlled, HITL-gated) as well as static
# config, so anything outside this set is stripped rather than trusted.
_UNSAFE_TABLE_CHARS = re.compile(r"[^A-Za-z0-9_]")


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
            from kazma_core.config_store import apply_sqlite_pragmas

            apply_sqlite_pragmas(self._conn)
            return self._conn
        except Exception as exc:
            logger.warning("[SQLiteVector] Connection failed: %s", exc)
            return None

    # ── sqlite-vec detection ────────────────────────────────────────────

    def _check_vec_available(self) -> bool:
        """Check if sqlite-vec extension can be loaded.  Caches result.

        Prefers the official ``sqlite-vec`` PyPI package (``sqlite_vec.load``),
        which ships platform wheels. Bare ``load_extension("vec0")`` only works
        when a system-wide vec0 binary is on the SQLite extension path — that
        path was failing silently even when ``pip install sqlite-vec`` was done.
        """
        if self._vec_available is not None:
            return self._vec_available
        conn = self._ensure_conn()
        if conn is None:
            self._vec_available = False
            return False

        # 1) Already loaded into this connection.
        try:
            conn.execute("SELECT vec_version()")
            self._vec_available = True
            logger.info("[SQLiteVector] sqlite-vec already active")
            return True
        except Exception:
            pass

        # 2) Official Python package — correct production path.
        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.execute("SELECT vec_version()")
            self._vec_available = True
            logger.info("[SQLiteVector] sqlite-vec loaded via sqlite_vec.load()")
            return True
        except Exception as exc:
            logger.debug("[SQLiteVector] sqlite_vec.load failed: %s", exc)

        # 3) Last resort: system extension name.
        try:
            conn.enable_load_extension(True)
            conn.load_extension("vec0")
            conn.execute("SELECT vec_version()")
            self._vec_available = True
            logger.info("[SQLiteVector] sqlite-vec loaded via load_extension(vec0)")
            return True
        except Exception:
            self._vec_available = False
            logger.warning(
                "[SQLiteVector] sqlite-vec not available — L4 disabled. "
                "Install with: pip install sqlite-vec  (or pip install -e '.[rag]')"
            )
            return False

    @property
    def available(self) -> bool:
        """Whether sqlite-vec is available for queries."""
        return self._check_vec_available()

    # ── Table management ────────────────────────────────────────────────

    def _table_name(self, worker_name: str) -> str:
        """Sanitise and return the table name for a worker.

        Normal names (alnum/hyphen/dot/underscore) map to the same table
        name as before. Any other character is stripped so a crafted
        worker name can never break out of the identifier position in the
        interpolated SQL below.
        """
        normalized = worker_name.replace("-", "_").replace(".", "_")
        safe = _UNSAFE_TABLE_CHARS.sub("", normalized) or "unnamed"
        return f"{_TABLE_PREFIX}_{safe}"

    def ensure_table(self, worker_name: str) -> bool:
        """Create the vector table for a worker if it doesn't exist.

        Uses the configured embedding dimension (default 384 for MiniLM,
        1024 for NIM/NeMo). If an existing table has a mismatched dimension
        (e.g. user switched providers), it is dropped and recreated — the
        vectors are a cache and will be re-indexed on next store.
        """
        if not self._check_vec_available():
            return False
        conn = self._ensure_conn()
        if conn is None:
            return False
        table = self._table_name(worker_name)
        try:
            from kazma_core.swarm.memory.embedder import get_embedding_dim

            dim = get_embedding_dim()
        except Exception:
            dim = 384
        try:
            conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS {table}
                USING vec0(
                    embedding FLOAT[{dim}],
                    +doc_id TEXT
                )
            """)
            conn.commit()
            return True
        except Exception as exc:
            # Dimension mismatch on an existing table — drop and recreate.
            if "dim" in str(exc).lower() or "shape" in str(exc).lower():
                logger.info(
                    "[SQLiteVector] Dimension mismatch for %s — dropping and recreating",
                    worker_name,
                )
                try:
                    conn.execute(f"DROP TABLE IF EXISTS {table}")
                    conn.execute(f"""
                        CREATE VIRTUAL TABLE IF NOT EXISTS {table}
                        USING vec0(
                            embedding FLOAT[{dim}],
                            +doc_id TEXT
                        )
                    """)
                    conn.commit()
                    return True
                except Exception as exc2:
                    logger.warning("[SQLiteVector] Recreate failed for %s: %s", worker_name, exc2)
                    return False
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
            embedding = model.encode(text)
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
        docs_table = f"{table}_docs"
        try:
            # vec0 uses an implicit integer rowid; doc_id lives in an
            # auxiliary column.  Delete-then-insert gives upsert-by-doc_id
            # semantics (vec0 does not support INSERT OR REPLACE on the
            # auxiliary column key).
            conn.execute(f"DELETE FROM {table} WHERE doc_id = ?", (doc_id,))
            conn.execute(
                f"INSERT INTO {table} (doc_id, embedding) VALUES (?, ?)",
                (doc_id, emb_bytes),
            )
            # Side table so L4 hits can return document text (vec0 only
            # stores the embedding + aux id).
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {docs_table} (
                    doc_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL
                )
                """
            )
            conn.execute(
                f"INSERT OR REPLACE INTO {docs_table} (doc_id, content) VALUES (?, ?)",
                (doc_id, text[:4000]),
            )
            conn.commit()
            return True
        except Exception as exc:
            logger.warning("[SQLiteVector] Index failed for %s/%s: %s", worker_name, doc_id, exc)
            return False

    def get_texts(self, worker_name: str, ids: list[str]) -> dict[str, str]:
        """Fetch stored document text for the given doc_ids (side table)."""
        if not ids:
            return {}
        conn = self._ensure_conn()
        if conn is None:
            return {}
        docs_table = f"{self._table_name(worker_name)}_docs"
        out: dict[str, str] = {}
        try:
            placeholders = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT doc_id, content FROM {docs_table} WHERE doc_id IN ({placeholders})",
                list(ids),
            ).fetchall()
            for rid, content in rows:
                if rid and content:
                    out[str(rid)] = str(content)
        except Exception:
            # Table may not exist yet for this worker.
            return {}
        return out

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
            embedding = model.encode(text)
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
                SELECT doc_id, distance
                FROM {table}
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (emb_bytes, limit),
            )
            rows = cursor.fetchall()
            return [(str(r[0]), 1.0 - float(r[1])) for r in rows if r[1] is not None and r[0] is not None]
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
            conn.execute(f"DELETE FROM {table} WHERE doc_id = ?", (doc_id,))
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

    def get_text(self, worker_name: str, doc_id: str) -> str:
        """Fetch the text content for a document by ID.

        Used by the UnifiedMemoryAdapter to populate the ``content`` field.
        Reads from the ``_docs`` side table (same as ``get_texts``).
        """
        conn = self._ensure_conn()
        if conn is None:
            return ""
        docs_table = f"{self._table_name(worker_name)}_docs"
        try:
            cursor = conn.execute(
                f"SELECT content FROM {docs_table} WHERE doc_id = ?", (doc_id,)
            )
            row = cursor.fetchone()
            return str(row[0]) if row else ""
        except Exception:
            # Table may not exist yet for this worker.
            return ""

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ── Helpers ───────────────────────────────────────────────────────────────


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Pack a float list into a compact binary blob for sqlite-vec."""
    import struct
    return struct.pack(f"{len(embedding)}f", *embedding)
