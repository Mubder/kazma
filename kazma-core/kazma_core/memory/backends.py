"""Memory scale backends — ConfigStore-driven vector/embedder profiles.

Default on one node remains local SQLite + sqlite-vec. When the process
already has a Postgres DSN (``KAZMA_DATABASE_URL`` or
``memory.backends.state.url``), **pgvector is auto-selected** as the dense
search engine (hybrid dual-write, or remote-first when
``state.role=primary``). Explicit Qdrant still wins. Kill-switch:
``KAZMA_PGVECTOR=0``.

Factory re-reads ConfigStore live (mirrors ``get_proxy_provider`` / HITL).
Never break chat: remote failures fall back per ``failover.on_remote_error``.
"""

from __future__ import annotations

import functools
import importlib
import logging
import threading
import time
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Liveness-probe cache TTL for remote vector backends' `available` property.
_READY_PROBE_TTL = 60.0

#: Last probe of each remote vector store, keyed ``(provider, target)``:
#: ``(checked_at, state)``. Shared by every backend instance — they are built
#: per call, so a per-instance cache probed on every search. A probe opens a
#: connection; :func:`vector_capability` only READS this (routes call it on
#: the event loop), and :func:`probe_vector_backend` fills it at boot.
_REMOTE_VECTOR_STATE: dict[tuple[str, str], tuple[float, str]] = {}

#: Probe states in which a remote vector store takes searches and writes.
#: The others: ``missing`` (no pgvector on that Postgres), ``not_permitted``
#: (the role may not create the extension or the table),
#: ``dimension_mismatch`` (the table holds another vector size),
#: ``unauthorized`` (Qdrant refused the key), ``unreachable``.
_USABLE_REMOTE_STATES = frozenset({"installed", "installable", "reachable"})
#: Probes run on worker threads; one transition, one log line.
_REMOTE_VECTOR_LOCK = threading.Lock()

#: ``(dsn, table)`` pairs whose extension, table and index this process has
#: created or found. Dropped when an operation on the table fails, so a table
#: removed underneath (the 2026-08-14 shared-database incident) is recreated.
_PGVECTOR_TABLES_READY: set[tuple[str, str]] = set()
_PGVECTOR_INDEX_WARNED: set[tuple[str, str]] = set()

#: Read-only: the extension, whether the table exists or may be created,
#: and the existing table's vector size (``atttypmod`` is the dimension for
#: ``vector(n)``, -1 for an unsized column). Both parameters are the table.
_PGVECTOR_STATE_SQL = """
    SELECT
      EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector'),
      EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector'),
      COALESCE(has_schema_privilege(current_schema(), 'CREATE'), false),
      (SELECT a.atttypmod FROM pg_attribute a
        WHERE a.attrelid = to_regclass(%s) AND a.attname = 'embedding'
          AND NOT a.attisdropped),
      to_regclass(%s) IS NOT NULL
"""


def _remote_state_detail(provider: str, state: str, *, auto: bool = False) -> str:
    """What a probe state means for memory and what fixes it.

    Shared by the log line, Settings and the Test button, so they cannot say
    different things. Never names the URL or DSN: a DSN carries a password.
    ``auto``: Kazma picked pgvector from the DSN; the operator did not.
    """
    if state == "unchecked":
        return "configured, not checked yet (checked at boot and by every memory search)."
    if provider == "pgvector" and auto and state == "missing":
        return (
            "not used: this Postgres has no 'vector' extension, so memory vectors stay "
            "in local sqlite-vec (pgvector is picked automatically when a Postgres DSN "
            "is set). For vectors in Postgres, run an image that ships pgvector (e.g. "
            "pgvector/pgvector:pg16); KAZMA_PGVECTOR=0 skips this check."
        )
    if provider == "pgvector":
        return {
            "installed": "search + upsert enabled (the 'vector' extension is installed).",
            "installable": "search + upsert enabled (the 'vector' extension is created on first use).",
            "missing": (
                "this Postgres has no 'vector' extension, so remote vector search and "
                "writes are off and memory vectors stay in local sqlite-vec. Run a Postgres "
                "image that ships pgvector (e.g. pgvector/pgvector:pg16), or set "
                "KAZMA_PGVECTOR=0 to choose sqlite-vec on purpose."
            ),
            "not_permitted": (
                "this Postgres ships the 'vector' extension but Kazma's database role may "
                "not create the extension or its vector table, so remote vector search and "
                "writes are off. As a superuser, run CREATE EXTENSION vector; in this "
                "database and grant the role CREATE on its schema (Kazma re-checks within "
                "a minute), or set KAZMA_PGVECTOR=0."
            ),
            "dimension_mismatch": (
                "the vector table holds a different vector size than the embedder makes, "
                "so remote vector search and writes are off. Point "
                "memory.backends.vector.collection at a new table name and run Settings → "
                "Memory → Rebuild embeddings, or set KAZMA_PGVECTOR=0."
            ),
            "unreachable": (
                "Postgres did not answer the last check, so remote vector search and "
                "writes are off until it does."
            ),
        }.get(state, f"unexpected probe state {state!r}.")
    return {
        "reachable": "search + upsert enabled (the server answered).",
        "unauthorized": (
            "the server refused the API key, so remote vector search and writes are "
            "off until the key in Settings → Memory is fixed."
        ),
        "unreachable": (
            "the server did not answer the last check, so remote vector search and "
            "writes are off until it does."
        ),
    }.get(state, f"unexpected probe state {state!r}.")


def _record_remote_state(
    provider: str, target: str, state: str, note: str = "", *, auto: bool = False
) -> str:
    """Store a probe result, and say so when a store stops or starts working.

    A WARNING on each change into an unusable state — at boot, that is the
    one line naming a misconfigured store — and an INFO when it recovers.
    ``note`` adds specifics (never a URL or DSN). An auto-selected pgvector
    on a Postgres without the extension is the expected fallback, not a
    fault: that one is said at INFO.
    """
    key = (provider, target)
    with _REMOTE_VECTOR_LOCK:
        previous = _REMOTE_VECTOR_STATE.get(key)
        _REMOTE_VECTOR_STATE[key] = (time.monotonic(), state)
    before = previous[1] if previous else None
    if state != before:
        if state not in _USABLE_REMOTE_STATES:
            detail = _remote_state_detail(provider, state, auto=auto)
            level = logging.INFO if auto and state == "missing" else logging.WARNING
            logger.log(level, "[%s] %s%s", provider, detail, f" ({note})" if note else "")
        elif before is not None and before not in _USABLE_REMOTE_STATES:
            logger.info(
                "[%s] vector store usable again: %s",
                provider,
                _remote_state_detail(provider, state),
            )
    return state


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:200] if text else type(exc).__name__


