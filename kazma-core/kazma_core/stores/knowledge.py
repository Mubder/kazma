"""Knowledge Library Store — SQLite-backed library + chunk persistence.

A "Knowledge Library" is a named, managed corpus of ingested documentation
(e.g. the Meta WhatsApp Cloud API docs).  Pages are fetched once, split into
hierarchy-aware chunks (see :mod:`knowledge_chunker`), and indexed for
retrieval.  This store holds the **source of truth**: full chunk content +
provenance metadata + a tokenized FTS5 copy.  Vector embeddings live in a
per-library ChromaDB collection (see :mod:`knowledge_index`); SQLite is the
durable backing store that survives ChromaDB unavailability.

The store is deliberately **decoupled** from chat memory:

- It writes its own tables (``knowledge_libraries``, ``knowledge_chunks``,
  ``knowledge_chunks_fts``) in the shared ``kazma-data/settings.db``.
- It never touches the ``agent_memory`` ChromaDB collection.
- Chunks are keyed by ``"{library_id}:{content_hash[:16]}"`` so identical
  sections across pages do not collide (a real bug in the shared
  ``UnifiedMemoryAdapter`` which keys on a bare content hash).

Concurrency model
-----------------
- WAL + ``busy_timeout=5000`` identical to ConfigStore / BookmarkStore.
- The process-wide singleton :func:`get_knowledge_store` ensures all
  components share one connection and one ``threading.Lock``.
- Multi-row mutations use explicit ``BEGIN`` / ``COMMIT`` transactions
  with ``ROLLBACK`` on failure.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.config_store import apply_sqlite_pragmas

__all__ = [
    "KnowledgeStore",
    "get_knowledge_store",
    "reset_knowledge_store",
]

logger = logging.getLogger(__name__)

_DEFAULT_DB = "kazma-data/settings.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS knowledge_libraries (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    seed_url    TEXT NOT NULL DEFAULT '',
    auto_inject INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id             TEXT PRIMARY KEY,
    library_id     TEXT NOT NULL,
    source_url     TEXT NOT NULL,
    document_title TEXT NOT NULL DEFAULT '',
    section_header TEXT NOT NULL DEFAULT '',
    chunk_index    INTEGER NOT NULL,
    content_hash   TEXT NOT NULL,
    has_code       INTEGER NOT NULL DEFAULT 0,
    char_count     INTEGER NOT NULL DEFAULT 0,
    content        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    FOREIGN KEY (library_id) REFERENCES knowledge_libraries(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kc_library ON knowledge_chunks(library_id);
CREATE INDEX IF NOT EXISTS idx_kc_hash ON knowledge_chunks(content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kc_lib_src_idx
    ON knowledge_chunks(library_id, source_url, chunk_index);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    content,
    library_id  UNINDEXED,
    source_url  UNINDEXED,
    chunk_id    UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class KnowledgeStore:
    """SQLite-backed store for Knowledge Libraries and their chunks.

    Use :func:`get_knowledge_store` to obtain the process-wide singleton
    instead of constructing this class directly.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = Path(db_path or _DEFAULT_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            self._conn.row_factory = sqlite3.Row
            apply_sqlite_pragmas(self._conn)
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            # FK support is needed for ON DELETE CASCADE on library deletion.
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Library CRUD
    # ------------------------------------------------------------------

    def create_library(
        self,
        library_id: str,
        name: str,
        *,
        description: str = "",
        seed_url: str = "",
    ) -> dict[str, Any]:
        """Create a new library row. Raises ``sqlite3.IntegrityError`` if the
        id already exists — callers should use :meth:`get_library` first or
        switch to upsert semantics."""
        now = _now_iso()
        lib_id = (library_id or "").strip()
        if not lib_id:
            raise ValueError("library_id must not be empty")
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """INSERT INTO knowledge_libraries
                       (id, name, description, seed_url, auto_inject,
                        chunk_count, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 0, 0, ?, ?)""",
                    (lib_id, name.strip(), description.strip(), seed_url.strip(), now, now),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        logger.info("[KnowledgeStore] Created library %r", lib_id)
        return self.get_library(lib_id)  # type: ignore[return-value]

    def list_libraries(self) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT id, name, description, seed_url, auto_inject,
                          chunk_count, created_at, updated_at
                   FROM knowledge_libraries ORDER BY created_at"""
            ).fetchall()
        return [self._library_row_to_dict(r) for r in rows]

    def get_library(self, library_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT id, name, description, seed_url, auto_inject,
                          chunk_count, created_at, updated_at
                   FROM knowledge_libraries WHERE id = ?""",
                (library_id,),
            ).fetchone()
        return self._library_row_to_dict(row) if row else None

    def update_library(
        self,
        library_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        seed_url: str | None = None,
        auto_inject: bool | None = None,
    ) -> dict[str, Any] | None:
        existing = self.get_library(library_id)
        if existing is None:
            return None
        new_name = name.strip() if name is not None else existing["name"]
        new_desc = description.strip() if description is not None else existing["description"]
        new_seed = seed_url.strip() if seed_url is not None else existing["seed_url"]
        new_auto = 1 if auto_inject else 0 if auto_inject is not None else existing["auto_inject"]
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """UPDATE knowledge_libraries
                       SET name = ?, description = ?, seed_url = ?,
                           auto_inject = ?, updated_at = ?
                       WHERE id = ?""",
                    (new_name, new_desc, new_seed, int(new_auto), _now_iso(), library_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return self.get_library(library_id)

    def delete_library(self, library_id: str) -> bool:
        """Delete a library and all its chunks (FK cascade + FTS5 rows).

        Returns ``True`` if the library existed and was removed.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                # FTS5 has no FK relationship; delete explicitly.
                conn.execute(
                    "DELETE FROM knowledge_chunks_fts WHERE library_id = ?",
                    (library_id,),
                )
                cur = conn.execute(
                    "DELETE FROM knowledge_libraries WHERE id = ?", (library_id,)
                )
                conn.execute("COMMIT")
                deleted = cur.rowcount > 0
            except Exception:
                conn.execute("ROLLBACK")
                raise
        if deleted:
            logger.info("[KnowledgeStore] Deleted library %r (+ chunks via cascade)", library_id)
        return deleted

    def set_chunk_count(self, library_id: str, count: int) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    "UPDATE knowledge_libraries SET chunk_count = ?, updated_at = ? WHERE id = ?",
                    (int(count), _now_iso(), library_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def list_auto_inject_libraries(self) -> list[dict[str, Any]]:
        """Return all libraries with ``auto_inject = 1`` (Phase 2)."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT id, name, description, seed_url, auto_inject,
                          chunk_count, created_at, updated_at
                   FROM knowledge_libraries WHERE auto_inject = 1
                   ORDER BY created_at"""
            ).fetchall()
        return [self._library_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Chunk CRUD
    # ------------------------------------------------------------------

    def upsert_chunk(self, chunk: dict[str, Any]) -> bool:
        """Insert a chunk row + FTS5 copy. Dedup: if
        ``(library_id, source_url, chunk_index)`` already exists with the same
        ``content_hash``, this is a no-op (returns ``False``). If the position
        exists with a *different* hash, the old row is replaced (re-ingest of
        a changed page). Returns ``True`` if a new/changed row was written.
        """
        library_id = chunk["library_id"]
        source_url = chunk["source_url"]
        chunk_index = int(chunk["chunk_index"])
        content_hash = chunk["content_hash"]
        content = chunk["content"]
        now = _now_iso()

        with self._lock:
            conn = self._get_conn()
            existing = conn.execute(
                "SELECT id, content_hash FROM knowledge_chunks "
                "WHERE library_id = ? AND source_url = ? AND chunk_index = ?",
                (library_id, source_url, chunk_index),
            ).fetchone()
            if existing and existing["content_hash"] == content_hash:
                return False  # unchanged — skip

            chunk_id = chunk.get("id") or f"{library_id}:{content_hash[:16]}"
            try:
                conn.execute("BEGIN")
                if existing:
                    # Position exists but content changed — replace the OLD
                    # row (keyed by its existing id, which differs from the
                    # new chunk_id when the hash changed). Delete by position
                    # to be safe across id changes.
                    old_id = existing["id"]
                    conn.execute(
                        "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?", (old_id,)
                    )
                    conn.execute(
                        "DELETE FROM knowledge_chunks WHERE id = ?", (old_id,)
                    )
                conn.execute(
                    """INSERT INTO knowledge_chunks
                       (id, library_id, source_url, document_title, section_header,
                        chunk_index, content_hash, has_code, char_count, content, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        chunk_id,
                        library_id,
                        source_url,
                        chunk.get("document_title", ""),
                        chunk.get("section_header", ""),
                        chunk_index,
                        content_hash,
                        1 if chunk.get("has_code") else 0,
                        int(chunk.get("char_count", len(content))),
                        content,
                        now,
                    ),
                )
                conn.execute(
                    "INSERT INTO knowledge_chunks_fts (content, library_id, source_url, chunk_id) "
                    "VALUES (?, ?, ?, ?)",
                    (content, library_id, source_url, chunk_id),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return True

    def list_chunks(
        self, library_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT id, library_id, source_url, document_title, section_header,
                          chunk_index, content_hash, has_code, char_count, content, created_at
                   FROM knowledge_chunks WHERE library_id = ?
                   ORDER BY source_url, chunk_index
                   LIMIT ? OFFSET ?""",
                (library_id, max(1, int(limit)), max(0, int(offset))),
            ).fetchall()
        return [self._chunk_row_to_dict(r) for r in rows]

    def get_chunks_by_ids(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch full chunk rows for a set of IDs. Used at retrieval time to
        join RRF-ranked IDs back to full content + provenance."""
        if not chunk_ids:
            return {}
        # Bind a variable number of IDs safely.
        placeholders = ",".join("?" for _ in chunk_ids)
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                f"""SELECT id, library_id, source_url, document_title, section_header,
                           chunk_index, content_hash, has_code, char_count, content, created_at
                    FROM knowledge_chunks WHERE id IN ({placeholders})""",
                tuple(chunk_ids),
            ).fetchall()
        return {r["id"]: self._chunk_row_to_dict(r) for r in rows}

    def existing_hashes(self, library_id: str) -> dict[str, str]:
        """Map ``content_hash -> chunk_id`` for all chunks in a library.

        The ingest pipeline uses this to skip unchanged pages on re-ingest
        without re-reading every chunk's content.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT content_hash, id FROM knowledge_chunks WHERE library_id = ?",
                (library_id,),
            ).fetchall()
        return {r["content_hash"]: r["id"] for r in rows}

    def delete_chunks_for_source(self, library_id: str, source_url: str) -> int:
        """Remove all chunks belonging to one URL (used on page re-ingest)."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    "DELETE FROM knowledge_chunks_fts "
                    "WHERE library_id = ? AND source_url = ?",
                    (library_id, source_url),
                )
                cur = conn.execute(
                    "DELETE FROM knowledge_chunks "
                    "WHERE library_id = ? AND source_url = ?",
                    (library_id, source_url),
                )
                conn.execute("COMMIT")
                return cur.rowcount
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def count_chunks(self, library_id: str) -> int:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM knowledge_chunks WHERE library_id = ?",
                (library_id,),
            ).fetchone()
        return int(row["c"]) if row else 0

    # ── FTS5 lexical search (BM25) ───────────────────────────────────────

    def fts_search(
        self, query: str, library_id: str, *, limit: int = 20
    ) -> list[tuple[str, float]]:
        """Run a BM25 lexical search scoped to one library.

        Returns a list of ``(chunk_id, bm25_score)`` tuples.  FTS5 bm25()
        returns negative scores (more negative = more relevant); we keep the
        sign so the RRF blender sorts L3 ascending (matches adapter.py).
        """
        if not query or not query.strip():
            return []
        # Build an OR'd MATCH phrase from whitespace tokens so a multi-word
        # query matches docs containing any term (BM25 still ranks).
        terms = [t for t in query.strip().split() if t]
        if not terms:
            return []
        match_expr = " OR ".join(terms)
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT chunk_id, bm25(knowledge_chunks_fts) AS score
                       FROM knowledge_chunks_fts
                       WHERE knowledge_chunks_fts MATCH ?
                         AND library_id = ?
                       ORDER BY score
                       LIMIT ?""",
                    (match_expr, library_id, int(limit)),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                # FTS5 syntax errors (odd punctuation) → return empty rather
                # than surfacing a 500 to the agent. Semantic search still runs.
                logger.debug("[KnowledgeStore] FTS5 query failed (%s): %s", query, exc)
                return []
        out: list[tuple[str, float]] = []
        for r in rows:
            chunk_id = r["chunk_id"] if isinstance(r, sqlite3.Row) else r[0]
            score = r["score"] if isinstance(r, sqlite3.Row) else r[1]
            out.append((chunk_id, float(score)))
        return out

    # ------------------------------------------------------------------
    # Marshalling
    # ------------------------------------------------------------------

    @staticmethod
    def _library_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "seed_url": row["seed_url"],
            "auto_inject": bool(row["auto_inject"]),
            "chunk_count": int(row["chunk_count"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _chunk_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "library_id": row["library_id"],
            "source_url": row["source_url"],
            "document_title": row["document_title"],
            "section_header": row["section_header"],
            "chunk_index": int(row["chunk_index"]),
            "content_hash": row["content_hash"],
            "has_code": bool(row["has_code"]),
            "char_count": int(row["char_count"]),
            "content": row["content"],
            "created_at": row["created_at"],
        }

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# ══════════════════════════════════════════════════════════════════════════
# Process-wide singleton
# ══════════════════════════════════════════════════════════════════════════

_knowledge_store: KnowledgeStore | None = None


def get_knowledge_store() -> KnowledgeStore:
    """Return the shared :class:`KnowledgeStore` singleton.

    Lazily creates a default instance on first call.  All components must
    use this instead of constructing ``KnowledgeStore()`` directly, so they
    share one SQLite connection and one ``threading.Lock``.
    """
    global _knowledge_store
    if _knowledge_store is None:
        _knowledge_store = KnowledgeStore()
    return _knowledge_store


def reset_knowledge_store() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _knowledge_store
    if _knowledge_store is not None:
        _knowledge_store.close()
    _knowledge_store = None
