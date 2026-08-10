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
- Chunks are keyed by ``"{library_id}:{url_digest}:{content_hash[:16]}"``
  so the same nav/footer chrome on two different pages never collides on
  the SQLite primary key (a real production failure mode on Meta doc
  crawls).  Dedup of *unchanged* pages still uses
  ``(library_id, source_url, chunk_index)`` + ``content_hash``.

Concurrency model
-----------------
- WAL + ``busy_timeout=5000`` identical to ConfigStore / BookmarkStore.
- The process-wide singleton :func:`get_knowledge_store` ensures all
  components share one connection and one ``threading.Lock``.
- Multi-row mutations use explicit ``BEGIN`` / ``COMMIT`` transactions
  with ``ROLLBACK`` on failure.
"""

from __future__ import annotations

import json
import logging
import re
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
    "slugify_library_id",
]

logger = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def slugify_library_id(raw: str) -> str:
    """Normalize a user-provided library ID into a ChromaDB-safe slug.

    Library IDs become ChromaDB collection names (``kazma_kb_<id>``) and
    URL path segments, so they must be ``[a-z0-9_-]`` only.  Without this,
    a user entering ``"Meta WhatsApp Documentations 2"`` gets a fragile ID
    that breaks collection creation and URL-encodes to ``%20`` in routes.

    Examples:
      ``"Meta WhatsApp Documentations 2"`` → ``"meta_whatsapp_documentations_2"``
      ``"ShipX-API"``                       → ``"shipx-api"``
      ``"  foo / bar!!"``                   → ``"foo_bar"``
    """
    s = (raw or "").strip().lower()
    s = _SLUG_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "library"

_DEFAULT_DB = "kazma-data/settings.db"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS knowledge_libraries (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    seed_url    TEXT NOT NULL DEFAULT '',
    auto_inject INTEGER NOT NULL DEFAULT 0,
    archived    INTEGER NOT NULL DEFAULT 0,
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
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    document_id    TEXT,
    version_id     TEXT,
    source_sha256  TEXT,
    active         INTEGER NOT NULL DEFAULT 1,
    tombstoned     INTEGER NOT NULL DEFAULT 0,
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
            # Idempotent column migration: SQLite has no ADD COLUMN IF NOT
            # EXISTS, so probe PRAGMA table_info and ALTER only when the
            # column is missing. Mirrors the WorkspaceStore pattern.
            self._migrate_library_columns(conn)
            self._migrate_chunk_columns(conn)

    @staticmethod
    def _migrate_library_columns(conn: sqlite3.Connection) -> None:
        """Idempotent ALTER TABLE for knowledge_libraries columns."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_libraries)")}
        if "archived" not in existing:
            conn.execute(
                "ALTER TABLE knowledge_libraries ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
            logger.debug("[KnowledgeStore] Migrated column: archived")
        if "tenant_id" not in existing:
            conn.execute(
                "ALTER TABLE knowledge_libraries ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kl_tenant ON knowledge_libraries(tenant_id)"
            )
            logger.debug("[KnowledgeStore] Migrated column: tenant_id")

    @staticmethod
    def _migrate_chunk_columns(conn: sqlite3.Connection) -> None:
        """Add citation/version columns without rebuilding existing KB data."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(knowledge_chunks)")}
        additions = {
            "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
            "document_id": "TEXT",
            "version_id": "TEXT",
            "source_sha256": "TEXT",
            "active": "INTEGER NOT NULL DEFAULT 1",
            "tombstoned": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, definition in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN {column} {definition}")
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_kc_document
               ON knowledge_chunks(library_id, document_id, version_id, active)"""
        )

    # ------------------------------------------------------------------
    # Library CRUD
    # ------------------------------------------------------------------

    @staticmethod
    def _current_tenant() -> str:
        try:
            from kazma_core.tenant_isolation import require_tenant_id

            return require_tenant_id()
        except Exception:
            return "default"

    @staticmethod
    def _tenant_filter_enabled() -> bool:
        import os

        if (os.environ.get("KAZMA_TENANT_FILTER") or "1").strip().lower() in (
            "0", "false", "off", "no",
        ):
            return False
        try:
            from kazma_core.tenant_isolation import multi_user_or_production

            return multi_user_or_production()
        except Exception:
            return False

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
        # Library IDs become ChromaDB collection names + URL path segments,
        # so they must be slugs (lowercase [a-z0-9_-]).  Slugify whatever
        # the caller passed — this also protects against spaces/uppercase
        # that would silently break collection creation or URL-encode badly.
        lib_id = slugify_library_id(library_id)
        if not lib_id:
            raise ValueError("library_id must not be empty")
        tenant_id = self._current_tenant()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                conn.execute(
                    """INSERT INTO knowledge_libraries
                       (id, name, description, seed_url, auto_inject,
                        chunk_count, created_at, updated_at, tenant_id)
                       VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                    (
                        lib_id,
                        name.strip(),
                        description.strip(),
                        seed_url.strip(),
                        now,
                        now,
                        tenant_id,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        logger.info("[KnowledgeStore] Created library %r tenant=%s", lib_id, tenant_id)
        return self.get_library(lib_id)  # type: ignore[return-value]

    def list_libraries(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """List libraries. Active only by default; pass ``include_archived=True``
        for the archive tab. Archived libraries are excluded from the main
        list so failed/unfinished crawls don't clutter the active view, but
        stay searchable (their chunks remain in the index).

        Multi-user/production scopes to the current tenant_id.
        """
        tenant_clause = ""
        params: list[Any] = []
        if self._tenant_filter_enabled():
            tenant_clause = " AND tenant_id = ?"
            params.append(self._current_tenant())
        with self._lock:
            conn = self._get_conn()
            if include_archived:
                rows = conn.execute(
                    f"""SELECT id, name, description, seed_url, auto_inject, archived,
                              chunk_count, created_at, updated_at, tenant_id
                       FROM knowledge_libraries
                       WHERE 1=1{tenant_clause}
                       ORDER BY created_at""",
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT id, name, description, seed_url, auto_inject, archived,
                              chunk_count, created_at, updated_at, tenant_id
                       FROM knowledge_libraries WHERE archived = 0{tenant_clause}
                       ORDER BY created_at""",
                    params,
                ).fetchall()
        return [self._library_row_to_dict(r) for r in rows]

    def list_archived_libraries(self) -> list[dict[str, Any]]:
        """Return only archived libraries (for the Archived tab)."""
        tenant_clause = ""
        params: list[Any] = []
        if self._tenant_filter_enabled():
            tenant_clause = " AND tenant_id = ?"
            params.append(self._current_tenant())
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                f"""SELECT id, name, description, seed_url, auto_inject, archived,
                          chunk_count, created_at, updated_at, tenant_id
                   FROM knowledge_libraries WHERE archived = 1{tenant_clause}
                   ORDER BY updated_at DESC""",
                params,
            ).fetchall()
        return [self._library_row_to_dict(r) for r in rows]

    def archive_library(self, library_id: str, archived: bool = True) -> bool:
        """Set the archived flag on a library. Returns True if updated.

        Archiving hides a library from the main list (useful for failed or
        abandoned crawls) WITHOUT deleting its chunks — they stay searchable
        so you can still query old data. Use :meth:`delete_library` for
        permanent removal.
        """
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                cur = conn.execute(
                    "UPDATE knowledge_libraries SET archived = ?, updated_at = ? WHERE id = ?",
                    (1 if archived else 0, _now_iso(), library_id),
                )
                conn.execute("COMMIT")
                return cur.rowcount > 0
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_library(self, library_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                """SELECT id, name, description, seed_url, auto_inject, archived,
                          chunk_count, created_at, updated_at, tenant_id
                   FROM knowledge_libraries WHERE id = ?""",
                (library_id,),
            ).fetchone()
        if row is None:
            return None
        lib = self._library_row_to_dict(row)
        # Cross-tenant read deny when multi-user/prod tenant filter is on
        if self._tenant_filter_enabled():
            lib_tenant = str(lib.get("tenant_id") or "default")
            if lib_tenant != self._current_tenant():
                return None
        return lib

    def get_library_for_tenant(
        self, library_id: str, tenant_id: str
    ) -> dict[str, Any] | None:
        """Resolve a library with an explicit, non-fallback tenant boundary."""
        tenant = str(tenant_id).strip()
        if not tenant:
            raise ValueError("tenant_id must not be empty")
        with self._lock:
            row = self._get_conn().execute(
                """SELECT id, name, description, seed_url, auto_inject, archived,
                          chunk_count, created_at, updated_at, tenant_id
                   FROM knowledge_libraries WHERE id = ? AND tenant_id = ?""",
                (library_id, tenant),
            ).fetchone()
        return self._library_row_to_dict(row) if row is not None else None

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
        """Return libraries with ``auto_inject = 1``, non-archived, tenant-scoped.

        Multi-user/production must not inject another tenant's corpus into
        the system prompt. Archived libraries stay searchable via explicit
        ``knowledge_search`` but must not auto-inject.
        """
        tenant_clause = ""
        params: list[Any] = []
        if self._tenant_filter_enabled():
            tenant_clause = " AND tenant_id = ?"
            params.append(self._current_tenant())
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                f"""SELECT id, name, description, seed_url, auto_inject, archived,
                          chunk_count, created_at, updated_at, tenant_id
                   FROM knowledge_libraries
                   WHERE auto_inject = 1 AND archived = 0{tenant_clause}
                   ORDER BY created_at""",
                params,
            ).fetchall()
        return [self._library_row_to_dict(r) for r in rows]

    def list_chunk_ids_for_source(self, library_id: str, source_url: str) -> list[str]:
        """Chunk IDs for one URL (used before re-ingest purge)."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT id FROM knowledge_chunks
                   WHERE library_id = ? AND source_url = ?
                     AND active = 1 AND tombstoned = 0""",
                (library_id, source_url),
            ).fetchall()
        return [r["id"] if isinstance(r, sqlite3.Row) else r[0] for r in rows]

    def list_source_urls(self, library_id: str) -> list[str]:
        """Distinct source URLs indexed for a library (for refresh prune)."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT DISTINCT source_url FROM knowledge_chunks
                   WHERE library_id = ? AND source_url != ''
                     AND active = 1 AND tombstoned = 0
                   ORDER BY source_url""",
                (library_id,),
            ).fetchall()
        return [
            (r["source_url"] if isinstance(r, sqlite3.Row) else r[0]) or ""
            for r in rows
            if (r["source_url"] if isinstance(r, sqlite3.Row) else r[0])
        ]

    def list_source_content_hashes(
        self, library_id: str, source_url: str
    ) -> list[str]:
        """Ordered content hashes for one URL (chunk_index order).

        Used by smart re-index: if the new page produces the same hash
        sequence, skip purge/embed entirely.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT content_hash FROM knowledge_chunks
                   WHERE library_id = ? AND source_url = ?
                     AND active = 1 AND tombstoned = 0
                   ORDER BY chunk_index ASC""",
                (library_id, source_url),
            ).fetchall()
        return [
            (r["content_hash"] if isinstance(r, sqlite3.Row) else r[0]) or ""
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Chunk CRUD
    # ------------------------------------------------------------------

    def publish_document_version(
        self,
        *,
        tenant_id: str,
        library_id: str,
        document_id: str,
        version_id: str,
        source_sha256: str,
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically activate one immutable document version in a library.

        New rows and their FTS copies are built in the same SQLite transaction
        that deactivates the prior version. A failed write therefore leaves the
        prior searchable version untouched.
        """
        tenant = str(tenant_id).strip()
        document = str(document_id).strip()
        version = str(version_id).strip()
        digest = str(source_sha256).strip()
        if not all((tenant, library_id, document, version, digest)):
            raise ValueError("tenant, library, document, version, and source hash are required")
        source_url = f"document://{document}/{version}"
        ordered = sorted(chunks, key=lambda item: int(item["chunk_index"]))
        if not ordered:
            raise ValueError("at least one document chunk is required")
        if len({int(item["chunk_index"]) for item in ordered}) != len(ordered):
            raise ValueError("document chunk indices must be unique")
        for item in ordered:
            if (
                item.get("library_id") != library_id
                or item.get("source_url") != source_url
                or item.get("document_id") != document
                or item.get("version_id") != version
            ):
                raise ValueError("document chunk scope does not match publication scope")

        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                library = conn.execute(
                    "SELECT 1 FROM knowledge_libraries WHERE id = ? AND tenant_id = ?",
                    (library_id, tenant),
                ).fetchone()
                if library is None:
                    raise PermissionError("knowledge library is unavailable in this tenant")
                existing = conn.execute(
                    """SELECT id, content_hash FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ? AND version_id = ?
                         AND active = 1 AND tombstoned = 0
                       ORDER BY chunk_index""",
                    (library_id, document, version),
                ).fetchall()
                wanted = [(str(item["id"]), str(item["content_hash"])) for item in ordered]
                present = [(row["id"], row["content_hash"]) for row in existing]
                if present == wanted:
                    conn.execute("COMMIT")
                    return {
                        "published": False,
                        "new_ids": [item[0] for item in wanted],
                        "retired_ids": [],
                    }

                retired = conn.execute(
                    """SELECT id FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ?
                         AND active = 1 AND tombstoned = 0""",
                    (library_id, document),
                ).fetchall()
                retired_ids = [row["id"] for row in retired]
                if retired_ids:
                    conn.executemany(
                        "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?",
                        [(identifier,) for identifier in retired_ids],
                    )
                conn.execute(
                    """UPDATE knowledge_chunks SET active = 0
                       WHERE library_id = ? AND document_id = ? AND active = 1""",
                    (library_id, document),
                )
                stale = conn.execute(
                    """SELECT id FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ? AND version_id = ?""",
                    (library_id, document, version),
                ).fetchall()
                if stale:
                    conn.executemany(
                        "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?",
                        [(row["id"],) for row in stale],
                    )
                conn.execute(
                    """DELETE FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ? AND version_id = ?""",
                    (library_id, document, version),
                )

                for item in ordered:
                    content = str(item["content"])
                    metadata = json.dumps(
                        item.get("metadata") or {},
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    conn.execute(
                        """INSERT INTO knowledge_chunks
                           (id, library_id, source_url, document_title, section_header,
                            chunk_index, content_hash, has_code, char_count, content,
                            metadata_json, document_id, version_id, source_sha256,
                            active, tombstoned, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)""",
                        (
                            item["id"],
                            library_id,
                            source_url,
                            item.get("document_title", ""),
                            item.get("section_header", ""),
                            int(item["chunk_index"]),
                            item["content_hash"],
                            1 if item.get("has_code") else 0,
                            int(item.get("char_count", len(content))),
                            content,
                            metadata,
                            document,
                            version,
                            digest,
                            now,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO knowledge_chunks_fts
                           (content, library_id, source_url, chunk_id)
                           VALUES (?, ?, ?, ?)""",
                        (content, library_id, source_url, item["id"]),
                    )
                count_row = conn.execute(
                    """SELECT COUNT(*) AS c FROM knowledge_chunks
                       WHERE library_id = ? AND active = 1 AND tombstoned = 0""",
                    (library_id,),
                ).fetchone()
                conn.execute(
                    """UPDATE knowledge_libraries
                       SET chunk_count = ?, updated_at = ? WHERE id = ? AND tenant_id = ?""",
                    (int(count_row["c"]), now, library_id, tenant),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return {
            "published": True,
            "new_ids": [str(item["id"]) for item in ordered],
            "retired_ids": retired_ids,
        }

    def unindex_document(
        self, *, tenant_id: str, library_id: str, document_id: str
    ) -> list[str]:
        """Tombstone all searchable versions of one tenant-owned document."""
        tenant = str(tenant_id).strip()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                library = conn.execute(
                    "SELECT 1 FROM knowledge_libraries WHERE id = ? AND tenant_id = ?",
                    (library_id, tenant),
                ).fetchone()
                if library is None:
                    raise PermissionError("knowledge library is unavailable in this tenant")
                rows = conn.execute(
                    """SELECT id FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ?
                         AND active = 1 AND tombstoned = 0""",
                    (library_id, document_id),
                ).fetchall()
                identifiers = [row["id"] for row in rows]
                if identifiers:
                    conn.executemany(
                        "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?",
                        [(identifier,) for identifier in identifiers],
                    )
                conn.execute(
                    """UPDATE knowledge_chunks SET active = 0, tombstoned = 1
                       WHERE library_id = ? AND document_id = ?""",
                    (library_id, document_id),
                )
                count_row = conn.execute(
                    """SELECT COUNT(*) AS c FROM knowledge_chunks
                       WHERE library_id = ? AND active = 1 AND tombstoned = 0""",
                    (library_id,),
                ).fetchone()
                conn.execute(
                    """UPDATE knowledge_libraries
                       SET chunk_count = ?, updated_at = ? WHERE id = ? AND tenant_id = ?""",
                    (int(count_row["c"]), _now_iso(), library_id, tenant),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return identifiers

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

            # Prefer caller-supplied id (chunk_to_dict); fall back to the
            # source-url-scoped form so shared chrome across pages never
            # collides on the PRIMARY KEY.
            if chunk.get("id"):
                chunk_id = chunk["id"]
            else:
                from kazma_core.stores.knowledge_chunker import make_chunk_id

                chunk_id = make_chunk_id(library_id, source_url, content_hash)
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
                # Also clear any stale row that already owns this id under a
                # different (library, source, index) — legacy content-only
                # ids could leave orphans that block INSERT.
                conn.execute(
                    "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?", (chunk_id,)
                )
                conn.execute(
                    "DELETE FROM knowledge_chunks WHERE id = ?", (chunk_id,)
                )
                conn.execute(
                    """INSERT INTO knowledge_chunks
                       (id, library_id, source_url, document_title, section_header,
                        chunk_index, content_hash, has_code, char_count, content,
                        metadata_json, document_id, version_id, source_sha256,
                        active, tombstoned, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)""",
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
                        json.dumps(
                            chunk.get("metadata") or {},
                            ensure_ascii=False,
                            allow_nan=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        chunk.get("document_id"),
                        chunk.get("version_id"),
                        (chunk.get("metadata") or {}).get("source_sha256"),
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
        self,
        library_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        active_clause = "" if include_inactive else " AND active = 1 AND tombstoned = 0"
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT id, library_id, source_url, document_title, section_header,
                          chunk_index, content_hash, has_code, char_count, content,
                          metadata_json, document_id, version_id, source_sha256,
                          active, tombstoned, created_at
                   FROM knowledge_chunks WHERE library_id = ?"""
                + active_clause
                + """
                   ORDER BY source_url, chunk_index
                   LIMIT ? OFFSET ?""",
                (library_id, max(1, int(limit)), max(0, int(offset))),
            ).fetchall()
        return [self._chunk_row_to_dict(r) for r in rows]

    def get_chunks_by_ids(
        self, chunk_ids: list[str], *, include_inactive: bool = False
    ) -> dict[str, dict[str, Any]]:
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
                           chunk_index, content_hash, has_code, char_count, content,
                           metadata_json, document_id, version_id, source_sha256,
                           active, tombstoned, created_at
                    FROM knowledge_chunks WHERE id IN ({placeholders})
                    {" " if include_inactive else "AND active = 1 AND tombstoned = 0"}""",
                tuple(chunk_ids),
            ).fetchall()
        return {r["id"]: self._chunk_row_to_dict(r) for r in rows}

    def get_document_chunks(
        self,
        *,
        tenant_id: str,
        library_id: str,
        document_id: str,
        version_id: str | None = None,
        include_history: bool = False,
    ) -> list[dict[str, Any]]:
        """Return citation rows for an explicitly scoped document/version."""
        if self.get_library_for_tenant(library_id, tenant_id) is None:
            raise PermissionError("knowledge library is unavailable in this tenant")
        clauses = ["library_id = ?", "document_id = ?"]
        params: list[Any] = [library_id, document_id]
        if version_id is not None:
            clauses.append("version_id = ?")
            params.append(version_id)
        if not include_history:
            clauses.extend(["active = 1", "tombstoned = 0"])
        with self._lock:
            rows = self._get_conn().execute(
                f"""SELECT id, library_id, source_url, document_title, section_header,
                           chunk_index, content_hash, has_code, char_count, content,
                           metadata_json, document_id, version_id, source_sha256,
                           active, tombstoned, created_at
                    FROM knowledge_chunks WHERE {' AND '.join(clauses)}
                    ORDER BY version_id, chunk_index""",
                params,
            ).fetchall()
        return [self._chunk_row_to_dict(row) for row in rows]

    def list_document_libraries(
        self,
        *,
        tenant_id: str,
        document_id: str,
    ) -> tuple[str, ...]:
        """List tenant-owned libraries with active chunks for a document."""
        tenant = str(tenant_id).strip()
        document = str(document_id).strip()
        if not tenant or not document:
            raise ValueError("tenant_id and document_id are required")
        with self._lock:
            rows = self._get_conn().execute(
                """SELECT DISTINCT c.library_id
                   FROM knowledge_chunks c
                   JOIN knowledge_libraries l ON l.id = c.library_id
                   WHERE l.tenant_id = ? AND c.document_id = ?
                     AND c.active = 1 AND c.tombstoned = 0
                   ORDER BY c.library_id""",
                (tenant, document),
            ).fetchall()
        return tuple(row["library_id"] for row in rows)

    def existing_hashes(self, library_id: str) -> dict[str, str]:
        """Map ``content_hash -> chunk_id`` for all chunks in a library.

        The ingest pipeline uses this to skip unchanged pages on re-ingest
        without re-reading every chunk's content.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT content_hash, id FROM knowledge_chunks
                   WHERE library_id = ? AND active = 1 AND tombstoned = 0""",
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
                """SELECT COUNT(*) AS c FROM knowledge_chunks
                   WHERE library_id = ? AND active = 1 AND tombstoned = 0""",
                (library_id,),
            ).fetchone()
        return int(row["c"]) if row else 0

    # ── FTS5 lexical search (BM25) ───────────────────────────────────────

    def fts_search(
        self,
        query: str,
        library_id: str,
        *,
        limit: int = 20,
        document_id: str | None = None,
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
        # Strip FTS5 operators / punctuation that zero out the lexical layer.
        raw_terms = [t for t in query.strip().split() if t]
        terms: list[str] = []
        for t in raw_terms:
            cleaned = re.sub(r'[^\w\-]+', " ", t, flags=re.UNICODE).strip()
            for part in cleaned.split():
                if len(part) >= 2 and part.lower() not in {"or", "and", "not", "near"}:
                    # Quote tokens so colons/hyphens inside don't break FTS5.
                    safe = part.replace('"', "")
                    if safe:
                        terms.append(f'"{safe}"')
        if not terms:
            return []
        match_expr = " OR ".join(terms)
        with self._lock:
            conn = self._get_conn()
            try:
                if document_id is None:
                    rows = conn.execute(
                        """SELECT chunk_id, bm25(knowledge_chunks_fts) AS score
                           FROM knowledge_chunks_fts
                           WHERE knowledge_chunks_fts MATCH ?
                             AND library_id = ?
                           ORDER BY score
                           LIMIT ?""",
                        (match_expr, library_id, int(limit)),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """SELECT knowledge_chunks_fts.chunk_id,
                                  bm25(knowledge_chunks_fts) AS score
                           FROM knowledge_chunks_fts
                           JOIN knowledge_chunks AS c
                             ON c.id = knowledge_chunks_fts.chunk_id
                           WHERE knowledge_chunks_fts MATCH ?
                             AND knowledge_chunks_fts.library_id = ?
                             AND c.document_id = ?
                             AND c.active = 1 AND c.tombstoned = 0
                           ORDER BY score
                           LIMIT ?""",
                        (match_expr, library_id, document_id, int(limit)),
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
        keys = set(row.keys())
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "seed_url": row["seed_url"],
            "auto_inject": bool(row["auto_inject"]),
            "archived": bool(row["archived"]) if "archived" in keys else False,
            "chunk_count": int(row["chunk_count"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "tenant_id": row["tenant_id"] if "tenant_id" in keys else "default",
        }

    @staticmethod
    def _chunk_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys())
        metadata = (
            json.loads(row["metadata_json"])
            if "metadata_json" in keys and row["metadata_json"]
            else {}
        )
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
            "metadata": metadata,
            "document_id": row["document_id"] if "document_id" in keys else None,
            "version_id": row["version_id"] if "version_id" in keys else None,
            "source_sha256": row["source_sha256"] if "source_sha256" in keys else None,
            "active": bool(row["active"]) if "active" in keys else True,
            "tombstoned": bool(row["tombstoned"]) if "tombstoned" in keys else False,
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