@functools.lru_cache(maxsize=1)
def _pg_driver_errors() -> tuple[type[BaseException], ...]:
    """The ``Error`` base of each importable Postgres driver.

    Cached: a failed import is not remembered by Python, and retrying the
    one that is absent would search ``sys.path`` on every probe.
    """
    found: list[type[BaseException]] = []
    for name in ("psycopg", "psycopg2"):
        try:
            found.append(importlib.import_module(name).Error)
        except ImportError:
            continue
    return tuple(found)

__all__ = [
    "DEFAULT_BACKENDS_CFG",
    "VectorBackend",
    "LocalSqliteVectorBackend",
    "get_backends_cfg",
    "get_vector_backend",
    "vector_capability",
    "save_backends_cfg",
    "mask_backends_cfg",
    "test_embedder_backend",
    "test_vector_backend",
    "reset_backends_to_local",
    "is_sensitive_backend_key",
]

DEFAULT_BACKENDS_CFG: dict[str, Any] = {
    "mode": "local",  # local | hybrid | remote
    "vector": {
        "provider": "sqlite_vec",  # sqlite_vec | pgvector | qdrant
        "url": "",
        "api_key": "",
        "collection": "kazma_memory",
        "dimension": 1024,
    },
    "embedder": {
        "provider": "local",  # local | openai_compat
        "model": "BAAI/bge-m3",
        "base_url": "",
        "api_key": "",
        "dim": 1024,
    },
    "graph": {
        "provider": "sqlite",  # sqlite | neo4j
        "url": "",
        "user": "neo4j",
        "password": "",
        "api_key": "",
    },
    # Shared cognitive state dual-mirror (multi-replica base)
    "state": {
        "provider": "sqlite",  # sqlite | postgres
        "url": "",  # Postgres DSN when provider=postgres
        "role": "mirror",  # mirror (assist) | primary (fail-closed recall)
        "region": "",  # optional region id for #77 conflict policy
        "conflict_policy": "last_write_wins",  # last_write_wins | origin_wins | fail_closed
    },
    "failover": {
        "on_remote_error": "local",  # local | empty | raise
        "timeout_ms": 5000,
    },
}

# Local is always write-ready. Remote becomes write-ready when URL is set
# and the adapter can open (probe/upsert/search implemented for Qdrant +
# pgvector optional driver).
_LOCAL_VECTOR = frozenset({"sqlite_vec", "local", "local_sqlite"})
_REMOTE_VECTOR = frozenset({"qdrant", "pgvector"})


@runtime_checkable
class VectorBackend(Protocol):
    """Pluggable dense-vector store for memory recall (SaaS base)."""

    name: str
    write_ready: bool

    @property
    def available(self) -> bool: ...

    def search(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str = "default",
        tier: str | list[str] | None = "recall",
        limit: int = 10,
        kind: str | None = None,
    ) -> list[tuple[str, float]]: ...

    def upsert(
        self,
        item_id: str,
        vec: list[float],
        *,
        tenant_id: str = "default",
        meta: dict[str, Any] | None = None,
    ) -> bool: ...

    def delete(self, item_id: str, *, tenant_id: str = "default") -> bool: ...


class LocalSqliteVectorBackend:
    """Default VectorBackend — wraps :class:`VectorEngine` on primary DB."""

    name = "sqlite_vec"
    write_ready = True

    def __init__(self, conn: Any) -> None:
        from kazma_core.memory.vector_engine import VectorEngine

        self._engine = VectorEngine(conn)
        self._conn = conn

    @property
    def available(self) -> bool:
        return bool(self._engine.available)

    def search(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str = "default",
        tier: str | list[str] | None = "recall",
        limit: int = 10,
        kind: str | None = None,
    ) -> list[tuple[str, float]]:
        del kind  # local engine is the episodes table
        return self._engine.search(
            query_vec, tenant_id=tenant_id, tier=tier, limit=limit
        )

    def upsert(
        self,
        item_id: str,
        vec: list[float],
        *,
        tenant_id: str = "default",
        meta: dict[str, Any] | None = None,
    ) -> bool:
        """Store float32 embedding on the episode row (local path)."""
        del tenant_id, meta  # row id is global PK
        if not item_id or not vec:
            return False
        try:
            import struct

            blob = struct.pack(f"{len(vec)}f", *vec)
            self._conn.execute(
                "UPDATE episodes SET embedding=? WHERE id=?",
                (blob, item_id),
            )
            self._conn.commit()
            return True
        except Exception:
            logger.debug("[vector_backend] local upsert failed", exc_info=True)
            return False

    def delete(self, item_id: str, *, tenant_id: str = "default") -> bool:
        del tenant_id
        try:
            self._conn.execute(
                "UPDATE episodes SET embedding=NULL WHERE id=?", (item_id,)
            )
            self._conn.commit()
            return True
        except Exception:
            return False


class _EmptyVectorBackend:
    """Fail-open empty results (failover policy ``empty``)."""

    name = "empty"
    write_ready = False

    @property
    def available(self) -> bool:
        return False

    def search(self, query_vec, *, tenant_id="default", tier=None, limit=10, kind=None):
        return []

    def upsert(self, item_id, vec, *, tenant_id="default", meta=None):
        return False

    def delete(self, item_id, *, tenant_id="default"):
        return False


