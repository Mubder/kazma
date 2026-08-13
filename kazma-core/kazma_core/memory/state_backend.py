"""Optional shared state backend — multi-replica foundation (P2-2).

Default remains local SQLite (``memory_state.db``). When
``memory.backends.state.provider=postgres`` and a DSN is set, writes can
**dual-mirror** beliefs/episodes to Postgres so multiple app processes share
a durable copy for APIs / future cutover.

Chat recall still prefers SQLite FTS/dense until a full remote recall path
is cut over — this module is the **wiring base**, not a forced migration.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "StateBackend",
    "get_state_backend",
    "state_capability",
    "mirror_episode_to_state",
    "mirror_belief_to_state",
    "search_state_episodes",
    "search_state_beliefs",
]

# Singleton cache for the shared-state mirror backend (see get_state_backend).
_state_backend_cache: dict[tuple, Any] = {}
_state_backend_lock = threading.Lock()


@runtime_checkable
class StateBackend(Protocol):
    name: str
    write_ready: bool

    @property
    def available(self) -> bool: ...

    def mirror_episode(self, row: dict[str, Any]) -> bool: ...

    def mirror_belief(self, row: dict[str, Any]) -> bool: ...

    def count_episodes(self, *, tenant_id: str = "default") -> int: ...

    def count_beliefs(self, *, tenant_id: str = "default") -> int: ...

    def search_episodes(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]: ...

    def search_beliefs(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]: ...


class NullStateBackend:
    """No-op remote state (local SQLite is the only store)."""

    name = "null"
    write_ready = False

    @property
    def available(self) -> bool:
        return False

    def mirror_episode(self, row: dict[str, Any]) -> bool:
        return False

    def mirror_belief(self, row: dict[str, Any]) -> bool:
        return False

    def count_episodes(self, *, tenant_id: str = "default") -> int:
        return 0

    def count_beliefs(self, *, tenant_id: str = "default") -> int:
        return 0

    def search_episodes(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]:
        return []

    def search_beliefs(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]:
        return []


class PostgresStateBackend:
    """Postgres dual-write sink for beliefs + episodes (core columns).

    Requires ``psycopg`` or ``psycopg2``. Tables are created on first use.
    """

    name = "postgres"
    write_ready = True

    def __init__(self, dsn: str, *, timeout_s: float = 5.0) -> None:
        self._dsn = dsn or ""
        self._timeout = max(0.5, float(timeout_s))
        self._ready: bool | None = None
        self._ensured = False

    def _connect(self) -> Any:
        try:
            import psycopg

            return psycopg.connect(self._dsn, connect_timeout=int(self._timeout))
        except ImportError:
            import psycopg2

            return psycopg2.connect(self._dsn, connect_timeout=int(self._timeout))

    @property
    def available(self) -> bool:
        if self._ready is not None:
            return self._ready
        if not self._dsn:
            self._ready = False
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
            return True
        except Exception:
            self._ready = False
            return False

    def _ensure(self, conn: Any) -> None:
        if self._ensured:
            return
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kazma_episodes (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              session_id TEXT,
              turn_number INTEGER,
              user_text TEXT,
              assistant_text TEXT,
              summary_text TEXT,
              tier TEXT,
              structural_importance INTEGER,
              created_at DOUBLE PRECISION,
              metadata_json TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS kazma_beliefs (
              id TEXT PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              subject TEXT,
              predicate TEXT,
              predicate_type TEXT,
              object TEXT,
              confidence DOUBLE PRECISION,
              structural_importance INTEGER,
              source_trust_weight DOUBLE PRECISION,
              valid_from DOUBLE PRECISION,
              valid_until DOUBLE PRECISION,
              invalidated_at DOUBLE PRECISION,
              metadata_json TEXT
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_kazma_ep_tenant ON kazma_episodes(tenant_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_kazma_bel_tenant ON kazma_beliefs(tenant_id)"
        )
        conn.commit()
        cur.close()
        self._ensured = True

    def mirror_episode(self, row: dict[str, Any]) -> bool:
        if not self._dsn or not row.get("id"):
            return False
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO kazma_episodes (
                      id, tenant_id, session_id, turn_number, user_text,
                      assistant_text, summary_text, tier, structural_importance,
                      created_at, metadata_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET
                      user_text = EXCLUDED.user_text,
                      assistant_text = EXCLUDED.assistant_text,
                      summary_text = EXCLUDED.summary_text,
                      tier = EXCLUDED.tier,
                      structural_importance = EXCLUDED.structural_importance,
                      metadata_json = EXCLUDED.metadata_json
                    """,
                    (
                        row.get("id"),
                        row.get("tenant_id") or "default",
                        row.get("session_id"),
                        int(row.get("turn_number") or 0),
                        row.get("user_text"),
                        row.get("assistant_text"),
                        row.get("summary_text"),
                        row.get("tier") or "episodic",
                        int(row.get("structural_importance") or 1),
                        float(row.get("created_at") or time.time()),
                        row.get("metadata_json")
                        if isinstance(row.get("metadata_json"), str)
                        else json.dumps(row.get("metadata") or {}, ensure_ascii=False),
                    ),
                )
                conn.commit()
                cur.close()
                return True
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres episode mirror failed", exc_info=True)
            return False

    def mirror_belief(self, row: dict[str, Any]) -> bool:
        if not self._dsn or not row.get("id"):
            return False
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO kazma_beliefs (
                      id, tenant_id, subject, predicate, predicate_type, object,
                      confidence, structural_importance, source_trust_weight,
                      valid_from, valid_until, invalidated_at, metadata_json
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE SET
                      object = EXCLUDED.object,
                      confidence = EXCLUDED.confidence,
                      valid_until = EXCLUDED.valid_until,
                      invalidated_at = EXCLUDED.invalidated_at,
                      metadata_json = EXCLUDED.metadata_json
                    """,
                    (
                        row.get("id"),
                        row.get("tenant_id") or "default",
                        row.get("subject"),
                        row.get("predicate"),
                        row.get("predicate_type") or "functional",
                        row.get("object"),
                        float(row.get("confidence") or 0.5),
                        int(row.get("structural_importance") or 1),
                        float(row.get("source_trust_weight") or 1.0),
                        row.get("valid_from"),
                        row.get("valid_until"),
                        row.get("invalidated_at"),
                        row.get("metadata_json")
                        if isinstance(row.get("metadata_json"), str)
                        else json.dumps(row.get("metadata") or {}, ensure_ascii=False),
                    ),
                )
                conn.commit()
                cur.close()
                return True
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres belief mirror failed", exc_info=True)
            return False

    def count_episodes(self, *, tenant_id: str = "default") -> int:
        return self._count("kazma_episodes", tenant_id)

    def count_beliefs(self, *, tenant_id: str = "default") -> int:
        return self._count("kazma_beliefs", tenant_id)

    def search_episodes(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]:
        """ILIKE sparse search over mirrored episode text (multi-replica read)."""
        terms = [t for t in (query or "").lower().split() if len(t) >= 2][:8]
        if not terms or not self._dsn:
            return []
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                clauses = " OR ".join(
                    [
                        "(LOWER(COALESCE(user_text,'')) LIKE %s OR "
                        "LOWER(COALESCE(assistant_text,'')) LIKE %s OR "
                        "LOWER(COALESCE(summary_text,'')) LIKE %s)"
                        for _ in terms
                    ]
                )
                params: list[Any] = [tenant_id]
                for t in terms:
                    pat = f"%{t}%"
                    params.extend([pat, pat, pat])
                params.append(max(1, min(int(limit), 50)))
                cur.execute(
                    f"""
                    SELECT id, session_id, user_text, assistant_text, summary_text,
                           tier, structural_importance, created_at
                    FROM kazma_episodes
                    WHERE tenant_id = %s AND ({clauses})
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                cur.close()
                return rows
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres episode search failed", exc_info=True)
            return []

    def search_beliefs(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]:
        """ILIKE sparse search over mirrored active beliefs."""
        terms = [t for t in (query or "").lower().split() if len(t) >= 2][:8]
        if not terms or not self._dsn:
            return []
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                clauses = " OR ".join(
                    [
                        "(LOWER(COALESCE(subject,'')) LIKE %s OR "
                        "LOWER(COALESCE(predicate,'')) LIKE %s OR "
                        "LOWER(COALESCE(object,'')) LIKE %s)"
                        for _ in terms
                    ]
                )
                params: list[Any] = [tenant_id]
                for t in terms:
                    pat = f"%{t}%"
                    params.extend([pat, pat, pat])
                params.append(max(1, min(int(limit), 50)))
                cur.execute(
                    f"""
                    SELECT id, subject, predicate, object, predicate_type,
                           confidence, structural_importance, source_trust_weight,
                           valid_from
                    FROM kazma_beliefs
                    WHERE tenant_id = %s
                      AND valid_until IS NULL AND invalidated_at IS NULL
                      AND ({clauses})
                    ORDER BY (structural_importance * confidence) DESC NULLS LAST
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                cur.close()
                return rows
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres belief search failed", exc_info=True)
            return []

    def _count(self, table: str, tenant_id: str) -> int:
        if not self.available:
            return 0
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                cur.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE tenant_id = %s",
                    (tenant_id,),
                )
                n = int(cur.fetchone()[0])
                cur.close()
                return n
            finally:
                conn.close()
        except Exception:
            return 0


def _cfg() -> dict[str, Any]:
    try:
        from kazma_core.memory.backends import get_backends_cfg

        return get_backends_cfg()
    except Exception:
        return {}


def state_capability(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    c = cfg or _cfg()
    st = c.get("state") or c.get("graph") or {}
    # Prefer dedicated state section; fall back to graph.url for advanced
    provider = str(st.get("provider") or "sqlite").lower()
    url = str(st.get("url") or "").strip()
    if provider in ("sqlite", "local", ""):
        return {
            "provider": "sqlite",
            "write_ready": True,
            "status": "local",
            "detail": "Primary state is local SQLite (memory_state.db)",
        }
    if provider in ("postgres", "postgresql", "pg") and url:
        return {
            "provider": "postgres",
            "write_ready": True,
            "status": "dual_mirror_and_sparse_read",
            "detail": (
                "Postgres dual-mirror + sparse ILIKE recall assist "
                "(local SQLite FTS/dense still primary)"
            ),
        }
    if provider in ("postgres", "postgresql", "pg"):
        return {
            "provider": "postgres",
            "write_ready": False,
            "status": "needs_url",
            "detail": "Set memory.backends.state.url (Postgres DSN) to enable dual-mirror",
        }
    return {
        "provider": provider,
        "write_ready": False,
        "status": "unknown",
        "detail": f"Unknown state provider {provider!r}",
    }


def get_state_backend() -> Any:
    """Live factory for optional shared state mirror.

    Cached per ``(provider, url, timeout_ms)``: previously every call minted a
    fresh ``PostgresStateBackend`` and probed connectivity (TCP connect +
    ``SELECT 1``), so a single recall with thin local hits opened 3+ fresh
    connections per turn — a Postgres connection storm (audit finding). The
    cache key captures every config field that changes the backend, so a
    Settings change naturally invalidates (new key → new backend). The null
    fallback is NOT cached so a later postgres-comes-back can be picked up.
    """
    c = _cfg()
    st = c.get("state") or {}
    provider = str(st.get("provider") or "sqlite").lower()
    url = str(st.get("url") or "").strip()
    timeout_ms = int((c.get("failover") or {}).get("timeout_ms") or 5000)
    key = (provider, url, timeout_ms)
    with _state_backend_lock:
        cached = _state_backend_cache.get(key)
    if cached is not None:
        return cached
    if provider in ("postgres", "postgresql", "pg") and url:
        be = PostgresStateBackend(url, timeout_s=timeout_ms / 1000.0)
        if be.available:
            with _state_backend_lock:
                _state_backend_cache[key] = be
            return be
        logger.info("[state_backend] postgres unavailable — null sink")
    return NullStateBackend()


def mirror_episode_to_state(row: dict[str, Any]) -> bool:
    """Best-effort dual-mirror of an episode row to shared state."""
    try:
        return bool(get_state_backend().mirror_episode(row))
    except Exception:
        return False


def mirror_belief_to_state(row: dict[str, Any]) -> bool:
    """Best-effort dual-mirror of a belief row to shared state."""
    try:
        return bool(get_state_backend().mirror_belief(row))
    except Exception:
        return False


def search_state_episodes(
    query: str, *, tenant_id: str = "default", limit: int = 10
) -> list[dict[str, Any]]:
    try:
        return list(
            get_state_backend().search_episodes(
                query, tenant_id=tenant_id, limit=limit
            )
        )
    except Exception:
        return []


def search_state_beliefs(
    query: str, *, tenant_id: str = "default", limit: int = 10
) -> list[dict[str, Any]]:
    try:
        return list(
            get_state_backend().search_beliefs(query, tenant_id=tenant_id, limit=limit)
        )
    except Exception:
        return []
