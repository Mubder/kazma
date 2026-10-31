"""Knowledge Library Store — SQLite-backed library + chunk persistence.

A "Knowledge Library" is a named, managed corpus of ingested documentation
(e.g. the Meta WhatsApp Cloud API docs).  Pages are fetched once, split into
hierarchy-aware chunks (see :mod:`knowledge_chunker`), and indexed for
retrieval.  This store holds the **source of truth**: full chunk content +
provenance metadata + a tokenized FTS5 copy.  Vector embeddings live in a
per-library ChromaDB collection (see :mod:`knowledge_index`); SQLite is the
durable backing store that survives ChromaDB unavailability.

The store is deliberately **decoupled** from chat memory:

- It has its own database file, ``knowledge.db`` in the data dir
  (Knowledge audit KB-E10, 2026-10-14). It used to share ``settings.db``
  with the settings store, and its 1 GiB ceiling measured that whole file:
  settings writes it did not control could grow the file past the ceiling,
  and then no Knowledge read or write could open at all. An older
  install's tables are moved here once, on the first open
  (:func:`_move_legacy_tables`).
- It never touches chat memory, and chat memory never copies it.
- A web page's chunks are keyed by library, page and POSITION
  (``"{library_id}:{url_digest}:{chunk_index}:{content_hash[:16]}"``,
  KB-E05): two identical sections on one page are two chunks.

Writes
------
- A web page is published in ONE transaction (:meth:`publish_page`,
  KB-E02): the old version or the new one, never a mix, and a failure
  raises to the caller -- it is never counted as an unchanged chunk.
- The FTS index is keyed by the chunk's rowid (KB-E06): a chunk's index
  row is written and deleted by rowid, so a write costs the same whatever
  the size of the other libraries. ``chunk_id`` stays in the index for the
  search join.
- Every connection gets every pragma, foreign keys included (KB-E13), and
  :meth:`close` holds the store lock like every statement does.

Concurrency model
-----------------
- WAL + ``busy_timeout=5000`` identical to ConfigStore / BookmarkStore.
- The process-wide singleton :func:`get_knowledge_store` ensures all
  components share one connection and one ``threading.Lock``.
- Multi-row mutations use explicit ``BEGIN`` / ``COMMIT`` transactions
  with ``ROLLBACK`` on failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from kazma_core.config_store import apply_sqlite_pragmas
from kazma_core.db.fts5_purge import drop_deleted_rows

__all__ = [
    "KB_FOLD_VERSION",
    "KnowledgeLibraryGone",
    "KnowledgeLibraryLimit",
    "KnowledgeStorageFull",
    "KnowledgeStore",
    "KnowledgeUrlRefused",
    "get_knowledge_store",
    "library_ceiling",
    "reset_knowledge_store",
    "slugify_library_id",
    "validate_web_url",
]

logger = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"[^a-z0-9_-]+")

#: The longest library id: it becomes a Chroma collection name
#: (``kazma_kb_<id>``, at most 63 characters) and a URL path segment. A
#: longer id made the library keyword-only without a word (KB-S13).
MAX_LIBRARY_ID = 48
#: The longest name and description a library keeps (KB-S13).
MAX_LIBRARY_TEXT = 500
#: The longest seed or page URL (KB-S13).
MAX_URL = 2048


class KnowledgeUrlRefused(ValueError):
    """A seed or page URL that is not an absolute http(s) URL (KB-S09)."""


class KnowledgeLibraryGone(LookupError):
    """The library a write names does not exist (deleted while a crawl ran)."""


class KnowledgeStorageFull(RuntimeError):
    """The Knowledge database reached its quota (KB-E10)."""


class KnowledgeLibraryLimit(KnowledgeStorageFull):
    """The install holds as many libraries as it may (KB-Q01): a new one is
    refused. A kind of :class:`KnowledgeStorageFull`, so every surface that
    refuses new work at the quota refuses a new library here too; the Web
    API names it ``kb_library_limit``."""

    def __init__(self, ceiling: int) -> None:
        self.ceiling = ceiling
        super().__init__(
            f"The Knowledge Library holds its limit of {ceiling} libraries; delete a library you "
            "no longer need to add another."
        )


def library_ceiling() -> int:
    """The most libraries an install may hold. Each library is one vector
    collection whose index stays loaded once used, and Chroma's index pool
    is sized so that every shard of it holds that many
    (:func:`kazma_core.memory.chroma_local.library_ceiling`): past it, two
    libraries in one shard would evict each other's index and a search
    could miss what was added since the index was last written."""
    from kazma_core.memory.chroma_local import library_ceiling as ceiling

    return ceiling()


def slugify_library_id(raw: str) -> str:
    """Normalize a user-provided library ID into a ChromaDB-safe slug.

    Library IDs become ChromaDB collection names (``kazma_kb_<id>``) and
    URL path segments, so they must be ``[a-z0-9_-]`` only and at most
    :data:`MAX_LIBRARY_ID` characters.  Without this, a user entering
    ``"Meta WhatsApp Documentations 2"`` gets a fragile ID that breaks
    collection creation and URL-encodes to ``%20`` in routes.

    Examples:
      ``"Meta WhatsApp Documentations 2"`` → ``"meta_whatsapp_documentations_2"``
      ``"ShipX-API"``                       → ``"shipx-api"``
      ``"  foo / bar!!"``                   → ``"foo_bar"``
    """
    s = (raw or "").strip().lower()
    s = _SLUG_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    s = s[:MAX_LIBRARY_ID].strip("_-")
    return s or "library"


def validate_web_url(url: str, *, allow_empty: bool = False) -> str:
    """*url* stripped, when it is an absolute http(s) URL; else
    :class:`KnowledgeUrlRefused`.

    The one rule for every URL the Knowledge Library stores -- a library's
    seed and a web page's source (KB-S09): a ``javascript:`` seed was
    stored as given and the page rendered it as a link. Control characters
    and whitespace inside the URL are refused too (a browser drops them,
    which is how ``java\\tscript:`` passes a naive check).
    """
    value = (url or "").strip()
    if not value:
        if allow_empty:
            return ""
        raise KnowledgeUrlRefused("A URL is required.")
    if len(value) > MAX_URL:
        raise KnowledgeUrlRefused(f"The URL is longer than {MAX_URL} characters.")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise KnowledgeUrlRefused("The URL contains spaces or control characters.")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise KnowledgeUrlRefused("The URL could not be read.") from exc
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        raise KnowledgeUrlRefused("Only http and https URLs can be stored.")
    return value


def _default_db() -> str:
    """The Knowledge Library's own file, ``knowledge.db`` in the data dir.

    Resolved lazily, not at import: ``data_dir()`` reads the environment, and
    binding it at module import would freeze whatever was set when the first
    importer happened to load this module.
    """
    from kazma_core.paths import knowledge_db

    return knowledge_db()


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
CREATE TABLE IF NOT EXISTS knowledge_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_vector_status (
    library_id  TEXT PRIMARY KEY,
    identity    TEXT NOT NULL DEFAULT '',
    chunks      INTEGER NOT NULL DEFAULT 0,
    covered     INTEGER NOT NULL DEFAULT 0,
    state       TEXT NOT NULL DEFAULT 'unknown',
    error       TEXT NOT NULL DEFAULT '',
    checked_at  TEXT NOT NULL,
    store_id    TEXT NOT NULL DEFAULT ''
);
"""