class QdrantVectorBackend:
    """Qdrant REST vector backend (search + upsert + delete).

    Uses httpx against the Collections/Points HTTP API. Collection is
    auto-created on first upsert when missing.
    """

    name = "qdrant"
    write_ready = True

    def __init__(
        self,
        *,
        url: str,
        api_key: str = "",
        collection: str = "kazma_memory",
        dimension: int = 1024,
        timeout_s: float = 5.0,
    ) -> None:
        self._url = (url or "").rstrip("/")
        self._api_key = api_key or ""
        self._collection = collection or "kazma_memory"
        self._dim = int(dimension or 1024)
        self._timeout = max(0.5, float(timeout_s))

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["api-key"] = self._api_key
        return h

    def probe(self, *, force: bool = False) -> str:
        """``reachable`` / ``unauthorized`` / ``unreachable``, cached 60 s per URL.

        TTL-cached (audit finding): a once-set result was never re-probed, so
        a server that went down after boot stayed "available" forever. A 401 or
        403 used to count as up (``< 500``), and every call after it failed
        on the same refused key.
        """
        import httpx

        key = ("qdrant", self._url)
        hit = _REMOTE_VECTOR_STATE.get(key)
        if not force and hit is not None and (time.monotonic() - hit[0]) < _READY_PROBE_TTL:
            return hit[1]
        state = "unreachable"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(
                    f"{self._url}/collections/{self._collection}",
                    headers=self._headers(),
                )
            # 404 = collection missing but server up → the first upsert makes it
            if r.status_code in (401, 403):
                state = "unauthorized"
            elif r.status_code < 500:
                state = "reachable"
        except (httpx.HTTPError, httpx.InvalidURL, OSError):
            logger.debug("[qdrant] probe failed", exc_info=True)
        return _record_remote_state("qdrant", self._url, state)

    @property
    def available(self) -> bool:
        return bool(self._url) and self.probe() in _USABLE_REMOTE_STATES

    def _ensure_collection(self, client: Any) -> None:
        r = client.get(
            f"{self._url}/collections/{self._collection}",
            headers=self._headers(),
        )
        if r.status_code == 200:
            return
        client.put(
            f"{self._url}/collections/{self._collection}",
            headers=self._headers(),
            json={
                "vectors": {
                    "size": self._dim,
                    "distance": "Cosine",
                }
            },
        )

    def search(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str = "default",
        tier: str | list[str] | None = "recall",
        limit: int = 10,
        kind: str | None = None,
    ) -> list[tuple[str, float]]:
        if not query_vec or not self._url:
            return []
        try:
            import httpx

            with httpx.Client(timeout=self._timeout) as client:
                body: dict[str, Any] = {
                    "vector": list(query_vec),
                    "limit": max(1, int(limit)),
                    "with_payload": True,
                }
                # Optional filter by tenant in payload
                must: list[dict[str, Any]] = [
                    {"key": "tenant_id", "match": {"value": tenant_id}}
                ]
                if isinstance(tier, (list, tuple)) and tier:
                    must.append({"key": "tier", "match": {"any": list(tier)}})
                elif tier:
                    must.append({"key": "tier", "match": {"value": str(tier)}})
                if kind:
                    must.append({"key": "kind", "match": {"value": str(kind)}})
                body["filter"] = {"must": must}
                r = client.post(
                    f"{self._url}/collections/{self._collection}/points/search",
                    headers=self._headers(),
                    json=body,
                )
                if r.status_code >= 400:
                    return []
                out: list[tuple[str, float]] = []
                for hit in (r.json() or {}).get("result") or []:
                    pid = str(hit.get("id") or "")
                    score = float(hit.get("score") or 0.0)
                    # Prefer payload episode_id if present
                    pl = hit.get("payload") or {}
                    eid = str(pl.get("episode_id") or pid)
                    if eid:
                        out.append((eid, score))
                return out
        except Exception:
            logger.debug("[qdrant] search failed", exc_info=True)
            return []

    def upsert(
        self,
        item_id: str,
        vec: list[float],
        *,
        tenant_id: str = "default",
        meta: dict[str, Any] | None = None,
    ) -> bool:
        if not item_id or not vec or not self._url:
            return False
        try:
            import httpx

            payload = {"tenant_id": tenant_id, "episode_id": item_id}
            if meta:
                payload.update({k: v for k, v in meta.items() if v is not None})
            # Qdrant point ids must be uuid or unsigned int — use hash string as uuid5-like hex
            import hashlib

            point_id = hashlib.md5(item_id.encode("utf-8"), usedforsecurity=False).hexdigest()
            # Format as UUID
            point_uuid = (
                f"{point_id[:8]}-{point_id[8:12]}-{point_id[12:16]}-"
                f"{point_id[16:20]}-{point_id[20:32]}"
            )
            with httpx.Client(timeout=self._timeout) as client:
                self._ensure_collection(client)
                r = client.put(
                    f"{self._url}/collections/{self._collection}/points"
                    "?wait=true",
                    headers=self._headers(),
                    json={
                        "points": [
                            {
                                "id": point_uuid,
                                "vector": list(vec),
                                "payload": payload,
                            }
                        ]
                    },
                )
                return r.status_code < 300
        except Exception:
            logger.debug("[qdrant] upsert failed", exc_info=True)
            return False

    def delete(self, item_id: str, *, tenant_id: str = "default") -> bool:
        del tenant_id
        if not item_id or not self._url:
            return False
        try:
            import hashlib

            import httpx

            point_id = hashlib.md5(item_id.encode("utf-8"), usedforsecurity=False).hexdigest()
            point_uuid = (
                f"{point_id[:8]}-{point_id[8:12]}-{point_id[12:16]}-"
                f"{point_id[16:20]}-{point_id[20:32]}"
            )
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self._url}/collections/{self._collection}/points/delete"
                    "?wait=true",
                    headers=self._headers(),
                    json={"points": [point_uuid]},
                )
                return r.status_code < 300
        except Exception:
            return False


