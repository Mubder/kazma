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

import logging
import time

# Liveness-probe cache TTL for remote vector backends' `available` property.
_READY_PROBE_TTL = 60.0
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

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
        self._ready: bool | None = None
        self._ready_at: float = 0.0

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["api-key"] = self._api_key
        return h

    @property
    def available(self) -> bool:
        # TTL-cache the liveness probe (audit finding): a once-set _ready was
        # never re-probed, so a backend that went down after a successful boot
        # probe kept reporting available forever (search then failed silently)
        # and a boot-time outage stuck until restart.
        if self._ready is not None and (time.monotonic() - self._ready_at) < _READY_PROBE_TTL:
            return self._ready
        if not self._url:
            self._ready = False
            self._ready_at = time.monotonic()
            return False
        try:
            import httpx

            with httpx.Client(timeout=self._timeout) as client:
                r = client.get(
                    f"{self._url}/collections/{self._collection}",
                    headers=self._headers(),
                )
                # 404 = collection missing but server up → still usable
                self._ready = r.status_code < 500
                self._ready_at = time.monotonic()
                return self._ready
        except Exception:
            self._ready = False
            self._ready_at = time.monotonic()
            return False

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

            point_id = hashlib.md5(item_id.encode("utf-8")).hexdigest()
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

            point_id = hashlib.md5(item_id.encode("utf-8")).hexdigest()
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
    ) -> None:
        self._dsn = dsn or ""
        self._table = "".join(c for c in (collection or "kazma_memory_vectors") if c.isalnum() or c == "_") or "kazma_memory_vectors"
        self._dim = int(dimension or 1024)
        self._timeout = max(0.5, float(timeout_s))
        self._ready: bool | None = None
        self._ready_at: float = 0.0

    def _connect(self) -> Any:
        try:
            import psycopg

            return psycopg.connect(self._dsn, connect_timeout=int(self._timeout))
        except ImportError:
            import psycopg2

            return psycopg2.connect(self._dsn, connect_timeout=int(self._timeout))

    @property
    def available(self) -> bool:
        # TTL-cache the liveness probe (audit finding): see QdrantVectorBackend.
        if self._ready is not None and (time.monotonic() - self._ready_at) < _READY_PROBE_TTL:
            return self._ready
        if not self._dsn:
            self._ready = False
            self._ready_at = time.monotonic()
            return False
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.close()
            finally:
                conn.close()
            self._ready = True
            self._ready_at = time.monotonic()
            return True
        except Exception:
            self._ready = False
            self._ready_at = time.monotonic()
            return False

    def _ensure_table(self, conn: Any) -> None:
        cur = conn.cursor()
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except Exception:
            pass
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
        try:
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._table}_hnsw
                ON {self._table}
                USING hnsw (embedding vector_cosine_ops)
                """
            )
        except Exception:
            logger.debug("[pgvector] HNSW index skipped", exc_info=True)
        conn.commit()
        cur.close()

    def search(
        self,
        query_vec: list[float] | None,
        *,
        tenant_id: str = "default",
        tier: str | list[str] | None = "recall",
        limit: int = 10,
        kind: str | None = None,
    ) -> list[tuple[str, float]]:
        if not query_vec or not self._dsn:
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
        if not item_id or not vec or not self._dsn:
            return False
        try:
            import json as _json

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
                        _json.dumps(meta or {}, ensure_ascii=False),
                    ),
                )
                conn.commit()
                cur.close()
                return True
            finally:
                conn.close()
        except Exception:
            logger.debug("[pgvector] upsert failed", exc_info=True)
            return False

    def delete(self, item_id: str, *, tenant_id: str = "default") -> bool:
        del tenant_id
        if not item_id or not self._dsn:
            return False
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(f"DELETE FROM {self._table} WHERE id = %s", (item_id,))
                conn.commit()
                cur.close()
                return True
            finally:
                conn.close()
        except Exception:
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


def _build_remote_backend(cfg: dict[str, Any]) -> Any | None:
    """Construct Qdrant or pgvector backend from config, or None."""
    vec = cfg.get("vector") or {}
    provider = str(vec.get("provider") or "").lower()
    url = str(vec.get("url") or "").strip()
    if not url:
        return None
    timeout_ms = int((cfg.get("failover") or {}).get("timeout_ms") or 5000)
    timeout_s = max(0.5, timeout_ms / 1000.0)
    dim = int(vec.get("dimension") or 1024)
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
        )
    return None


def vector_capability(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Honest capability matrix for Settings / Dashboard."""
    c = cfg or get_backends_cfg()
    provider = str((c.get("vector") or {}).get("provider") or "sqlite_vec")
    mode = c.get("mode") or "local"
    url = str((c.get("vector") or {}).get("url") or "").strip()
    if provider in _LOCAL_VECTOR or mode == "local":
        status = "full"
        write_ready = True
        search_ready = True
        detail = "Local sqlite-vec: search + write"
    elif provider in _REMOTE_VECTOR and url:
        # Remote write path is implemented; actual connectivity is separate
        status = "remote_ready"
        write_ready = True
        search_ready = True
        detail = (
            f"{provider}: search + upsert enabled (URL configured). "
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
        "embedder_provider": (c.get("embedder") or {}).get("provider") or "local",
        "failover": dict(c.get("failover") or {}),
    }


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
        # Remote providers: connectivity check only (no hard dep)
        url = (c.get("vector") or {}).get("url") or ""
        if not url:
            return {
                "ok": False,
                "error": f"{provider} requires a connection URL",
                "latency_ms": 0,
            }
        # Lightweight TCP/HTTP reachability without importing heavy clients
        import httpx

        with httpx.Client(timeout=5.0) as client:
            # Qdrant / generic health
            r = client.get(url.rstrip("/") + "/collections")
            ms = (time.perf_counter() - t0) * 1000
            return {
                "ok": r.status_code < 500,
                "provider": provider,
                "status_code": r.status_code,
                "latency_ms": round(ms, 1),
            }
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        failover = (c.get("failover") or {}).get("on_remote_error", "local")
        return {
            "ok": False,
            "error": str(exc)[:300],
            "latency_ms": round(ms, 1),
            "failover": failover,
        }