# The FTS index is derived data and is rebuilt in place by
# ``_ensure_fts_layout``; keeping its DDL in one constant means the rebuild
# and the initial create can never drift apart. Its rowid is the chunk's
# rowid (KB-E06): ``chunk_id`` and ``library_id`` are UNINDEXED columns, so
# a delete by them scanned the whole index -- every chunk write paid for
# every library's chunks.
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    content,
    folded,
    library_id  UNINDEXED,
    source_url  UNINDEXED,
    chunk_id    UNINDEXED,
    tokenize = "unicode61 remove_diacritics 2"
);
"""

#: The FTS layout this code writes: rows keyed by the chunk's rowid.
_FTS_LAYOUT = "rowid-1"

#: The version of :func:`kazma_core.documents.arabic.fold_for_search` the
#: index's ``folded`` column was made with. A change to the fold that is not
#: matched by a new version here leaves index-time and query-time folding
#: different -- Arabic keyword search then misses silently (KB-E11).
#: ``tests/test_knowledge_store_hardening.py`` holds the fold's output on a
#: fixed sample to this number: change one, change the other.
KB_FOLD_VERSION = 1

_SCHEMA += _FTS_SCHEMA

#: Rows deleted per transaction when a library is deleted (KB-E06): the
#: store lock is released between batches, so searches never wait for a
#: whole library.
_DELETE_BATCH = 500

#: Pages of the keyword index rewritten per transaction when a delete's
#: words are purged from it (:meth:`KnowledgeStore.purge_search_index`): the
#: store lock is released between steps.
_PURGE_STEP_PAGES = 1000

#: ``knowledge_meta`` key of the keyword-index purge a delete owes (KB-Q01):
#: written in the same transaction as the rows it deletes, cleared by the
#: purge that took their words out, so a process killed in between leaves
#: the purge to the next open or maintenance pass. Its value counts the
#: deleting transactions, so a purge never clears a later delete's mark.
_PURGE_DUE_KEY = "keyword_purge_due"

#: The keyword index's FTS5 table.
_FTS_TABLE = "knowledge_chunks_fts"

#: Test hook: called with the name of each step of a page publication, a
#: library delete and its keyword-index purge (fault injection,
#: ``tests/test_knowledge_publication.py``, ``tests/test_knowledge_store.py``).
#: None in production.
_FAULT: Callable[[str], None] | None = None


def _fault(point: str) -> None:
    if _FAULT is not None:
        _FAULT(point)


def _fold(value: str) -> str:
    """Search normal form for a chunk or query.

    Lazily imported: ``kazma_core.documents.arabic`` lives under the documents
    package, whose ``__init__`` imports modules that import *this* one. A
    module-level import would close that cycle at first use.
    """
    from kazma_core.documents.arabic import fold_for_search

    return fold_for_search(value)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


#: A library's chunk count, counted whenever a library is read. The
#: chunk_count column is a cache several writers keep, and one missed: on
#: 2026-09-26 the live Knowledge page listed a library at 365 chunks while it
#: held 366. The column is still written, for older readers.
_CHUNK_COUNT_SQL = (
    "(SELECT COUNT(*) FROM knowledge_chunks c WHERE c.library_id = knowledge_libraries.id"
    " AND c.active = 1 AND c.tombstoned = 0) AS chunk_count"
)

#: The most the Knowledge Library's own pages may hold (KB-E10). It is
#: measured on ``knowledge.db`` alone -- used pages, freed pages excluded --
#: and enforced where work is admitted: a crawl or a page is refused before
#: anything is fetched. Reads never depend on it.
_MAX_KNOWLEDGE_BYTES = 1024**3

#: Room a crawl must have left when it is admitted.
_ADMISSION_HEADROOM_BYTES = 16 * 1024**2

#: FTS errors that are the query's own syntax, not the index failing.
_FTS_SYNTAX_MARKERS = ("fts5: syntax error", "unterminated string", "unknown special query",
                       "malformed match expression")


def _is_fts_syntax_error(exc: sqlite3.OperationalError) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _FTS_SYNTAX_MARKERS)


class KnowledgeStore:
    """SQLite-backed store for Knowledge Libraries and their chunks.

    Use :func:`get_knowledge_store` to obtain the process-wide singleton
    instead of constructing this class directly.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._default = db_path is None
        self._db_path = Path(db_path or _default_db())
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        #: The last FTS storage failure (a missing, locked or corrupt index),
        #: for health; None while the index answers (KB-E11).
        self.fts_problem: str | None = None
        self._quota_warned = False
        self._init_db()
        # A delete whose process was killed before its keyword-index purge
        # finished left the purge due (KB-Q01): the next open finishes it.
        try:
            self.finish_pending_purge()
        except sqlite3.Error:
            logger.warning("[KnowledgeStore] the keyword-index purge a delete left due could not run at "
                           "open; the maintenance pass tries again", exc_info=True)

    @property
    def path(self) -> Path:
        """The database file this store reads and writes."""
        return self._db_path

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        """The store's connection, opened with EVERY pragma (KB-E13).

        ``PRAGMA foreign_keys`` was set only by ``_init_db``: a connection
        reopened after :meth:`close` had none, so a later library delete
        left its chunks behind and a re-created library of the same id
        inherited them. The quota is a write limit, never a read one
        (KB-E10): a file already past it still opens.
        """
        if self._conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            if conn.execute("PRAGMA page_count").fetchone()[0] == 0:
                # A new file reclaims freed pages on request (``compact``),
                # without VACUUM -- which would renumber the rowids the
                # keyword index is keyed by.
                conn.execute("PRAGMA auto_vacuum = INCREMENTAL")
            apply_sqlite_pragmas(conn)
            conn.execute("PRAGMA foreign_keys = ON")
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            pages = max(1, _MAX_KNOWLEDGE_BYTES // page_size)
            actual = conn.execute(f"PRAGMA max_page_count={pages}").fetchone()[0]
            if actual > pages and not self._quota_warned:
                self._quota_warned = True
                logger.warning(
                    "[KnowledgeStore] %s is larger than its %d-byte quota; reading works, "
                    "new pages are refused until libraries are deleted and the store compacted",
                    self._db_path.name, _MAX_KNOWLEDGE_BYTES,
                )
            self._conn = conn
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._get_conn()
            conn.executescript(_SCHEMA)
            # Idempotent column migration: SQLite has no ADD COLUMN IF NOT
            # EXISTS, so probe PRAGMA table_info and ALTER only when the
            # column is missing. Mirrors the WorkspaceStore pattern.
            self._migrate_library_columns(conn)
            self._migrate_chunk_columns(conn)
            self._migrate_status_columns(conn)
            if self._default:
                self._move_legacy_tables(conn)
            self._ensure_fts_layout(conn)

    # ------------------------------------------------------------------
    # Schema upkeep
    # ------------------------------------------------------------------

    @staticmethod
    def _meta(conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM knowledge_meta WHERE key = ?", (key,)).fetchone()
        return str(row[0]) if row is not None else None

    @staticmethod
    def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT INTO knowledge_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _ensure_fts_layout(self, conn: sqlite3.Connection) -> None:
        """Rebuild the keyword index once when its layout or fold changed.

        FTS5 virtual tables cannot be ALTERed, but the index is derived data
        -- ``knowledge_chunks`` is the source of truth -- so a rebuild is
        safe. It runs when the index is not keyed by rowid yet (KB-E06) or
        was folded by another version of the fold (KB-E11); both are
        recorded in ``knowledge_meta``.
        """
        if (self._meta(conn, "fts_layout") == _FTS_LAYOUT
                and self._meta(conn, "fold_version") == str(KB_FOLD_VERSION)):
            return
        self._rebuild_fts_locked(conn, reason="layout or fold version changed")

    def _rebuild_fts_locked(self, conn: sqlite3.Connection, *, reason: str) -> int | None:
        """Drop and repopulate the keyword index in ONE transaction.

        The connection runs in autocommit, so an unwrapped ``DROP TABLE``
        would commit on the spot: a failed repopulate would leave no index
        and every keyword search empty. ``BEGIN IMMEDIATE`` also takes the
        write lock first, so two processes opening the store serialize.
        Returns the number of chunks indexed, None when it could not run.
        """
        logger.info("[KnowledgeStore] Rebuilding the keyword index (%s)", reason)
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error:
            logger.warning("[KnowledgeStore] keyword index rebuild deferred (database busy)")
            return None
        try:
            conn.execute("DROP TABLE IF EXISTS knowledge_chunks_fts")
            conn.execute(_FTS_SCHEMA.strip().rstrip(";"))
            rows = conn.execute(
                "SELECT rowid, content, library_id, source_url, id FROM knowledge_chunks "
                "WHERE active = 1 AND tombstoned = 0"
            ).fetchall()
            conn.executemany(
                "INSERT INTO knowledge_chunks_fts "
                "(rowid, content, folded, library_id, source_url, chunk_id) VALUES (?, ?, ?, ?, ?, ?)",
                [(r[0], r[1], _fold(r[1] or ""), r[2], r[3], r[4]) for r in rows],
            )
            self._set_meta(conn, "fts_layout", _FTS_LAYOUT)
            self._set_meta(conn, "fold_version", str(KB_FOLD_VERSION))
            # A new index holds no deleted row's words: no purge is owed.
            conn.execute("DELETE FROM knowledge_meta WHERE key = ?", (_PURGE_DUE_KEY,))
            conn.execute("COMMIT")
            logger.info("[KnowledgeStore] keyword index rebuilt (%d chunks)", len(rows))
            self.fts_problem = None
            return len(rows)
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            logger.warning(
                "[KnowledgeStore] keyword index rebuild failed; the existing index is intact "
                "and the rebuild will be retried", exc_info=True,
            )
            return None

    def _move_legacy_tables(self, conn: sqlite3.Connection) -> None:
        """Move an older install's Knowledge tables out of ``settings.db``, once.

        Copied by explicit column names into this file's own schema (never
        a schema of the copy's), checked by count, and only then dropped
        from ``settings.db``. A copy that cannot finish leaves both files
        as they were and is tried at the next open.
        """
        from kazma_core.paths import settings_db

        legacy = Path(settings_db())
        if not legacy.exists() or legacy.resolve() == self._db_path.resolve():
            return
        try:
            probe = sqlite3.connect(f"file:{legacy.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            return
        try:
            has = probe.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_libraries'"
            ).fetchone()
        except sqlite3.Error:
            has = None
        finally:
            probe.close()
        if not has:
            return
        if self._meta(conn, "legacy_settings_moved") is None:
            if not copy_legacy_knowledge(conn, str(legacy)):
                return
            self._rebuild_fts_locked(conn, reason="moved from settings.db")
            self._set_meta(conn, "legacy_settings_moved", _now_iso())
        _drop_legacy_tables(str(legacy))

    @staticmethod
    def _migrate_library_columns(conn: sqlite3.Connection) -> None:
        """Idempotent ALTER TABLE for knowledge_libraries columns."""
        from kazma_core.db.sqlite_columns import add_missing_columns

        add_missing_columns(
            conn,
            "knowledge_libraries",
            (
                ("archived", "INTEGER NOT NULL DEFAULT 0"),
                ("tenant_id", "TEXT NOT NULL DEFAULT 'default'"),
                # The scope the owner chose for this library's crawls, which
                # its refreshes keep ('' = Kazma's default).
                ("crawl_scope", "TEXT NOT NULL DEFAULT ''"),
            ),
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kl_tenant ON knowledge_libraries(tenant_id)")

    @staticmethod
    def _migrate_chunk_columns(conn: sqlite3.Connection) -> None:
        """Add citation/version columns without rebuilding existing KB data."""
        from kazma_core.db.sqlite_columns import add_missing_columns

        add_missing_columns(
            conn,
            "knowledge_chunks",
            (
                ("metadata_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("document_id", "TEXT"),
                ("version_id", "TEXT"),
                ("source_sha256", "TEXT"),
                ("active", "INTEGER NOT NULL DEFAULT 1"),
                ("tombstoned", "INTEGER NOT NULL DEFAULT 0"),
            ),
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_kc_document
               ON knowledge_chunks(library_id, document_id, version_id, active)"""
        )

    @staticmethod
    def _migrate_status_columns(conn: sqlite3.Connection) -> None:
        """The vector store a coverage record was measured on (KB-Q01): a
        record without one is shown as not checked until the next pass."""
        from kazma_core.db.sqlite_columns import add_missing_columns

        add_missing_columns(conn, "knowledge_vector_status", (("store_id", "TEXT NOT NULL DEFAULT ''"),))

    # ------------------------------------------------------------------
    # FTS rows, by rowid (KB-E06)
    # ------------------------------------------------------------------

    @staticmethod
    def _fts_insert(conn: sqlite3.Connection, rowid: int, content: str, library_id: str,
                    source_url: str, chunk_id: str) -> None:
        conn.execute(
            "INSERT INTO knowledge_chunks_fts (rowid, content, folded, library_id, source_url, chunk_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rowid, content, _fold(content), library_id, source_url, chunk_id),
        )

    @staticmethod
    def _fts_delete(conn: sqlite3.Connection, rows: list[tuple[int, str]]) -> None:
        """Delete the index rows of ``(rowid, chunk_id)`` pairs: a rowid
        lookup each, checked against the chunk id so a row never deletes
        another chunk's entry."""
        if rows:
            conn.executemany(
                "DELETE FROM knowledge_chunks_fts WHERE rowid = ? AND chunk_id = ?",
                [(int(r), str(c)) for r, c in rows],
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

    @staticmethod
    def _checked_text(value: str, what: str) -> str:
        text = (value or "").strip()
        if len(text) > MAX_LIBRARY_TEXT:
            raise ValueError(f"The library {what} is longer than {MAX_LIBRARY_TEXT} characters.")
        return text

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
        switch to upsert semantics. A seed that is not an http(s) URL raises
        :class:`KnowledgeUrlRefused` (KB-S09); a library past
        :func:`library_ceiling`, counted in the same write transaction,
        raises :class:`KnowledgeLibraryLimit` (KB-Q01)."""
        now = _now_iso()
        # Library IDs become ChromaDB collection names + URL path segments,
        # so they must be slugs (lowercase [a-z0-9_-]).  Slugify whatever
        # the caller passed — this also protects against spaces/uppercase
        # that would silently break collection creation or URL-encode badly.
        lib_id = slugify_library_id(library_id)
        if not lib_id:
            raise ValueError("library_id must not be empty")
        seed = validate_web_url(seed_url, allow_empty=True)
        display = self._checked_text(name, "name") or lib_id
        about = self._checked_text(description, "description")
        tenant_id = self._current_tenant()
        ceiling = library_ceiling()
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                held = conn.execute("SELECT COUNT(*) FROM knowledge_libraries").fetchone()[0]
                if held >= ceiling:
                    raise KnowledgeLibraryLimit(ceiling)
                conn.execute(
                    """INSERT INTO knowledge_libraries
                       (id, name, description, seed_url, auto_inject,
                        chunk_count, created_at, updated_at, tenant_id)
                       VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                    (lib_id, display, about, seed, now, now, tenant_id),
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        logger.info("[KnowledgeStore] Created library %r tenant=%s", lib_id, tenant_id)
        return self.get_library(lib_id)  # type: ignore[return-value]

    def list_libraries(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """List libraries. Active only by default; pass ``include_archived=True``
        for the archive tab. Archived libraries are excluded from the main
        list, from auto-inject and from searches across libraries; an
        explicit search by the library's id still reads them.

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
                              {_CHUNK_COUNT_SQL}, created_at, updated_at, tenant_id
                       FROM knowledge_libraries
                       WHERE 1=1{tenant_clause}
                       ORDER BY created_at""",
                    params,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT id, name, description, seed_url, auto_inject, archived,
                              {_CHUNK_COUNT_SQL}, created_at, updated_at, tenant_id
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
                          {_CHUNK_COUNT_SQL}, created_at, updated_at, tenant_id
                   FROM knowledge_libraries WHERE archived = 1{tenant_clause}
                   ORDER BY updated_at DESC""",
                params,
            ).fetchall()
        return [self._library_row_to_dict(r) for r in rows]

    def archive_library(self, library_id: str, archived: bool = True) -> bool:
        """Set the archived flag on a library. Returns True if updated.

        Archiving hides a library from the main list, from auto-inject and
        from searches across libraries WITHOUT deleting its chunks: an
        explicit search by its id still reads them. Use :meth:`delete_library`
        for permanent removal.
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
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def library_exists(self, library_id: str) -> bool:
        """Whether a library of this id exists, whatever its tenant (a crawl
        asks it before each page: a deleted library stops its crawl)."""
        with self._lock:
            row = self._get_conn().execute(
                "SELECT 1 FROM knowledge_libraries WHERE id = ?", (library_id,)
            ).fetchone()
        return row is not None

    def get_library(self, library_id: str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                f"""SELECT id, name, description, seed_url, auto_inject, archived,
                          {_CHUNK_COUNT_SQL}, created_at, updated_at, tenant_id
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

    def crawl_scope(self, library_id: str) -> str:
        """The crawl scope the owner chose for *library_id* ('' = the default)."""
        with self._lock:
            row = self._get_conn().execute(
                "SELECT crawl_scope FROM knowledge_libraries WHERE id = ?", (library_id,)
            ).fetchone()
        return str(row[0] or "") if row is not None else ""

    def set_crawl_scope(self, library_id: str, scope: str) -> None:
        """Remember the crawl scope chosen for *library_id*; refreshes keep it."""
        with self._lock:  # autocommit: the statement is its own transaction
            self._get_conn().execute(
                "UPDATE knowledge_libraries SET crawl_scope = ? WHERE id = ?", (scope, library_id))

    def get_library_for_tenant(
        self, library_id: str, tenant_id: str
    ) -> dict[str, Any] | None:
        """Resolve a library with an explicit, non-fallback tenant boundary."""
        tenant = str(tenant_id).strip()
        if not tenant:
            raise ValueError("tenant_id must not be empty")
        with self._lock:
            row = self._get_conn().execute(
                f"""SELECT id, name, description, seed_url, auto_inject, archived,
                          {_CHUNK_COUNT_SQL}, created_at, updated_at, tenant_id
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
        new_name = self._checked_text(name, "name") if name is not None else existing["name"]
        new_desc = (self._checked_text(description, "description")
                    if description is not None else existing["description"])
        new_seed = (validate_web_url(seed_url, allow_empty=True)
                    if seed_url is not None else existing["seed_url"])
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
                    (new_name or existing["name"], new_desc, new_seed, int(new_auto),
                     _now_iso(), library_id),
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return self.get_library(library_id)

    def delete_library(self, library_id: str) -> bool:
        """Delete a library and all its chunks, in bounded batches (KB-E06).

        Each batch deletes up to :data:`_DELETE_BATCH` chunks and their
        keyword-index rows in its own transaction, and the store lock is
        released between batches, so searches and other writers never wait
        for a whole library. The library row goes last, then the deleted
        words leave the keyword index's segments (:meth:`purge_search_index`).
        Each batch records the purge as due in its own transaction, so a
        process killed before the purge ends leaves it to the next open or
        maintenance pass (:meth:`finish_pending_purge`), and a delete that
        finds the chunks already gone still purges.
        Returns ``True`` if the library existed and was removed.
        """
        while True:
            with self._lock:
                conn = self._get_conn()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    rows = conn.execute(
                        "SELECT rowid, id FROM knowledge_chunks WHERE library_id = ? LIMIT ?",
                        (library_id, _DELETE_BATCH),
                    ).fetchall()
                    if not rows:
                        cur = conn.execute("DELETE FROM knowledge_libraries WHERE id = ?", (library_id,))
                        conn.execute("DELETE FROM knowledge_vector_status WHERE library_id = ?", (library_id,))
                        conn.execute("COMMIT")
                        deleted = cur.rowcount > 0
                        break
                    self._fts_delete(conn, [(r[0], r[1]) for r in rows])
                    conn.executemany("DELETE FROM knowledge_chunks WHERE rowid = ?", [(r[0],) for r in rows])
                    self._mark_purge_due(conn)
                    conn.execute("COMMIT")
                except Exception:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            _fault("delete_batch")
        _fault("library_row_deleted")
        if self._purge_due() is not None:
            try:
                self.purge_search_index()
            except sqlite3.Error:
                # The words are unsearchable already, and the purge stays
                # due: the next open or maintenance pass finishes it.
                logger.warning("[KnowledgeStore] the keyword index still holds the deleted library %r's "
                               "words (they are unsearchable); the next open or maintenance pass "
                               "purges them", library_id, exc_info=True)
        if deleted:
            logger.info("[KnowledgeStore] Deleted library %r and its chunks", library_id)
        return deleted

    @staticmethod
    def _mark_purge_due(conn: sqlite3.Connection) -> None:
        """Record, in the caller's transaction, that deleted rows' words are
        still in the keyword index's segments (:data:`_PURGE_DUE_KEY`)."""
        conn.execute(
            "INSERT INTO knowledge_meta (key, value) VALUES (?, '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1",
            (_PURGE_DUE_KEY,),
        )

    def _purge_due(self) -> str | None:
        """The purge mark's value, or None when no purge is owed."""
        with self._lock:
            row = self._get_conn().execute(
                "SELECT value FROM knowledge_meta WHERE key = ?", (_PURGE_DUE_KEY,)
            ).fetchone()
        return str(row[0]) if row is not None else None

    def finish_pending_purge(self) -> dict[str, Any] | None:
        """Finish a keyword-index purge a delete left due (KB-Q01): a process
        killed between a delete's commit and the end of its purge. Run at
        every open and by the maintenance pass; None when none is due."""
        if self._purge_due() is None:
            return None
        logger.info("[KnowledgeStore] finishing the keyword-index purge a delete left due")
        return self.purge_search_index()

    def purge_search_index(self) -> dict[str, Any]:
        """Rewrite the keyword index without the words of deleted rows (KB-Q01).

        FTS5 records a deleted row as a delete marker in a new segment; the
        row's words stay in the older segments of ``knowledge_chunks_fts_data``
        until a merge happens to reach them -- unsearchable, but on disk: the
        soak harness found every planted word of a deleted 10,000-chunk
        library there after the delete. This merges every segment into one
        (which drops deleted rows and their markers), in steps of
        :data:`_PURGE_STEP_PAGES` pages, each its own transaction with the
        store lock released between them, so a search or a writer never waits
        for the whole rewrite.

        A merge drops the markers only when the segment it writes is the
        oldest in the index, and when every segment sits on one level below
        the top FTS5 merges them one level up and keeps every marker, word
        included (the multi-day soak, 2026-10-15: 8 words of a library
        deleted at cycle 76). So the last transaction checks the shape the
        merge left and rebuilds the index when it is not one segment on the
        last level (:func:`kazma_core.db.fts5_purge.drop_deleted_rows`), and
        clears the purge mark -- unless a delete marked it again meanwhile.
        Returns the steps, the seconds and whether the index was rebuilt.
        """
        started = time.monotonic()
        due = self._purge_due()
        steps = 0
        pages = -_PURGE_STEP_PAGES  # negative: every segment may merge (a full merge starts)
        while True:
            with self._lock:
                conn = self._get_conn()
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    before = conn.total_changes
                    conn.execute("INSERT INTO knowledge_chunks_fts (knowledge_chunks_fts, rank) "
                                 "VALUES ('merge', ?)", (pages,))
                    # FTS5: a delta under 2 means the merge found nothing to do.
                    worked = conn.total_changes - before >= 2
                    conn.execute("COMMIT")
                except sqlite3.Error:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
            steps += 1
            _fault("purge_step")
            if not worked:
                break
            pages = _PURGE_STEP_PAGES  # positive: continue the merge that started
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                outcome = drop_deleted_rows(conn, _FTS_TABLE)
                _fault("purge_checked")
                if due is not None:
                    conn.execute("DELETE FROM knowledge_meta WHERE key = ? AND value = ?", (_PURGE_DUE_KEY, due))
                conn.execute("COMMIT")
            finally:
                if conn.in_transaction:  # anything raised: the rebuild and the mark stay as they were
                    conn.execute("ROLLBACK")
        seconds = time.monotonic() - started
        rebuilt = outcome == "rebuilt"
        logger.info("[KnowledgeStore] keyword index merged in %d step(s)%s, %.2f s", steps,
                    " and rebuilt" if rebuilt else "", seconds)
        return {"steps": steps, "seconds": round(seconds, 3), "rebuilt": rebuilt}

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
                if conn.in_transaction:
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
                          {_CHUNK_COUNT_SQL}, created_at, updated_at, tenant_id
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

    def active_chunk_sizes(self, library_id: str) -> dict[str, int]:
        """The library's searchable chunks (active, not tombstoned): id -> characters."""
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                """SELECT id, char_count FROM knowledge_chunks
                   WHERE library_id = ? AND active = 1 AND tombstoned = 0""",
                (library_id,),
            ).fetchall()
        return {str(r[0]): int(r[1] or 0) for r in rows}

    def active_chunk_ids(self, library_id: str) -> set[str]:
        """Ids of the library's searchable chunks (active, not tombstoned)."""
        return set(self.active_chunk_sizes(library_id))

    def list_source_urls(self, library_id: str) -> list[str]:
        """Distinct source URLs indexed for a library."""
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

    def library_document_ids(self, library_id: str) -> list[tuple[str, str]]:
        """``(tenant, document)`` for every document with searchable chunks in
        the library: what the Documents platform is told when the library
        is deleted (KB-E08)."""
        with self._lock:
            rows = self._get_conn().execute(
                """SELECT DISTINCT l.tenant_id, c.document_id
                   FROM knowledge_chunks c JOIN knowledge_libraries l ON l.id = c.library_id
                   WHERE c.library_id = ? AND c.document_id IS NOT NULL
                     AND c.active = 1 AND c.tombstoned = 0
                   ORDER BY c.document_id""",
                (library_id,),
            ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    # ------------------------------------------------------------------
    # Chunk CRUD
    # ------------------------------------------------------------------

    def publish_page(
        self,
        library_id: str,
        source_url: str,
        chunks: list[dict[str, Any]],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """Activate one web page's chunks in ONE transaction (KB-E02).

        The page's old rows and keyword-index rows are removed and the new
        ones written inside one ``BEGIN IMMEDIATE``, the way
        :meth:`publish_document_version` publishes a document: a failure at
        any step rolls the whole page back, so the library holds the old
        version or the new one, never a mix. It used to purge the page in
        its own transaction and then write each chunk in another, counting
        a failed write as a skipped duplicate: a crawl whose writes failed
        ended "completed" with a success toast while the old version was
        already gone.

        Each chunk's id is positional (library, page, ``chunk_index``,
        content hash; KB-E05), so two identical sections on one page are
        two chunks. A page whose stored ``(id, content_hash)`` sequence
        equals the new one is ``unchanged`` and nothing is written.

        Returns ``{"outcome": "unchanged" | "published", "new_ids": [...],
        "retired_ids": [...]}``. Raises :class:`KnowledgeLibraryGone` when
        the library no longer exists, :class:`KnowledgeUrlRefused` for a
        source that is not http(s), :class:`KnowledgeStorageFull` when the
        store is at its quota, and any SQLite error as it is.
        """
        from kazma_core.stores.knowledge_chunker import make_chunk_id

        url = validate_web_url(source_url)
        if not chunks:
            raise ValueError("a page needs at least one chunk")
        prepared: list[dict[str, Any]] = []
        for item in sorted(chunks, key=lambda c: int(c["chunk_index"])):
            content = str(item.get("content") or "")
            digest = str(item.get("content_hash") or hashlib.sha256(content.encode("utf-8")).hexdigest())
            index = int(item["chunk_index"])
            prepared.append({
                **item,
                "content": content,
                "content_hash": digest,
                "chunk_index": index,
                "id": make_chunk_id(library_id, url, digest, index),
            })
        if len({c["chunk_index"] for c in prepared}) != len(prepared):
            raise ValueError("page chunk indices must be unique")
        wanted = [(c["id"], c["content_hash"]) for c in prepared]
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            try:
                _fault("begin")
                conn.execute("BEGIN IMMEDIATE")
                if conn.execute("SELECT 1 FROM knowledge_libraries WHERE id = ?",
                                (library_id,)).fetchone() is None:
                    raise KnowledgeLibraryGone(f"knowledge library {library_id!r} no longer exists")
                _fault("library_checked")
                existing = conn.execute(
                    """SELECT rowid, id, content_hash FROM knowledge_chunks
                       WHERE library_id = ? AND source_url = ?
                       ORDER BY chunk_index""",
                    (library_id, url),
                ).fetchall()
                if not force and [(r[1], r[2]) for r in existing] == wanted:
                    conn.execute("COMMIT")
                    return {"outcome": "unchanged", "new_ids": [w[0] for w in wanted], "retired_ids": []}
                self._fts_delete(conn, [(r[0], r[1]) for r in existing])
                _fault("fts_deleted")
                conn.executemany("DELETE FROM knowledge_chunks WHERE rowid = ?", [(r[0],) for r in existing])
                _fault("rows_deleted")
                for n, item in enumerate(prepared):
                    rowid = self._insert_chunk_row(conn, item, library_id, url, now)
                    _fault(f"row_inserted:{n}")
                    self._fts_insert(conn, rowid, item["content"], library_id, url, item["id"])
                    _fault(f"fts_inserted:{n}")
                conn.execute(
                    "UPDATE knowledge_libraries SET chunk_count = "
                    "(SELECT COUNT(*) FROM knowledge_chunks WHERE library_id = ? AND active = 1 "
                    "AND tombstoned = 0), updated_at = ? WHERE id = ?",
                    (library_id, now, library_id),
                )
                _fault("count_updated")
                conn.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                if "full" in str(exc).lower():
                    raise KnowledgeStorageFull(
                        "The Knowledge database is at its quota; delete libraries to make room."
                    ) from exc
                raise
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        new_ids = [w[0] for w in wanted]
        retired = [str(r[1]) for r in existing if str(r[1]) not in set(new_ids)]
        return {"outcome": "published", "new_ids": new_ids, "retired_ids": retired,
                "chunks": prepared}

    @staticmethod
    def _insert_chunk_row(conn: sqlite3.Connection, item: dict[str, Any], library_id: str,
                          source_url: str, now: str) -> int:
        content = str(item["content"])
        metadata = item.get("metadata") or {}
        cur = conn.execute(
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
                item.get("document_title", "") or "",
                item.get("section_header", "") or "",
                int(item["chunk_index"]),
                item["content_hash"],
                1 if item.get("has_code") else 0,
                int(item.get("char_count", len(content))),
                content,
                json.dumps(metadata, ensure_ascii=False, allow_nan=False, sort_keys=True,
                           separators=(",", ":")),
                item.get("document_id"),
                item.get("version_id"),
                item.get("source_sha256") or metadata.get("source_sha256"),
                now,
            ),
        )
        return int(cur.lastrowid)

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
                    """SELECT rowid, id FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ?
                         AND active = 1 AND tombstoned = 0""",
                    (library_id, document),
                ).fetchall()
                retired_ids = [row["id"] for row in retired]
                self._fts_delete(conn, [(row[0], row[1]) for row in retired])
                conn.execute(
                    """UPDATE knowledge_chunks SET active = 0
                       WHERE library_id = ? AND document_id = ? AND active = 1""",
                    (library_id, document),
                )
                stale = conn.execute(
                    """SELECT rowid, id FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ? AND version_id = ?""",
                    (library_id, document, version),
                ).fetchall()
                self._fts_delete(conn, [(row[0], row[1]) for row in stale])
                conn.execute(
                    """DELETE FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ? AND version_id = ?""",
                    (library_id, document, version),
                )

                for item in ordered:
                    row = {**item, "document_id": document, "version_id": version,
                           "source_sha256": digest}
                    rowid = self._insert_chunk_row(conn, row, library_id, source_url, now)
                    self._fts_insert(conn, rowid, str(item["content"]), library_id, source_url,
                                     str(item["id"]))
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
                if conn.in_transaction:
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
                    """SELECT rowid, id FROM knowledge_chunks
                       WHERE library_id = ? AND document_id = ?
                         AND active = 1 AND tombstoned = 0""",
                    (library_id, document_id),
                ).fetchall()
                identifiers = [row["id"] for row in rows]
                self._fts_delete(conn, [(row[0], row[1]) for row in rows])
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
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        return identifiers

    def upsert_chunk(self, chunk: dict[str, Any]) -> bool:
        """Write ONE chunk row + its keyword-index row (tests and tools that
        hand-build a library; a crawl publishes whole pages through
        :meth:`publish_page`, ``tests/test_knowledge_publication.py``).

        Dedup: if ``(library_id, source_url, chunk_index)`` already exists
        with the same ``content_hash`` this is a no-op (returns ``False``);
        a changed chunk at that position replaces the old one. It never
        deletes a row at another position (KB-E05: the old code deleted any
        row owning the same id, which was the earlier identical section of
        the same page) -- an id another position holds is refused by the
        primary key. Returns ``True`` if a new/changed row was written.
        """
        library_id = chunk["library_id"]
        source_url = chunk["source_url"]
        chunk_index = int(chunk["chunk_index"])
        content_hash = chunk["content_hash"]
        now = _now_iso()

        with self._lock:
            conn = self._get_conn()
            existing = conn.execute(
                "SELECT rowid, id, content_hash FROM knowledge_chunks "
                "WHERE library_id = ? AND source_url = ? AND chunk_index = ?",
                (library_id, source_url, chunk_index),
            ).fetchone()
            if existing and existing["content_hash"] == content_hash:
                return False  # unchanged — skip

            if chunk.get("id"):
                chunk_id = chunk["id"]
            else:
                from kazma_core.stores.knowledge_chunker import make_chunk_id

                chunk_id = make_chunk_id(library_id, source_url, content_hash, chunk_index)
            try:
                conn.execute("BEGIN")
                if existing:
                    self._fts_delete(conn, [(existing[0], existing[1])])
                    conn.execute("DELETE FROM knowledge_chunks WHERE rowid = ?", (existing[0],))
                rowid = self._insert_chunk_row(conn, {**chunk, "id": chunk_id}, library_id,
                                               source_url, now)
                self._fts_insert(conn, rowid, str(chunk["content"]), library_id, source_url, chunk_id)
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
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

    def document_index_entries(self) -> set[tuple[str, str, str, str]]:
        """Every ``(tenant, library, document, version)`` a library holds
        active chunks for: the knowledge side of the documents index
        reconcile (``DocumentIngestionService.reconcile_index``)."""
        with self._lock:
            rows = self._get_conn().execute(
                """SELECT DISTINCT l.tenant_id, c.library_id, c.document_id, c.version_id
                   FROM knowledge_chunks c
                   JOIN knowledge_libraries l ON l.id = c.library_id
                   WHERE c.document_id IS NOT NULL AND c.active = 1 AND c.tombstoned = 0"""
            ).fetchall()
        return {
            (str(r["tenant_id"]), str(r["library_id"]), str(r["document_id"]), str(r["version_id"] or ""))
            for r in rows
        }

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
        """Remove all chunks belonging to one URL (an explicit purge)."""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN")
                rows = conn.execute(
                    "SELECT rowid, id FROM knowledge_chunks WHERE library_id = ? AND source_url = ?",
                    (library_id, source_url),
                ).fetchall()
                self._fts_delete(conn, [(r[0], r[1]) for r in rows])
                conn.executemany("DELETE FROM knowledge_chunks WHERE rowid = ?", [(r[0],) for r in rows])
                conn.execute("COMMIT")
                return len(rows)
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def all_library_ids(self) -> list[str]:
        """Every library of every tenant, oldest first.

        For system maintenance only (the meaning-vector backfill runs with no
        tenant bound); anything that answers a request uses
        :meth:`list_libraries`, which scopes to the caller's tenant.
        """
        with self._lock:
            conn = self._get_conn()
            rows = conn.execute(
                "SELECT id FROM knowledge_libraries ORDER BY created_at"
            ).fetchall()
        return [str(r["id"]) for r in rows]

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

        A query FTS5 cannot parse returns nothing (logged at DEBUG). A
        failing index -- missing, locked, corrupt -- returns nothing too,
        but is logged at WARNING and recorded in :attr:`fts_problem` for
        health (KB-E11): it used to read as "no results" at DEBUG.
        """
        if not query or not query.strip():
            return []
        # Build an OR'd MATCH phrase from whitespace tokens so a multi-word
        # query matches docs containing any term (BM25 still ranks).
        # Strip FTS5 operators / punctuation that zero out the lexical layer.
        def _tokens(value: str) -> list[str]:
            out: list[str] = []
            for raw in value.strip().split():
                cleaned = re.sub(r'[^\w\-]+', " ", raw, flags=re.UNICODE).strip()
                for part in cleaned.split():
                    if len(part) >= 2 and part.lower() not in {"or", "and", "not", "near"}:
                        # Quote tokens so colons/hyphens inside don't break FTS5.
                        safe = part.replace('"', "")
                        if safe:
                            out.append(safe)
            return out

        # Two halves, OR'd, both ranked by the same bm25 call: the raw terms
        # against ``content`` so an exact match still scores, and the
        # search-normalized terms against ``folded`` so Arabic orthographic
        # variants match at all. The fold MUST be the same function used at
        # index time — a fold applied to only one side is worse than no fold.
        terms = [f'content : "{t}"' for t in _tokens(query)]
        terms += [f'folded : "{t}"' for t in _tokens(_fold(query))]
        if not terms:
            return []
        match_expr = " OR ".join(dict.fromkeys(terms))
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
                if _is_fts_syntax_error(exc):
                    logger.debug("[KnowledgeStore] FTS5 query could not be parsed (%s): %s", query, exc)
                    return []
                if self.fts_problem != str(exc):
                    logger.warning("[KnowledgeStore] keyword index failed: %s", exc)
                self.fts_problem = str(exc)
                return []
            except sqlite3.DatabaseError as exc:  # a corrupt index
                if self.fts_problem != str(exc):
                    logger.warning("[KnowledgeStore] keyword index failed: %s", exc)
                self.fts_problem = str(exc)
                return []
        if self.fts_problem is not None:
            logger.info("[KnowledgeStore] keyword index answers again")
            self.fts_problem = None
        out: list[tuple[str, float]] = []
        for r in rows:
            chunk_id = r["chunk_id"] if isinstance(r, sqlite3.Row) else r[0]
            score = r["score"] if isinstance(r, sqlite3.Row) else r[1]
            out.append((chunk_id, float(score)))
        return out

    def fts_coverage(self) -> dict[str, Any]:
        """Whether the keyword index matches the searchable chunks (KB-E11):
        ``chunks`` searchable, ``indexed`` index rows, ``missing`` chunks
        with no index row, ``stale`` index rows with no chunk behind them.
        ``ok`` False with ``error`` when the index cannot be read."""
        with self._lock:
            conn = self._get_conn()
            try:
                chunks = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE active = 1 AND tombstoned = 0"
                ).fetchone()[0]
                indexed = conn.execute("SELECT COUNT(*) FROM knowledge_chunks_fts").fetchone()[0]
                missing = conn.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks c WHERE c.active = 1 AND c.tombstoned = 0 "
                    "AND NOT EXISTS (SELECT 1 FROM knowledge_chunks_fts f WHERE f.rowid = c.rowid "
                    "AND f.chunk_id = c.id)"
                ).fetchone()[0]
            except sqlite3.DatabaseError as exc:
                return {"ok": False, "error": str(exc), "chunks": 0, "indexed": 0,
                        "missing": 0, "stale": 0}
        stale = max(0, int(indexed) - (int(chunks) - int(missing)))
        return {"ok": missing == 0 and stale == 0, "error": "", "chunks": int(chunks),
                "indexed": int(indexed), "missing": int(missing), "stale": stale}

    def rebuild_fts(self, *, reason: str = "requested") -> int | None:
        """Rebuild the keyword index from the chunks (repair, KB-E11)."""
        with self._lock:
            return self._rebuild_fts_locked(self._get_conn(), reason=reason)

    # ── storage quota (KB-E10) ───────────────────────────────────────────

    def storage_status(self) -> dict[str, Any]:
        """The store's own size: ``used_bytes`` (pages holding data),
        ``file_bytes``, ``free_bytes`` (freed pages a compaction returns),
        ``quota_bytes`` and ``full`` -- no room for a new crawl."""
        with self._lock:
            conn = self._get_conn()
            page_size = conn.execute("PRAGMA page_size").fetchone()[0]
            pages = conn.execute("PRAGMA page_count").fetchone()[0]
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        used = (pages - free) * page_size
        headroom = min(_ADMISSION_HEADROOM_BYTES, _MAX_KNOWLEDGE_BYTES // 10)
        return {"used_bytes": used, "file_bytes": pages * page_size, "free_bytes": free * page_size,
                "quota_bytes": _MAX_KNOWLEDGE_BYTES, "full": used + headroom > _MAX_KNOWLEDGE_BYTES}

    def ensure_capacity(self, library_id: str | None = None) -> None:
        """Refuse new work when the store is at its quota (raises
        :class:`KnowledgeStorageFull`), or when *library_id* names a library
        that does not exist yet and the install holds its
        :func:`library_ceiling` (raises :class:`KnowledgeLibraryLimit`):
        asked before anything is fetched. :meth:`create_library` counts
        again when it writes."""
        status = self.storage_status()
        if status["full"]:
            raise KnowledgeStorageFull(
                "The Knowledge Library is at its storage quota; delete libraries you no "
                "longer need to make room."
            )
        if library_id is None:
            return
        lib_id = slugify_library_id(library_id)
        with self._lock:
            conn = self._get_conn()
            exists = conn.execute("SELECT 1 FROM knowledge_libraries WHERE id = ?", (lib_id,)).fetchone()
            held = conn.execute("SELECT COUNT(*) FROM knowledge_libraries").fetchone()[0]
        ceiling = library_ceiling()
        if exists is None and held >= ceiling:
            raise KnowledgeLibraryLimit(ceiling)

    def compact(self) -> dict[str, Any]:
        """Return freed pages to the file system (incremental vacuum).

        Never ``VACUUM``: it renumbers the rowids the keyword index is keyed
        by. A store created before incremental vacuum was set reports
        ``compactable: False``; its freed pages are still reused by new
        writes and do not count against the quota.
        """
        with self._lock:
            conn = self._get_conn()
            mode = conn.execute("PRAGMA auto_vacuum").fetchone()[0]
            free = conn.execute("PRAGMA freelist_count").fetchone()[0]
            if mode != 2:
                return {"compactable": False, "freed_pages": 0}
            if free:
                conn.execute("PRAGMA incremental_vacuum")
            return {"compactable": True, "freed_pages": int(free)}

    # ── meaning-search coverage, written by the repair sweep (KB-E03) ───

    def set_vector_status(self, library_id: str, *, identity: str, chunks: int, covered: int,
                          state: str, error: str = "", store_id: str = "") -> None:
        """Record what the repair sweep measured, and on which vector store
        (``store_id``: a record is shown only for the store it measured)."""
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO knowledge_vector_status
                   (library_id, identity, chunks, covered, state, error, checked_at, store_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(library_id) DO UPDATE SET identity = excluded.identity,
                     chunks = excluded.chunks, covered = excluded.covered, state = excluded.state,
                     error = excluded.error, checked_at = excluded.checked_at,
                     store_id = excluded.store_id""",
                (library_id, identity, int(chunks), int(covered), state, error[:500], _now_iso(), store_id),
            )

    def vector_status(self, library_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._get_conn().execute(
                "SELECT identity, chunks, covered, state, error, checked_at, store_id "
                "FROM knowledge_vector_status WHERE library_id = ?",
                (library_id,),
            ).fetchone()
        if row is None:
            return None
        return {"identity": row[0], "chunks": int(row[1]), "covered": int(row[2]),
                "state": row[3], "error": row[4], "checked_at": row[5], "store_id": row[6] or ""}

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
        """Close the connection, holding the store lock (KB-E13): a close
        while another thread's statement was live freed SQLite's handle
        under it -- AGENTS.md §8's use-after-free. A later call reopens."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None


# ══════════════════════════════════════════════════════════════════════════
# Moving an older layout in (KB-E10, KB-E01)
# ══════════════════════════════════════════════════════════════════════════

#: The columns copied from an older Knowledge layout, by name: the store's
#: own schema decides every other column (KB-E01 -- the migration importer
#: once created the tables with its own, different columns, and a fresh
#: target could no longer open its Knowledge Library).
_LIBRARY_COLUMNS = ("id", "name", "description", "seed_url", "auto_inject", "archived",
                    "chunk_count", "created_at", "updated_at", "tenant_id")
_CHUNK_COLUMNS = ("id", "library_id", "source_url", "document_title", "section_header",
                  "chunk_index", "content_hash", "has_code", "char_count", "content",
                  "metadata_json", "document_id", "version_id", "source_sha256", "active",
                  "tombstoned", "created_at")


def copy_legacy_knowledge(conn: sqlite3.Connection, source_path: str) -> bool:
    """Copy the Knowledge tables of *source_path* into the store behind
    *conn* (its own schema, already created). One transaction; True when it
    committed. The caller rebuilds the keyword index from the copied chunks
    (``rebuild_fts``): the source's index is never copied.

    Only columns both sides have are copied, by name; a seed that is not an
    http(s) URL is dropped (KB-S09). The source is only read. Used for the
    first open after the move out of ``settings.db`` and for a migration
    bundle in the older layout.
    """
    try:
        conn.execute("ATTACH DATABASE ? AS legacy_kb", (str(source_path),))
    except sqlite3.Error:
        logger.warning("[KnowledgeStore] could not open %s to move the Knowledge tables",
                       source_path, exc_info=True)
        return False
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM legacy_kb.sqlite_master WHERE type = 'table'")}
        if "knowledge_libraries" not in tables:
            return True

        def columns(table: str, wanted: tuple[str, ...]) -> list[str]:
            have = {r[1] for r in conn.execute(f"PRAGMA legacy_kb.table_info({table})")}
            return [c for c in wanted if c in have]

        libs = columns("knowledge_libraries", _LIBRARY_COLUMNS)
        if not {"id", "name", "created_at", "updated_at"} <= set(libs):
            logger.warning("[KnowledgeStore] %s holds a Knowledge layout this version cannot "
                           "read; nothing was moved", source_path)
            return False
        chunk_cols = columns("knowledge_chunks", _CHUNK_COLUMNS) if "knowledge_chunks" in tables else []
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                f"INSERT OR IGNORE INTO knowledge_libraries ({', '.join(libs)}) "
                f"SELECT {', '.join(libs)} FROM legacy_kb.knowledge_libraries"
            )
            if "seed_url" in libs:
                bad = [r[0] for r in conn.execute(
                    "SELECT id, seed_url FROM knowledge_libraries WHERE seed_url != ''").fetchall()
                    if not _is_web_url(r[1])]
                conn.executemany("UPDATE knowledge_libraries SET seed_url = '' WHERE id = ?",
                                 [(b,) for b in bad])
            if {"id", "library_id", "source_url", "chunk_index", "content_hash", "content",
                    "created_at"} <= set(chunk_cols):
                conn.execute(
                    f"INSERT OR IGNORE INTO knowledge_chunks ({', '.join(chunk_cols)}) "
                    f"SELECT {', '.join(chunk_cols)} FROM legacy_kb.knowledge_chunks"
                )
            moved_libs = conn.execute("SELECT COUNT(*) FROM legacy_kb.knowledge_libraries").fetchone()[0]
            held_libs = conn.execute("SELECT COUNT(*) FROM knowledge_libraries").fetchone()[0]
            if held_libs < moved_libs:
                raise sqlite3.IntegrityError("not every library was copied")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    except sqlite3.Error:
        logger.warning("[KnowledgeStore] moving the Knowledge tables from %s failed; both files "
                       "are unchanged and the move is tried again at the next open",
                       source_path, exc_info=True)
        return False
    finally:
        try:
            conn.execute("DETACH DATABASE legacy_kb")
        except sqlite3.Error:
            pass
    logger.info("[KnowledgeStore] moved the Knowledge Library from %s", Path(source_path).name)
    return True


def _is_web_url(value: str) -> bool:
    try:
        validate_web_url(value)
        return True
    except KnowledgeUrlRefused:
        return False


def _drop_legacy_tables(settings_path: str) -> None:
    """Drop the moved Knowledge tables from ``settings.db``: a copy kept
    there would hold a deleted library's text for good."""
    try:
        legacy = sqlite3.connect(settings_path, timeout=5, isolation_level=None)
    except sqlite3.Error:
        return
    try:
        legacy.execute("BEGIN IMMEDIATE")
        for table in ("knowledge_chunks_fts", "knowledge_chunks", "knowledge_libraries"):
            legacy.execute(f"DROP TABLE IF EXISTS {table}")
        legacy.execute("COMMIT")
        logger.info("[KnowledgeStore] removed the moved Knowledge tables from settings.db")
    except sqlite3.Error:
        if legacy.in_transaction:
            legacy.execute("ROLLBACK")
        logger.warning("[KnowledgeStore] could not remove the moved Knowledge tables from "
                       "settings.db; tried again at the next open", exc_info=True)
    finally:
        legacy.close()


# ══════════════════════════════════════════════════════════════════════════
# Process-wide singleton
# ══════════════════════════════════════════════════════════════════════════

_knowledge_store: KnowledgeStore | None = None
_knowledge_store_init_lock = threading.Lock()


def get_knowledge_store() -> KnowledgeStore:
    """Return the shared :class:`KnowledgeStore` singleton.

    Lazily creates a default instance on first call.  All components must
    use this instead of constructing ``KnowledgeStore()`` directly, so they
    share one SQLite connection and one ``threading.Lock``. Creation is
    double-checked-locked (audit L-16): two threads racing the first call
    used to each build a store, and the loser's connection/lock diverged.
    """
    global _knowledge_store
    if _knowledge_store is None:
        with _knowledge_store_init_lock:
            if _knowledge_store is None:
                _knowledge_store = KnowledgeStore()
    return _knowledge_store


def reset_knowledge_store() -> None:
    """Drop the singleton reference (used by test teardown)."""
    global _knowledge_store
    if _knowledge_store is not None:
        _knowledge_store.close()
    _knowledge_store = None