class PgvectorBackend:
    """Postgres + pgvector backend (optional ``psycopg`` / ``psycopg2``).

    Expects a table (auto-created)::

        CREATE TABLE IF NOT EXISTS kazma_memory_vectors (
          id TEXT PRIMARY KEY,
          tenant_id TEXT NOT NULL,
          tier TEXT,
          embedding vector(N),
          meta JSONB
        );
    """

    name = "pgvector"
    write_ready = True

    def __init__(
        self,
        *,
        dsn: str,
        collection: str = "kazma_memory_vectors",
        dimension: int = 1024,
        timeout_s: float = 5.0,
        auto: bool = False,
    ) -> None:
        self._dsn = dsn or ""
        # Auto-selected from the DSN rather than chosen (see vector_auto).
        self._auto = auto
        self._table = "".join(c for c in (collection or "kazma_memory_vectors") if c.isalnum() or c == "_") or "kazma_memory_vectors"
        self._dim = int(dimension or 1024)
        self._timeout = max(0.5, float(timeout_s))

    def _connect(self) -> Any:
        try:
            import psycopg

            return psycopg.connect(self._dsn, connect_timeout=int(self._timeout))
        except ImportError:
            import psycopg2

            return psycopg2.connect(self._dsn, connect_timeout=int(self._timeout))

    def probe(self, *, force: bool = False) -> str:
        """Whether this Postgres can hold vectors: cached 60 s, or fresh with ``force``.

        ``installed`` / ``installable`` (``CREATE EXTENSION`` will find it) /
        ``missing`` / ``not_permitted`` (this role may not create the
        extension or the table) / ``dimension_mismatch`` (the table holds
        another vector size) / ``unreachable``. Read-only: catalog queries,
        no DDL.

        The probe used to be ``SELECT 1``, which a Postgres without pgvector
        passes. The live install ran ``postgres:16-alpine``; pgvector was
        auto-selected from the DSN, and every search, upsert and delete paid
        a connection and a refused statement, logged only at DEBUG — 142
        ``CREATE TABLE``s and 63 ``DELETE``s a week — while Settings said
        "search + upsert enabled". Recall kept working only because it fell
        back to sqlite-vec (2026-09-25).
        """
        key = ("pgvector", self._dsn)
        hit = _REMOTE_VECTOR_STATE.get(key)
        if not force and hit is not None and (time.monotonic() - hit[0]) < _READY_PROBE_TTL:
            return hit[1]
        state, note = "unreachable", ""
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(_PGVECTOR_STATE_SQL, (self._table, self._table))
                installed, installable, may_create, table_dim, table_exists = cur.fetchone()
                cur.close()
            finally:
                conn.close()
            if not installed and not installable:
                state = "missing"
            elif table_exists and table_dim not in (None, -1) and int(table_dim) != self._dim:
                # CREATE TABLE IF NOT EXISTS never alters it: every write
                # would be refused for its size.
                state = "dimension_mismatch"
                note = f"table {self._table} holds {table_dim}, the embedder makes {self._dim}"
            elif not table_exists and not may_create:
                state = "not_permitted"
                note = f"no CREATE on the schema for table {self._table}"
            else:
                state = "installed" if installed else "installable"
        except Exception:  # noqa: BLE001 — on recall's path; see below
            # Broad on purpose: ``available`` is read inside every recall, and
            # a driver can fail outside its own hierarchy (psycopg2 raises
            # UnicodeDecodeError on a localized server's messages). Any
            # failure means "not usable now"; the state says so.
            logger.debug("[pgvector] extension probe failed", exc_info=True)
        # Creating the extension already failed for this role. The catalog
        # still says "installable"; only a superuser's CREATE EXTENSION (then
        # it says "installed") changes the answer, so do not retry each probe.
        if state == "installable" and hit is not None and hit[1] == "not_permitted":
            state = "not_permitted"
        return _record_remote_state("pgvector", self._dsn, state, note, auto=self._auto)

    @property
    def available(self) -> bool:
        """True only when this Postgres has, or can create, the extension."""
        return bool(self._dsn) and self.probe() in _USABLE_REMOTE_STATES

    def _table_failed(self) -> None:
        """Forget that the table is ready: the next call re-creates what is gone."""
        _PGVECTOR_TABLES_READY.discard((self._dsn, self._table))

    def _ensure_table(self, conn: Any) -> None:
        """Extension, table and index — checked once per process per table.

        Each statement commits or rolls back on its own. In one transaction a
        refused ``CREATE EXTENSION`` (the role may not) or ``CREATE INDEX``
        (HNSW stops at 2000 dimensions) aborted the block and took the
        ``CREATE TABLE`` down with it, so every later call failed the same way.
        """
        key = (self._dsn, self._table)
        if key in _PGVECTOR_TABLES_READY:
            return
        errors = _pg_driver_errors()
        cur = conn.cursor()
        try:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                conn.commit()
            except errors:
                conn.rollback()
                # A concurrent CREATE can lose the race and still leave it there.
                cur.execute(
                    "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
                )
                if not cur.fetchone()[0]:
                    _record_remote_state(
                        "pgvector", self._dsn, "not_permitted", "CREATE EXTENSION was refused"
                    )
                    raise
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                  id TEXT PRIMARY KEY,
                  tenant_id TEXT NOT NULL,
                  tier TEXT,
                  embedding vector({self._dim}),
                  meta JSONB DEFAULT '{{}}'::jsonb
                )
                """
            )
            conn.commit()
            try:
                cur.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS {self._table}_hnsw
                    ON {self._table}
                    USING hnsw (embedding vector_cosine_ops)
                    """
                )
                conn.commit()
            except errors as exc:
                conn.rollback()
                if key not in _PGVECTOR_INDEX_WARNED:
                    _PGVECTOR_INDEX_WARNED.add(key)
                    logger.warning(
                        "[pgvector] no HNSW index on %s (%s); vector search scans the table",
                        self._table,
                        _first_line(exc),
                    )
        finally:
            cur.close()
        _PGVECTOR_TABLES_READY.add(key)

    def search(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str = "default",
        tier: str | list[str] | None = "recall",
        limit: int = 10,
        kind: str | None = None,
    ) -> list[tuple[str, float]]:
        if not query_vec or not self._dsn or not self.available:
            return []
        try:
            conn = self._connect()
            try:
                self._ensure_table(conn)
                cur = conn.cursor()
                emb = "[" + ",".join(str(float(x)) for x in query_vec) + "]"
                kind_sql = ""
                kind_params: list[Any] = []
                if kind:
                    kind_sql = " AND COALESCE(meta->>'kind', 'episode') = %s"
                    kind_params = [str(kind)]
                if isinstance(tier, (list, tuple)) and tier:
                    placeholders = ",".join(["%s"] * len(tier))
                    cur.execute(
                        f"""
                        SELECT id, 1 - (embedding <=> %s::vector) AS score
                        FROM {self._table}
                        WHERE tenant_id = %s AND tier IN ({placeholders}){kind_sql}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        [emb, tenant_id, *list(tier), *kind_params, emb, int(limit)],
                    )
                elif tier:
                    cur.execute(
                        f"""
                        SELECT id, 1 - (embedding <=> %s::vector) AS score
                        FROM {self._table}
                        WHERE tenant_id = %s AND tier = %s{kind_sql}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (emb, tenant_id, str(tier), *kind_params, emb, int(limit)),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT id, 1 - (embedding <=> %s::vector) AS score
                        FROM {self._table}
                        WHERE tenant_id = %s{kind_sql}
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (emb, tenant_id, *kind_params, emb, int(limit)),
                    )
                rows = cur.fetchall()
                cur.close()
                return [(str(r[0]), float(r[1] or 0.0)) for r in rows]
            finally:
                conn.close()
        except Exception:
            self._table_failed()
            logger.debug("[pgvector] search failed", exc_info=True)
            return []

    def upsert(
        self,
        item_id: str,
        vec: list[float],
        *,
        tenant_id: str = "default",
        meta: dict[str, Any] | None = None,
    ) -> bool:
        # The hybrid backend writes here on every upsert; on a Postgres with no
        # pgvector that was a refused CREATE TABLE each time.
        if not item_id or not vec or not self._dsn or not self.available:
            return False
        try:
            from kazma_core.db.pg_helpers import json_dumps as _pg_json

            conn = self._connect()
            try:
                self._ensure_table(conn)
                cur = conn.cursor()
                emb = "[" + ",".join(str(float(x)) for x in vec) + "]"
                tier = (meta or {}).get("tier") or "episodic"
                cur.execute(
                    f"""
                    INSERT INTO {self._table} (id, tenant_id, tier, embedding, meta)
                    VALUES (%s, %s, %s, %s::vector, %s::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                      tenant_id = EXCLUDED.tenant_id,
                      tier = EXCLUDED.tier,
                      embedding = EXCLUDED.embedding,
                      meta = EXCLUDED.meta
                    """,
                    (
                        item_id,
                        tenant_id,
                        tier,
                        emb,
                        _pg_json(meta or {}),  # NUL-free (pg_helpers.strip_nul)
                    ),
                )
                conn.commit()
                cur.close()
                return True
            finally:
                conn.close()
        except Exception:
            self._table_failed()
            logger.debug("[pgvector] upsert failed", exc_info=True)
            return False

    def delete(self, item_id: str, *, tenant_id: str = "default") -> bool:
        del tenant_id
        # No extension, no table: the DELETE could only fail (63 a week live).
        if not item_id or not self._dsn or not self.available:
            return False
        try:
            conn = self._connect()
            try:
                # Like search/upsert: the table may not exist yet on a server
                # where pgvector is installable but nothing has written.
                self._ensure_table(conn)
                cur = conn.cursor()
                cur.execute(f"DELETE FROM {self._table} WHERE id = %s", (item_id,))
                conn.commit()
                cur.close()
                return True
            finally:
                conn.close()
        except Exception:
            self._table_failed()
            logger.debug("[pgvector] delete failed", exc_info=True)
            return False


class HybridVectorBackend:
    """Search remote first (if available), always dual-write to local + remote."""

    name = "hybrid"
    write_ready = True

    def __init__(self, remote: Any, local: Any) -> None:
        self._remote = remote
        self._local = local

    @property
    def available(self) -> bool:
        return bool(
            getattr(self._remote, "available", False)
            or getattr(self._local, "available", False)
        )

    def search(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str = "default",
        tier: str | list[str] | None = "recall",
        limit: int = 10,
        kind: str | None = None,
    ) -> list[tuple[str, float]]:
        if getattr(self._remote, "available", False):
            hits = self._remote.search(
                query_vec, tenant_id=tenant_id, tier=tier, limit=limit, kind=kind
            )
            if hits:
                return hits
        # Local sqlite-vec is the episodes table — do not mix belief queries.
        if str(kind or "") == "belief":
            return []
        return self._local.search(
            query_vec, tenant_id=tenant_id, tier=tier, limit=limit, kind=kind
        )

    def upsert(
        self,
        item_id: str,
        vec: list[float],
        *,
        tenant_id: str = "default",
        meta: dict[str, Any] | None = None,
    ) -> bool:
        ok_l = self._local.upsert(item_id, vec, tenant_id=tenant_id, meta=meta)
        ok_r = False
        try:
            ok_r = self._remote.upsert(item_id, vec, tenant_id=tenant_id, meta=meta)
        except Exception:
            logger.debug("[hybrid] remote upsert failed", exc_info=True)
        return bool(ok_l or ok_r)

    def delete(self, item_id: str, *, tenant_id: str = "default") -> bool:
        a = self._local.delete(item_id, tenant_id=tenant_id)
        b = False
        try:
            b = self._remote.delete(item_id, tenant_id=tenant_id)
        except Exception:
            pass
        return bool(a or b)


def _embedding_dimension(vec: dict[str, Any]) -> int:
    """The vector size the embedder makes — the size the local sqlite-vec table uses.

    ``memory.backends.vector.dimension`` (default 1024, in no Settings form)
    used to size the remote table on its own, so an embedder of any other
    size had every pgvector write refused. It is now only the fallback.
    """
    try:
        from kazma_core.memory.embedder import get_embedding_dim

        dim = int(get_embedding_dim())
        if dim > 0:
            return dim
    except (ImportError, TypeError, ValueError):
        logger.debug("[backends] embedding dimension unreadable", exc_info=True)
    return int(vec.get("dimension") or 1024)


def _build_remote_backend(cfg: dict[str, Any]) -> Any | None:
    """Construct Qdrant or pgvector backend from config, or None."""
    vec = cfg.get("vector") or {}
    provider = str(vec.get("provider") or "").lower()
    url = str(vec.get("url") or "").strip()
    if not url:
        return None
    timeout_ms = int((cfg.get("failover") or {}).get("timeout_ms") or 5000)
    timeout_s = max(0.5, timeout_ms / 1000.0)
    dim = _embedding_dimension(vec)
    collection = str(vec.get("collection") or "kazma_memory")
    api_key = str(vec.get("api_key") or "")
    if provider == "qdrant":
        return QdrantVectorBackend(
            url=url,
            api_key=api_key,
            collection=collection,
            dimension=dim,
            timeout_s=timeout_s,
        )
    if provider == "pgvector":
        return PgvectorBackend(
            dsn=url,
            collection=collection,
            dimension=dim,
            timeout_s=timeout_s,
            auto=bool(cfg.get("vector_auto")),
        )
    return None


def vector_capability(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Honest capability matrix for Settings / Dashboard.

    A remote store is reported from its last probe, never from its URL alone
    — "search + upsert enabled (URL configured)" is what Settings said for
    weeks about a Postgres with no pgvector. Never probes: routes call this
    on the event loop. Boot and every memory search keep the probe fresh.
    """
    c = cfg or get_backends_cfg()
    provider = str((c.get("vector") or {}).get("provider") or "sqlite_vec")
    mode = c.get("mode") or "local"
    url = str((c.get("vector") or {}).get("url") or "").strip()
    remote_state: str | None = None
    # A remote provider is used even when mode was left at "local"
    # (get_vector_backend), so the provider decides, not the mode.
    if provider in _LOCAL_VECTOR:
        status = "full"
        write_ready = True
        search_ready = True
        detail = "Local sqlite-vec: search + write"
    elif provider in _REMOTE_VECTOR and url:
        hit = _REMOTE_VECTOR_STATE.get((provider, url))
        remote_state = hit[1] if hit else "unchecked"
        auto = bool(c.get("vector_auto"))
        write_ready = search_ready = remote_state in _USABLE_REMOTE_STATES
        if write_ready:
            status = "remote_ready"
        elif auto and remote_state == "missing":
            # Nobody asked for pgvector and it is not there: local sqlite-vec
            # serves every search and write, which is what "full" means.
            status = "full"
            write_ready = search_ready = True
        elif remote_state == "missing":
            status = "extension_missing"
        else:
            status = remote_state
        detail = (
            f"{provider}: {_remote_state_detail(provider, remote_state, auto=auto)} "
            f"Mode={mode}; failover={(c.get('failover') or {}).get('on_remote_error', 'local')}"
        )
    elif provider in _REMOTE_VECTOR:
        status = "needs_url"
        write_ready = False
        search_ready = False
        detail = f"{provider}: set connection URL to enable remote search/write"
    else:
        status = "unknown"
        write_ready = False
        search_ready = False
        detail = f"Unknown vector provider {provider!r}"
    return {
        "mode": mode,
        "vector_provider": provider,
        "vector_write_ready": write_ready,
        "vector_search_ready": search_ready,
        "vector_status": status,
        "vector_status_detail": detail,
        # The raw probe state (None for local stores), for callers that
        # need more than the status word.
        "vector_remote_state": remote_state,
        "embedder_provider": (c.get("embedder") or {}).get("provider") or "local",
        "failover": dict(c.get("failover") or {}),
    }


def probe_vector_backend() -> str | None:
    """Probe the configured remote vector store now; None when vectors are local.

    Blocking — call it off the event loop. Boot runs it so a store that
    cannot hold vectors is named in the log at startup, not at the first
    memory search, and Settings shows the real state from the first page.
    """
    remote = _build_remote_backend(get_backends_cfg())
    if remote is None:
        return None
    return remote.probe(force=True)


def get_vector_backend(conn: Any | None = None) -> Any:
    """Return the active VectorBackend (live ConfigStore read).

    - sqlite-vec + ``mode=local`` → LocalSqliteVectorBackend
    - ``hybrid`` → Hybrid(remote + local) when remote URL set
    - ``remote`` **or** provider ``pgvector``/``qdrant`` → remote if up,
      else failover policy (default: local sqlite-vec)
    """
    cfg = get_backends_cfg()
    provider = str((cfg.get("vector") or {}).get("provider") or "sqlite_vec").lower()
    mode = str(cfg.get("mode") or "local").lower()
    failover = str(
        (cfg.get("failover") or {}).get("on_remote_error") or "local"
    ).lower()

    def _local() -> Any:
        if conn is not None:
            return LocalSqliteVectorBackend(conn)
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        c = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        ensure_primary_schema(c)
        return LocalSqliteVectorBackend(c)

    def _failover() -> Any:
        if failover == "raise":
            raise RuntimeError(
                f"Vector provider {provider!r} unavailable (failover=raise)"
            )
        if failover == "empty":
            return _EmptyVectorBackend()
        try:
            return _local()
        except Exception:
            return _EmptyVectorBackend()

    remote = _build_remote_backend(cfg)

    if mode == "hybrid" and remote is not None:
        try:
            return HybridVectorBackend(remote, _local())
        except Exception:
            return remote if getattr(remote, "available", False) else _failover()

    # pgvector / qdrant even if mode was left at the default "local"
    if provider in _REMOTE_VECTOR:
        if remote is not None and getattr(remote, "available", False):
            return remote
        return _failover()

    if mode == "remote":
        if remote is not None and getattr(remote, "available", False):
            return remote
        return _failover()

    try:
        return _local()
    except Exception:
        logger.debug("[vector_backend] local open failed", exc_info=True)
        return _EmptyVectorBackend()

_SENSITIVE_SUFFIXES = ("api_key", "password", "token", "secret")


def is_sensitive_backend_key(key: str) -> bool:
    k = (key or "").lower()
    return any(s in k for s in _SENSITIVE_SUFFIXES)


def get_backends_cfg() -> dict[str, Any]:
    """Merged backends config: defaults ← ConfigStore ``memory.backends.*``."""
    out: dict[str, Any] = {
        "mode": DEFAULT_BACKENDS_CFG["mode"],
        "vector": dict(DEFAULT_BACKENDS_CFG["vector"]),
        "embedder": dict(DEFAULT_BACKENDS_CFG["embedder"]),
        "graph": dict(DEFAULT_BACKENDS_CFG["graph"]),
        "state": dict(DEFAULT_BACKENDS_CFG["state"]),
        "failover": dict(DEFAULT_BACKENDS_CFG["failover"]),
    }
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        mode = store.get("memory.backends.mode")
        if mode is not None:
            out["mode"] = str(mode).strip().lower() or "local"
        for section in ("vector", "embedder", "graph", "state", "failover"):
            for k, default in DEFAULT_BACKENDS_CFG[section].items():
                val = store.get(f"memory.backends.{section}.{k}")
                if val is not None:
                    out[section][k] = val
                else:
                    out[section][k] = default
        # Align with embedding.* keys when backends embedder not set
        if not store.get("memory.backends.embedder.provider"):
            emb_p = store.get("embedding.provider")
            emb_m = store.get("embedding.model")
            emb_u = store.get("embedding.base_url")
            emb_k = store.get("embedding.api_key")
            emb_d = store.get("embedding.dim")
            if emb_p is not None:
                out["embedder"]["provider"] = emb_p
            if emb_m is not None:
                out["embedder"]["model"] = emb_m
            if emb_u is not None:
                out["embedder"]["base_url"] = emb_u
            if emb_k is not None:
                out["embedder"]["api_key"] = emb_k
            if emb_d is not None:
                out["embedder"]["dim"] = emb_d
    except Exception:
        logger.debug("[backends] ConfigStore read failed", exc_info=True)
    # Normalize mode
    if out["mode"] not in ("local", "hybrid", "remote"):
        out["mode"] = "local"
    _apply_state_env_overrides(out)
    _apply_pgvector_scale_defaults(out)
    # Install / env defaults for Neo4j (fail-open: still sqlite if unset)
    _apply_neo4j_env_defaults(out)
    return out


def _postgres_memory_dsn(out: dict[str, Any]) -> str:
    """DSN for pgvector / state — Settings URL, else process Postgres URL."""
    import os

    st = out.get("state") if isinstance(out.get("state"), dict) else {}
    url = str((st or {}).get("url") or "").strip()
    if url:
        return url
    return (
        os.environ.get("KAZMA_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()


def _apply_pgvector_scale_defaults(out: dict[str, Any]) -> None:
    """When Postgres is already on, pgvector is the dense engine.

    Kill-switch ``KAZMA_PGVECTOR=0``. Explicit Qdrant is never overridden.
    ``state.role=primary`` → remote-first; otherwise hybrid dual-write.
    """
    import os

    raw = (os.environ.get("KAZMA_PGVECTOR") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return
    vec = out.get("vector")
    if not isinstance(vec, dict):
        return
    provider = str(vec.get("provider") or "sqlite_vec").strip().lower()
    if provider == "qdrant":
        return
    dsn = str(vec.get("url") or "").strip() or _postgres_memory_dsn(out)
    if not dsn:
        return
    if provider not in _LOCAL_VECTOR and provider not in ("", "pgvector"):
        return
    if provider != "pgvector":
        # Kazma chose it, not the operator: a Postgres without the extension
        # then means "stay local", not "misconfigured". Top level, so the
        # Settings form never saves it back as a choice.
        out["vector_auto"] = True
    vec["provider"] = "pgvector"
    if not str(vec.get("url") or "").strip():
        vec["url"] = dsn
    mode = str(out.get("mode") or "local").strip().lower()
    role = str((out.get("state") or {}).get("role") or "mirror").strip().lower()
    if mode == "local":
        out["mode"] = "remote" if role == "primary" else "hybrid"


def _apply_state_env_overrides(out: dict[str, Any]) -> None:
    """Live env overrides for Postgres-primary recall + conflict policy."""
    import os

    st = out.get("state")
    if not isinstance(st, dict):
        return
    role = (os.environ.get("KAZMA_MEMORY_STATE_ROLE") or "").strip().lower()
    if role in ("primary", "mirror"):
        st["role"] = role
    region = (os.environ.get("KAZMA_MEMORY_STATE_REGION") or "").strip()
    if region:
        st["region"] = region
    policy = (os.environ.get("KAZMA_MEMORY_CONFLICT_POLICY") or "").strip().lower()
    if policy in ("last_write_wins", "origin_wins", "fail_closed"):
        st["conflict_policy"] = policy
    raw_role = str(st.get("role") or "mirror").strip().lower()
    st["role"] = raw_role if raw_role in ("primary", "mirror") else "mirror"
    raw_pol = str(st.get("conflict_policy") or "last_write_wins").strip().lower()
    if raw_pol not in ("last_write_wins", "origin_wins", "fail_closed"):
        st["conflict_policy"] = "last_write_wins"


def _apply_neo4j_env_defaults(out: dict[str, Any]) -> None:
    """Prefer Neo4j when install env opts in — never require a live server.

    Triggers (any):
      - ``KAZMA_GRAPH_PROVIDER=neo4j``
      - ``KAZMA_NEO4J_URL`` / ``NEO4J_URI`` set
      - ``KAZMA_NEO4J_DEFAULT=1`` (docker-compose.neo4j profile)

    If ConfigStore already has an explicit non-sqlite graph.provider, leave it.
    Password/url from env only fill empty fields.
    """
    import os

    g = out.get("graph")
    if not isinstance(g, dict):
        return
    prov = str(g.get("provider") or "sqlite").strip().lower()
    # Respect explicit user choice of non-sqlite / non-empty remote already saved
    stored_explicit = prov not in ("sqlite", "local", "")
    env_prov = (os.environ.get("KAZMA_GRAPH_PROVIDER") or "").strip().lower()
    env_url = (
        os.environ.get("KAZMA_NEO4J_URL")
        or os.environ.get("NEO4J_URI")
        or os.environ.get("NEO4J_URL")
        or ""
    ).strip()
    want_default = (os.environ.get("KAZMA_NEO4J_DEFAULT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not stored_explicit and (env_prov == "neo4j" or env_url or want_default):
        g["provider"] = "neo4j"
        if env_url:
            g["url"] = env_url
        elif not str(g.get("url") or "").strip():
            g["url"] = "bolt://localhost:7687"
        user = (
            os.environ.get("KAZMA_NEO4J_USER")
            or os.environ.get("NEO4J_USER")
            or ""
        ).strip()
        if user:
            g["user"] = user
        elif not str(g.get("user") or "").strip():
            g["user"] = "neo4j"
        pw = (
            os.environ.get("KAZMA_NEO4J_PASSWORD")
            or os.environ.get("NEO4J_PASSWORD")
            or ""
        ).strip()
        if pw and not str(g.get("password") or "").strip():
            g["password"] = pw
        out["graph"] = g
    elif prov == "neo4j":
        # Fill missing URL from env when provider already neo4j
        if not str(g.get("url") or "").strip() and env_url:
            g["url"] = env_url
        if not str(g.get("password") or "").strip():
            pw = (
                os.environ.get("KAZMA_NEO4J_PASSWORD")
                or os.environ.get("NEO4J_PASSWORD")
                or ""
            ).strip()
            if pw:
                g["password"] = pw
        out["graph"] = g


def mask_backends_cfg(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return config with secrets masked for API responses."""
    import copy

    c = copy.deepcopy(cfg if cfg is not None else get_backends_cfg())
    for section in ("vector", "embedder", "graph", "state"):
        sec = c.get(section) or {}
        for k, v in list(sec.items()):
            if is_sensitive_backend_key(k) and v:
                sec[k] = "***"
        c[section] = sec
    return c


def save_backends_cfg(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist backends profile via batch_set. Never writes ``***`` as a secret."""
    from kazma_core.config_store import get_config_store

    store = get_config_store()
    pairs: list[tuple[str, Any]] = []
    mode = str(payload.get("mode") or "local").strip().lower()
    if mode not in ("local", "hybrid", "remote"):
        mode = "local"
    pairs.append(("memory.backends.mode", mode))

    for section in ("vector", "embedder", "graph", "state", "failover"):
        sec = payload.get(section) or {}
        if not isinstance(sec, dict):
            continue
        for k, v in sec.items():
            if is_sensitive_backend_key(str(k)):
                try:
                    from kazma_core.config_store import is_masked_secret_placeholder

                    if v is None or is_masked_secret_placeholder(v) or str(v).strip() == "":
                        continue  # keep existing secret
                except Exception:
                    if v is None or str(v).strip() in ("", "***"):
                        continue
            pairs.append((f"memory.backends.{section}.{k}", v))

    # Mirror embedder into embedding.* so get_embedder() sees the change
    emb = payload.get("embedder") or {}
    if isinstance(emb, dict):
        if emb.get("provider") is not None:
            pairs.append(("embedding.provider", emb["provider"]))
        if emb.get("model") is not None:
            pairs.append(("embedding.model", emb["model"]))
        if emb.get("base_url") is not None:
            pairs.append(("embedding.base_url", emb.get("base_url") or ""))
        if emb.get("dim") is not None:
            pairs.append(("embedding.dim", emb["dim"]))
        key = emb.get("api_key")
        if key is not None and str(key).strip() not in ("", "***"):
            pairs.append(("embedding.api_key", key))

    items = [(k, v, "memory") for k, v in pairs]
    if hasattr(store, "batch_set"):
        store.batch_set(items)
    else:
        for k, v, cat in items:
            store.set(k, v, category=cat)

    # Invalidate embedder singleton so next call rebuilds
    try:
        from kazma_core.memory.embedder import reset_embedder

        reset_embedder()
    except Exception:
        logger.debug("[backends] reset_embedder failed", exc_info=True)
    try:
        from kazma_core.memory.graph_backend import reset_graph_backend_cache

        reset_graph_backend_cache()
    except Exception:
        logger.debug("[backends] reset_graph_backend_cache failed", exc_info=True)

    return mask_backends_cfg()


def reset_backends_to_local() -> dict[str, Any]:
    """Wipe remote backend keys → local defaults."""
    return save_backends_cfg(
        {
            "mode": "local",
            "vector": dict(DEFAULT_BACKENDS_CFG["vector"]),
            "embedder": dict(DEFAULT_BACKENDS_CFG["embedder"]),
            "graph": dict(DEFAULT_BACKENDS_CFG["graph"]),
            "state": dict(DEFAULT_BACKENDS_CFG["state"]),
            "failover": dict(DEFAULT_BACKENDS_CFG["failover"]),
        }
    )


def test_embedder_backend(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe embedder: encode a short string, return latency + dim."""
    t0 = time.perf_counter()
    try:
        from kazma_core.memory.embedder import get_embedder

        emb = get_embedder()
        if emb is None:
            return {"ok": False, "error": "embedder unavailable", "latency_ms": 0}
        vec = emb.encode("kazma memory backend probe")
        ms = (time.perf_counter() - t0) * 1000
        dim = len(vec) if vec else getattr(emb, "dim", 0)
        return {
            "ok": bool(vec),
            "latency_ms": round(ms, 1),
            "dim": dim,
            "provider": (cfg or get_backends_cfg()).get("embedder", {}).get("provider"),
        }
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return {"ok": False, "error": str(exc)[:300], "latency_ms": round(ms, 1)}


def test_vector_backend(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Probe vector path: open primary DB + VectorEngine availability."""
    t0 = time.perf_counter()
    c = cfg or get_backends_cfg()
    provider = (c.get("vector") or {}).get("provider") or "sqlite_vec"
    try:
        if provider in ("sqlite_vec", "local", "local_sqlite"):
            import sqlite3

            from kazma_core.memory.schema_v2 import ensure_primary_schema
            from kazma_core.memory.vector_engine import VectorEngine
            from kazma_core.paths import primary_memory_db

            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            try:
                ensure_primary_schema(conn)
                ve = VectorEngine(conn)
                ms = (time.perf_counter() - t0) * 1000
                return {
                    "ok": True,
                    "provider": provider,
                    "available": ve.available,
                    "sqlite_vec": ve.has_sqlite_vec,
                    "numpy": ve.has_numpy,
                    "latency_ms": round(ms, 1),
                }
            finally:
                conn.close()
        # Remote providers: the backend's own probe, run fresh. This used to
        # GET "<url>/collections" for every provider, which cannot reach a
        # postgresql:// DSN, and passed Qdrant on a refused key (401 < 500).
        remote = _build_remote_backend(c)
        if remote is None:
            return {
                "ok": False,
                "error": (
                    f"{provider} requires a connection URL"
                    if provider in _REMOTE_VECTOR
                    else f"Unknown vector provider {provider!r}"
                ),
                "latency_ms": 0,
            }
        state = remote.probe(force=True)
        ms = (time.perf_counter() - t0) * 1000
        result: dict[str, Any] = {
            "ok": state in _USABLE_REMOTE_STATES,
            "provider": provider,
            "state": state,
            "latency_ms": round(ms, 1),
            # The probe just refreshed what the Settings banner reads.
            "capability": vector_capability(c),
        }
        if not result["ok"]:
            result["error"] = _remote_state_detail(provider, state)
        return result
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        failover = (c.get("failover") or {}).get("on_remote_error", "local")
        return {
            "ok": False,
            "error": str(exc)[:300],
            "latency_ms": round(ms, 1),
            "failover": failover,
        }
