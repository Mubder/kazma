"""Optional shared state backend — multi-replica foundation (P2-2).

Default remains local SQLite (``memory_state.db``). When
``memory.backends.state.provider=postgres`` and a DSN is set, writes can
**dual-mirror** beliefs/episodes to Postgres so multiple app processes share
a durable copy for APIs / future cutover.

Default recall still prefers SQLite FTS/dense with this module as a
**mirror + ILIKE assist**, fused with **pgvector** when Postgres is on.
Set ``memory.backends.state.role=primary``
(or ``KAZMA_MEMORY_STATE_ROLE=primary``) to make StateBackend the
sparse SoT — fail-closed if Postgres is down (no silent SQLite lie).
Dense search then uses pgvector (not ILIKE-only).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from collections.abc import Iterable

# Liveness-probe cache TTL for the shared-state backend's `available`.
_READY_PROBE_TTL = 60.0
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "StateBackend",
    "get_state_backend",
    "is_state_primary",
    "should_apply_remote_write",
    "state_capability",
    "state_conflict_policy",
    "state_region",
    "state_role",
    "mirror_episode_to_state",
    "mirror_belief_to_state",
    "backfill_state_mirror",
    "search_state_episodes",
    "search_state_beliefs",
    "unmirror_belief_to_state",
    "remirror_belief_by_id",
    "remirror_episode_by_id",
    "remirror_episodes",
    "sync_state_mirror",
    "last_mirror_sync",
    "written_elsewhere",
    "reconcile_state_beliefs",
    "mirror_drift_summary",
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


class _NullStateBackend:
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

    def delete_belief(self, row_id: str) -> bool:
        del row_id
        return False

    def mirror_belief_snapshot(self) -> dict[str, bool]:
        return {}

    def episode_digest(self) -> dict[str, tuple[str, str]]:
        return {}

    def belief_digest(self) -> dict[str, tuple[bool, str]]:
        return {}

    def row_regions(self, table: str, ids: list[str] | None = None) -> dict[str, str]:
        return {}

    def search_episodes(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]:
        return []

    def search_beliefs(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]:
        return []

    def fetch_episodes(
        self, ids: list[str], *, tenant_id: str = "default"
    ) -> list[dict[str, Any]]:
        return []

    def fetch_beliefs(
        self, ids: list[str], *, tenant_id: str = "default"
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
        self._ready_at: float = 0.0
        self._ensured = False
        self._pool: Any = None
        self._pool_lock = threading.Lock()
        self._closed = False

    def _connect(self) -> Any:
        """A connection for one operation; its ``close()`` gives it back.

        Borrowed from this backend's pool (Memory audit M31: every mirrored
        row, digest and probe opened a Postgres connection of its own --
        thousands a pass at scale). Without psycopg's pool package, a new
        connection, as before.
        """
        pool = self._get_pool()
        if pool is not None:
            return _PooledConnection(pool, pool.getconn(timeout=self._timeout))
        try:
            import psycopg

            return psycopg.connect(self._dsn, connect_timeout=int(self._timeout))
        except ImportError:
            import psycopg2

            return psycopg2.connect(self._dsn, connect_timeout=int(self._timeout))

    def _get_pool(self) -> Any:
        if self._pool is not None or self._closed or not self._dsn:
            return self._pool
        try:
            import psycopg  # noqa: F401 - the pool needs psycopg 3
            from psycopg_pool import ConnectionPool
        except ImportError:
            return None
        from kazma_core.db.postgres_pool import pool_connection_kwargs

        with self._pool_lock:
            if self._pool is None and not self._closed:
                self._pool = ConnectionPool(
                    conninfo=self._dsn,
                    min_size=1,
                    max_size=4,
                    timeout=self._timeout,
                    kwargs=pool_connection_kwargs(self._dsn, autocommit=False),
                    # A connection the server closed is replaced at checkout
                    # (AGENTS.md section 44).
                    check=ConnectionPool.check_connection,
                    name="kazma-memory-mirror",
                    open=True,
                )
        return self._pool

    def close(self) -> None:
        """Close the pool (a backend dropped from the cache, or shutdown)."""
        with self._pool_lock:
            self._closed = True
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    @property
    def available(self) -> bool:
        # TTL-cache the liveness probe (audit finding): a once-set _ready was
        # never re-probed, so a backend that went down after a successful boot
        # probe kept reporting available forever, and a boot-time outage stuck
        # until restart.
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

    def _ensure(self, conn: Any) -> None:
        if self._ensured:
            return
        from kazma_core.db.pg_schema import ensure_schema

        cur = conn.cursor()
        ensure_schema(cur, [
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
            """,
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
            """,
            "CREATE INDEX IF NOT EXISTS idx_kazma_ep_tenant ON kazma_episodes(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_kazma_bel_tenant ON kazma_beliefs(tenant_id)",
        ])
        conn.commit()
        cur.close()
        self._ensured = True

    def _existing_meta(self, conn: Any, table: str, row_id: str) -> dict[str, Any] | None:
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT metadata_json FROM {table} WHERE id = %s", (row_id,))
            hit = cur.fetchone()
        finally:
            cur.close()
        if not hit:
            return None
        return _parse_meta(hit[0])

    def _may_write(self, conn: Any, table: str, row_id: str) -> bool:
        existing = self._existing_meta(conn, table, str(row_id))
        if existing is None:
            return True
        ok, reason = should_apply_remote_write(
            existing, region=state_region(), policy=state_conflict_policy()
        )
        if not ok:
            # The mirror sync counts these and, under fail_closed, reports
            # them once a pass; one line per refused write would be noise.
            logger.debug("[state_backend] skip %s %s: %s", table, row_id, reason)
        return ok

    def mirror_episode(self, row: dict[str, Any]) -> bool:
        return bool(row.get("id")) and self.mirror_episode_rows([row]) == [str(row["id"])]

    def mirror_episode_rows(self, rows: list[dict[str, Any]]) -> list[str]:
        """Upsert *rows* on one connection, in one transaction (Memory audit
        M31: a sweep that moved 16,000 memories pushed them one connection
        and two round trips each -- five minutes at 10x the live size).
        Returns the ids written; a row another region owns (under
        ``origin_wins`` / ``fail_closed``) is left out. Never raises: a
        failure writes nothing and returns ``[]``."""
        rows = [r for r in rows if r.get("id")]
        if not self._dsn or not rows:
            return []
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                allowed = self._writable(conn, "kazma_episodes", [str(r["id"]) for r in rows])
                rows = [r for r in rows if str(r["id"]) in allowed]
                if not rows:
                    return []
                cur = conn.cursor()
                cur.executemany(_EPISODE_UPSERT, [_episode_params(r) for r in rows])
                conn.commit()
                cur.close()
                return [str(r["id"]) for r in rows]
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres episode mirror failed", exc_info=True)
            return []

    def _writable(self, conn: Any, table: str, ids: list[str]) -> set[str]:
        """Which of *ids* this region may write (:func:`should_apply_remote_write`
        on each existing row's metadata), read in one query."""
        cur = conn.cursor()
        try:
            cur.execute(f"SELECT id, metadata_json FROM {table} WHERE id = ANY(%s)", (list(ids),))
            existing = {str(r[0]): _parse_meta(r[1]) for r in cur.fetchall()}
        finally:
            cur.close()
        region, policy = state_region(), state_conflict_policy()
        out = set()
        for rid in ids:
            meta = existing.get(rid)
            if meta is None or should_apply_remote_write(meta, region=region, policy=policy)[0]:
                out.add(rid)
            else:
                logger.debug("[state_backend] skip %s %s: another region's row", table, rid)
        return out

    def mirror_belief(self, row: dict[str, Any]) -> bool:
        if not self._dsn or not row.get("id"):
            return False
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                if not self._may_write(conn, "kazma_beliefs", str(row.get("id"))):
                    return False
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
                        _pg_text(row.get("subject")),
                        _pg_text(row.get("predicate")),
                        row.get("predicate_type") or "functional",
                        _pg_text(row.get("object")),
                        float(row.get("confidence") or 0.5),
                        int(row.get("structural_importance") or 1),
                        float(row.get("source_trust_weight") or 1.0),
                        row.get("valid_from"),
                        row.get("valid_until"),
                        row.get("invalidated_at"),
                        _stamp_region_meta(row),
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

    def delete_belief(self, row_id: str) -> bool:
        """Hard-delete a mirrored belief (row left the SQLite SoT entirely)."""
        if not self._dsn or not row_id:
            return False
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                cur.execute("DELETE FROM kazma_beliefs WHERE id = %s", (row_id,))
                conn.commit()
                cur.close()
                return True
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres belief delete failed", exc_info=True)
            return False

    def mirror_belief_snapshot(self) -> dict[str, bool]:
        """id → live-flag scan of the mirror (reconcile/drift inputs)."""
        if not self._dsn:
            return {}
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, invalidated_at IS NULL AND valid_until IS NULL "
                    "FROM kazma_beliefs"
                )
                out = {r[0]: bool(r[1]) for r in cur.fetchall()}
                cur.close()
                return out
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] snapshot failed", exc_info=True)
            return {}

    def _digest(self, sql: str) -> dict[str, tuple[Any, str]]:
        if not self._dsn:
            return {}
        conn = self._connect()
        try:
            self._ensure(conn)
            cur = conn.cursor()
            cur.execute(sql)
            out = {str(r[0]): (r[1], str(r[2])) for r in cur.fetchall()}
            cur.close()
            return out
        finally:
            conn.close()

    def episode_digest(self) -> dict[str, tuple[str, str]]:
        """id -> (tier, digest of the texts) of every mirrored episode, for
        :func:`sync_state_mirror`. Raises when Postgres does: a sync that could
        not read the mirror must not conclude it is empty."""
        return self._digest(
            "SELECT id, COALESCE(tier, ''), md5(COALESCE(user_text, '') || chr(31) || "
            "COALESCE(assistant_text, '') || chr(31) || COALESCE(summary_text, '')) "
            "FROM kazma_episodes"
        )

    def belief_digest(self) -> dict[str, tuple[bool, str]]:
        """id -> (alive, digest of the value) of every mirrored fact. Raises
        like :meth:`episode_digest`."""
        return {
            k: (bool(alive), h)
            for k, (alive, h) in self._digest(
                "SELECT id, invalidated_at IS NULL AND valid_until IS NULL, "
                "md5(COALESCE(object, '')) FROM kazma_beliefs"
            ).items()
        }

    def present_ids(self, table: str, ids: list[str]) -> set[str]:
        """Which of *ids* the mirror's *table* holds (primary-key reads).
        :func:`sync_state_mirror` asks it for the newest rows it already
        pushed: none there means the mirror went back (a restore of the
        shared database). Raises like :meth:`episode_digest`."""
        if table not in ("kazma_episodes", "kazma_beliefs") or not self._dsn or not ids:
            return set()
        conn = self._connect()
        try:
            self._ensure(conn)
            cur = conn.cursor()
            cur.execute(f"SELECT id FROM {table} WHERE id = ANY(%s)", (list(ids),))
            out = {str(r[0]) for r in cur.fetchall()}
            cur.close()
            return out
        finally:
            conn.close()

    def row_regions(self, table: str, ids: list[str] | None = None) -> dict[str, str]:
        """id -> the region that wrote it, for every row of *table* whose
        metadata names one (:func:`sync_state_mirror` under ``origin_wins`` /
        ``fail_closed``), or only the rows *ids* names (the outbox drain).
        Read with a pattern, not a JSON cast: one row of malformed metadata
        would fail the whole query. Raises like :meth:`episode_digest`."""
        if table not in ("kazma_episodes", "kazma_beliefs") or not self._dsn:
            return {}
        conn = self._connect()
        try:
            self._ensure(conn)
            cur = conn.cursor()
            sql = (
                "SELECT id, substring(metadata_json from "
                "'\"region\"[[:space:]]*:[[:space:]]*\"([^\"]*)\"') "
                f"FROM {table} WHERE metadata_json LIKE '%\"region\"%'"
            )
            if ids is None:
                cur.execute(sql)
            else:
                cur.execute(sql + " AND id = ANY(%s)", (list(ids),))
            out = {str(r[0]): str(r[1]) for r in cur.fetchall() if r[1]}
            cur.close()
            return out
        finally:
            conn.close()

    def search_episodes(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]:
        """Keyword search over mirrored episode text (multi-replica read).

        The question's content words only (``memory/query_terms.py``): ORing
        every token as a substring matched "is" and "my" everywhere, so this
        returned the newest memories whatever was asked. ``LIKE`` prefilters;
        whole words are confirmed in Python.
        """
        from kazma_core.memory.query_terms import mentions, search_terms

        terms = search_terms(query)[:8]
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
                from kazma_core.memory.vector_engine import RECALLABLE_TIERS

                text = ("(LOWER(COALESCE(user_text,'')) || ' ' || LOWER(COALESCE(assistant_text,'')) "
                        "|| ' ' || LOWER(COALESCE(summary_text,'')))")
                # How many of the question's words a row holds: the candidates
                # are the rows that hold the most, not the newest that hold one
                # (Memory audit M06 -- an older memory only its words would
                # find fell out of the slice once newer rows shared a word).
                score = " + ".join(f"(CASE WHEN {text} LIKE %s THEN 1 ELSE 0 END)" for _ in terms)
                tiers = ",".join("%s" for _ in RECALLABLE_TIERS)
                params: list[Any] = [f"%{t}%" for t in terms]
                params.append(tenant_id)
                params.extend(RECALLABLE_TIERS)
                for t in terms:
                    pat = f"%{t}%"
                    params.extend([pat, pat, pat])
                params.append(max(1, min(int(limit), 50)) * 5)
                cur.execute(
                    f"""
                    SELECT id, session_id, turn_number, user_text, assistant_text, summary_text,
                           tier, structural_importance, created_at, metadata_json,
                           ({score}) AS words_held
                    FROM kazma_episodes
                    WHERE tenant_id = %s AND tier IN ({tiers}) AND ({clauses})
                    ORDER BY words_held DESC, created_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                cur.close()
                rows = [
                    r for r in rows
                    if mentions(
                        " ".join(str(r.get(k) or "") for k in ("user_text", "assistant_text", "summary_text")),
                        terms,
                    )
                ]
                return rows[: max(1, min(int(limit), 50))]
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres episode search failed", exc_info=True)
            return []

    def search_beliefs(
        self, query: str, *, tenant_id: str = "default", limit: int = 10
    ) -> list[dict[str, Any]]:
        """Keyword search over mirrored active beliefs (content words, whole words)."""
        from kazma_core.memory.query_terms import mentions, search_terms

        terms = search_terms(query)[:8]
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
                text = ("(LOWER(COALESCE(subject,'')) || ' ' || LOWER(COALESCE(predicate,'')) "
                        "|| ' ' || LOWER(COALESCE(object,'')))")
                score = " + ".join(f"(CASE WHEN {text} LIKE %s THEN 1 ELSE 0 END)" for _ in terms)
                params: list[Any] = [f"%{t}%" for t in terms]
                params.append(tenant_id)
                for t in terms:
                    pat = f"%{t}%"
                    params.extend([pat, pat, pat])
                params.append(max(1, min(int(limit), 50)) * 5)
                cur.execute(
                    f"""
                    SELECT id, subject, predicate, object, predicate_type,
                           confidence, structural_importance, source_trust_weight,
                           valid_from, metadata_json,
                           ({score}) AS words_held
                    FROM kazma_beliefs
                    WHERE tenant_id = %s
                      AND valid_until IS NULL AND invalidated_at IS NULL
                      AND ({clauses})
                    ORDER BY words_held DESC, (structural_importance * confidence) DESC NULLS LAST
                    LIMIT %s
                    """,
                    params,
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                cur.close()
                rows = [
                    r for r in rows
                    if mentions(
                        " ".join(str(r.get(k) or "") for k in ("subject", "predicate", "object")),
                        terms,
                    )
                ]
                return rows[: max(1, min(int(limit), 50))]
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres belief search failed", exc_info=True)
            return []

    def fetch_episodes(
        self, ids: list[str], *, tenant_id: str = "default"
    ) -> list[dict[str, Any]]:
        wanted = [str(i) for i in ids if i][:50]
        if not wanted or not self._dsn:
            return []
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                from kazma_core.memory.vector_engine import RECALLABLE_TIERS

                placeholders = ",".join(["%s"] * len(wanted))
                tiers = ",".join("%s" for _ in RECALLABLE_TIERS)
                cur.execute(
                    f"""
                    SELECT id, session_id, turn_number, user_text, assistant_text, summary_text,
                           tier, structural_importance, created_at
                    FROM kazma_episodes
                    WHERE tenant_id = %s AND id IN ({placeholders}) AND tier IN ({tiers})
                    """,
                    [tenant_id, *wanted, *RECALLABLE_TIERS],
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                cur.close()
                return rows
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres episode fetch failed", exc_info=True)
            return []

    def fetch_beliefs(
        self, ids: list[str], *, tenant_id: str = "default"
    ) -> list[dict[str, Any]]:
        wanted = [str(i) for i in ids if i][:50]
        if not wanted or not self._dsn:
            return []
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                cur = conn.cursor()
                placeholders = ",".join(["%s"] * len(wanted))
                cur.execute(
                    f"""
                    SELECT id, subject, predicate, object, predicate_type,
                           confidence, structural_importance, source_trust_weight,
                           valid_from
                    FROM kazma_beliefs
                    WHERE tenant_id = %s AND id IN ({placeholders})
                      AND valid_until IS NULL AND invalidated_at IS NULL
                    """,
                    [tenant_id, *wanted],
                )
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                cur.close()
                return rows
            finally:
                conn.close()
        except Exception:
            logger.debug("[state_backend] postgres belief fetch failed", exc_info=True)
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


class _PooledConnection:
    """A pooled connection whose ``close()`` returns it to the pool, with any
    open transaction rolled back first (a digest's SELECT leaves one open,
    and the pool warns about every connection handed back inside one)."""

    def __init__(self, pool: Any, conn: Any) -> None:
        self._pool = pool
        self._conn = conn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is None:
            return
        import psycopg

        try:
            if not conn.closed and conn.info.transaction_status != _idle_status():
                conn.rollback()
        except psycopg.Error:  # a broken connection is discarded by putconn
            logger.debug("[state_backend] rollback before returning a connection failed", exc_info=True)
        self._pool.putconn(conn)


def _idle_status() -> Any:
    from psycopg.pq import TransactionStatus

    return TransactionStatus.IDLE


_EPISODE_UPSERT = """
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
"""


def _episode_params(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("id"),
        row.get("tenant_id") or "default",
        row.get("session_id"),
        int(row.get("turn_number") or 0),
        _pg_text(row.get("user_text")),
        _pg_text(row.get("assistant_text")),
        _pg_text(row.get("summary_text")),
        row.get("tier") or "episodic",
        int(row.get("structural_importance") or 1),
        float(row.get("created_at") or time.time()),
        _stamp_region_meta(row),
    )


def _cfg() -> dict[str, Any]:
    try:
        from kazma_core.memory.backends import get_backends_cfg

        return get_backends_cfg()
    except Exception:
        return {}


def state_role(cfg: dict[str, Any] | None = None) -> str:
    """``mirror`` (default) or ``primary``."""
    st = (cfg or _cfg()).get("state") or {}
    role = str(st.get("role") or "mirror").strip().lower()
    return role if role in ("primary", "mirror") else "mirror"


def is_state_primary(cfg: dict[str, Any] | None = None) -> bool:
    return state_role(cfg) == "primary"


def mirrors_to_postgres(cfg: dict[str, Any] | None = None) -> bool:
    """Memory's state backend is configured as Postgres (whether or not it is
    reachable right now). Never raises."""
    st = (cfg or _cfg()).get("state") or {}
    return str(st.get("provider") or "").lower() in ("postgres", "postgresql", "pg") and bool(
        str(st.get("url") or "").strip())


def state_region(cfg: dict[str, Any] | None = None) -> str:
    return str(((cfg or _cfg()).get("state") or {}).get("region") or "").strip()


def state_conflict_policy(cfg: dict[str, Any] | None = None) -> str:
    raw = str(
        ((cfg or _cfg()).get("state") or {}).get("conflict_policy") or "last_write_wins"
    ).strip().lower()
    if raw in ("last_write_wins", "origin_wins", "fail_closed"):
        return raw
    return "last_write_wins"


def should_apply_remote_write(
    existing_meta: dict[str, Any] | None,
    *,
    region: str = "",
    policy: str = "last_write_wins",
) -> tuple[bool, str]:
    """Decide whether a dual-write may overwrite an existing mirrored row.

    * ``last_write_wins`` -- always apply (default).
    * ``origin_wins`` -- the region that wrote a row keeps it; another
      region's change is not mirrored, quietly (memory health counts it,
      ``kept_by_origin``).
    * ``fail_closed`` -- the same refusal, reported: memory health counts it
      (``region_conflicts``), the mirror sync logs a WARNING each pass and
      pages the operator (``memory.region_conflict``). Until 2026-09-27 the
      two behaved the same.
    """
    pol = (policy or "last_write_wins").strip().lower()
    if pol not in ("last_write_wins", "origin_wins", "fail_closed"):
        pol = "last_write_wins"
    if pol == "last_write_wins":
        return True, "last_write_wins"
    existing_region = str((existing_meta or {}).get("region") or "").strip()
    incoming = (region or "").strip()
    if not existing_region or existing_region == incoming:
        return True, "same_or_empty_region"
    if pol == "origin_wins":
        return False, f"origin_wins: kept region {existing_region!r}"
    return False, f"fail_closed: region conflict {existing_region!r} vs {incoming!r}"


def _parse_meta(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _stamp_region_meta(row: dict[str, Any]) -> str:
    meta = _parse_meta(row.get("metadata_json") or row.get("metadata"))
    region = state_region()
    if region:
        meta["region"] = region
    install = _this_install_id()
    if install:
        meta["install"] = install
    meta["updated_at"] = time.time()
    meta["conflict_policy"] = state_conflict_policy()
    return json.dumps(meta, ensure_ascii=False)


def _pg_text(value: Any) -> Any:
    """Text as Postgres stores it: a NUL byte is refused there (the chat store
    refused every save of one chat over a single NUL, AGENTS.md §36), and a
    refused mirror write would be retried by every sync pass."""
    return value.replace("\x00", "") if isinstance(value, str) else value


def _this_install_id() -> str | None:
    """This install's id (``<data dir>/install_id``, written at boot), read
    and never minted: a script or a test that never booted a server has none,
    and its mirror writes carry no tag rather than a made-up one."""
    from kazma_core.db.shared_store_peers import install_id

    return install_id(create=False)


def written_elsewhere(row: dict[str, Any]) -> bool:
    """True when another install sharing the mirror wrote this mirror row.

    The mirror holds this install's memories and other replicas' writes. A
    row this install wrote and no longer holds was removed here -- forgotten,
    retired as a copy, deleted by a cleanup since removed -- and recall's
    top-up must not bring it back: live on 2026-09-27 the mirror held 53 such
    rows, test data among them ("User prefers dark mode"). Rows mirrored
    before writers were tagged (that day) carry no tag and count as this
    install's own.
    """
    meta = _parse_meta(row.get("metadata_json") or row.get("metadata"))
    writer = str(meta.get("install") or "")
    return bool(writer) and writer != (_this_install_id() or "")


def state_capability(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    c = cfg or _cfg()
    st = c.get("state") or c.get("graph") or {}
    # Prefer dedicated state section; fall back to graph.url for advanced
    provider = str(st.get("provider") or "sqlite").lower()
    url = str(st.get("url") or "").strip()
    role = state_role(c)
    policy = state_conflict_policy(c)
    region = state_region(c)
    extra = {
        "role": role,
        "conflict_policy": policy,
        "region": region,
    }
    if provider in ("sqlite", "local", ""):
        return {
            "provider": "sqlite",
            "write_ready": True,
            "status": "local",
            "detail": "Primary state is local SQLite (memory_state.db)",
            **extra,
        }
    if provider in ("postgres", "postgresql", "pg") and url:
        if role == "primary":
            return {
                "provider": "postgres",
                "write_ready": True,
                "status": "postgres_primary",
                "detail": (
                    "Postgres is the recall primary (StateBackend ILIKE). "
                    "Down = fail-closed empty recall, no silent SQLite fallback. "
                    f"Conflict policy: {policy}."
                ),
                **extra,
            }
        return {
            "provider": "postgres",
            "write_ready": True,
            "status": "dual_mirror_and_sparse_read",
            "detail": (
                "Postgres dual-mirror + sparse ILIKE recall assist "
                f"(local SQLite FTS/dense still primary; conflict={policy})"
            ),
            **extra,
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
                # A changed setting replaces the backend: the old one's pool
                # is closed, not left reconnecting for the life of the process.
                stale = [_state_backend_cache.pop(k) for k in list(_state_backend_cache) if k != key]
                _state_backend_cache[key] = be
            for old in stale:
                getattr(old, "close", lambda: None)()
            return be
        be.close()
        logger.info("[state_backend] postgres unavailable — null sink")
    return _NullStateBackend()


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


def unmirror_belief_to_state(row_id: str) -> bool:
    """Hard-delete a mirrored belief whose SQLite row no longer exists."""
    try:
        return bool(get_state_backend().delete_belief(row_id))
    except Exception:
        return False


#: The episode columns the state mirror holds (``PostgresStateBackend.mirror_episode``).
_EPISODE_MIRROR_COLUMNS = (
    "id, tenant_id, session_id, turn_number, user_text, assistant_text, "
    "summary_text, tier, structural_importance, created_at, metadata_json"
)


def episode_mirror_row(conn: Any, episode_id: str) -> dict[str, Any] | None:
    """An episode as the mirror holds it, read from *conn* now (None when the
    row does not exist). Writers push this, never their own arguments."""
    return _row_dict(conn, f"SELECT {_EPISODE_MIRROR_COLUMNS} FROM episodes WHERE id = ?", episode_id)


def remirror_episode_by_id(conn: Any, episode_id: str) -> bool:
    """Push an episode's CURRENT local row to the mirror (a tier move, restored
    text). A row that no longer exists is left alone. Never raises."""
    try:
        row = episode_mirror_row(conn, episode_id)
    except sqlite3.Error:
        logger.debug("[state_backend] remirror failed for %s", episode_id, exc_info=True)
        return False
    return mirror_episode_to_state(row) if row is not None else False


def _row_dict(conn: Any, sql: str, key: str) -> dict[str, Any] | None:
    """One row as a dict, whatever the connection's row factory: the
    working-turn promotion's connection returns tuples, and ``dict(row)`` of a
    tuple raised out of a function that promises never to."""
    cur = conn.execute(sql, (key,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], tuple(row)))


def remirror_belief_by_id(conn: Any, belief_id: str) -> bool:
    """Tombstone/sync primitive: push a belief's CURRENT local row to the
    mirror — including its death flags (valid_until / invalidated_at).

    Call after ANY local life/death transition (supersede close, invalidate,
    restore, edit). A row that vanished from SQLite mid-flight is treated as
    a delete. Never raises.
    """
    try:
        row = _row_dict(conn, "SELECT * FROM beliefs WHERE id = ?", belief_id)
        if row is None:
            return unmirror_belief_to_state(belief_id)
        return mirror_belief_to_state(row)
    except Exception:
        logger.debug("[state_backend] remirror failed for %s", belief_id, exc_info=True)
        return False


#: Episodes pushed per batch (one SELECT here, one transaction there).
_PUSH_BATCH = 500


def remirror_episodes(conn: Any, episode_ids: Iterable[str]) -> int:
    """Push these episodes' current local rows to the mirror after a change
    (a tier move, restored text), in batches. No-op without a mirror. Returns
    how many reached it; a miss stays in the outbox for the next sync pass
    (and the full comparison)."""
    ids = list(dict.fromkeys(str(i) for i in episode_ids if i))
    backend = get_state_backend()
    if not ids or isinstance(backend, _NullStateBackend):
        return 0
    return len(_push_episodes(conn, backend, ids))


def _push_episodes(conn: Any, backend: Any, ids: list[str]) -> list[str]:
    """Push the current local rows of *ids* (a row no longer here is skipped)
    and take them off the outbox. Returns the ids the mirror took."""
    batch_push = getattr(backend, "mirror_episode_rows", None)
    written: list[str] = []
    for start in range(0, len(ids), _PUSH_BATCH):
        chunk = ids[start:start + _PUSH_BATCH]
        upto = _outbox_head(conn)
        try:
            rows = _episode_rows(conn, chunk)
        except sqlite3.Error:
            logger.debug("[state_backend] remirror read failed", exc_info=True)
            continue
        if batch_push is not None:
            done = batch_push(rows)
        else:  # a backend without batches (a stand-in): one row at a time
            done = [str(r["id"]) for r in rows if backend.mirror_episode(r)]
        written.extend(done)
        _dequeue(conn, "episode", done, upto)
    return written


def _episode_rows(conn: Any, ids: list[str]) -> list[dict[str, Any]]:
    cur = conn.execute(
        f"SELECT {_EPISODE_MIRROR_COLUMNS} FROM episodes WHERE id IN ({','.join('?' for _ in ids)})", ids)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, tuple(r))) for r in cur.fetchall()]


def _outbox_head(conn: Any) -> int | None:
    """The outbox's newest entry now (changes queued later stay queued)."""
    try:
        row = conn.execute("SELECT MAX(seq) FROM memory_mirror_outbox").fetchone()
    except sqlite3.OperationalError:  # a database older than the outbox
        return None
    return int(row[0]) if row and row[0] is not None else None


def _dequeue(conn: Any, kind: str, ids: list[str], upto: int | None) -> None:
    """Take *ids* off the outbox, entries up to *upto* only -- a change queued
    after the row was read is still to push. Left alone inside a caller's own
    transaction (the next pass pushes the row once more, which is harmless)."""
    if not ids or upto is None or getattr(conn, "in_transaction", True):
        return
    try:
        for start in range(0, len(ids), _PUSH_BATCH):
            chunk = ids[start:start + _PUSH_BATCH]
            conn.execute(
                f"DELETE FROM memory_mirror_outbox WHERE kind = ? AND seq <= ? "
                f"AND row_id IN ({','.join('?' for _ in chunk)})", [kind, upto, *chunk])
        conn.commit()
    except sqlite3.Error:
        logger.debug("[state_backend] outbox dequeue failed (the next pass pushes again)", exc_info=True)


#: At most this many rows pushed per sync pass (each is one Postgres write).
#: Rows a pass pushes at most. 500 when every push was a connection of its
#: own; batched and pooled (M31) a pass pushes 5,000 in seconds, and a
#: restore of the shared database can take thousands from the mirror at once.
_SYNC_LIMIT = 5000
_last_sync: dict[str, Any] = {}
#: When the mirror was last compared row by row (``observability.cadence``).
_FULL_VERIFY_KEY = "memory.v2.mirror_full_verify"
#: A queued row whose push failed this many times leaves the outbox; the next
#: full comparison finds it (and reports a region conflict, if that is why).
_OUTBOX_MAX_ATTEMPTS = 5


def _episode_digest(tier: Any, user: Any, assistant: Any, summary: Any) -> tuple[str, str]:
    """The (tier, texts digest) :meth:`PostgresStateBackend.episode_digest`
    computes, from a local row: the texts as the mirror stores them."""
    texts = "\x1f".join(str(_pg_text(t) or "") for t in (user, assistant, summary))
    # A comparison digest, not a secret -- the mirror computes the same md5.
    return str(tier or "episodic"), hashlib.md5(texts.encode("utf-8"), usedforsecurity=False).hexdigest()


def _verify_interval_s() -> float:
    try:
        from kazma_core.memory.config import read_memory_cfg

        hours = float(((read_memory_cfg() or {}).get("v2") or {}).get("mirror_verify_hours", 24))
    except (TypeError, ValueError):
        hours = 24.0
    return min(168.0, max(1.0, hours)) * 3600.0


def _full_verify_due(now: float) -> bool:
    from kazma_core.observability.cadence import last_run

    last = last_run(_FULL_VERIFY_KEY)
    return last is None or now - last >= _verify_interval_s()


#: When a pass last compared in full because the mirror had gone back; at
#: most once an hour, so a row another region keeps out of the mirror (one of
#: the newest, by chance) cannot make every pass a full comparison.
_forced_full_at: dict[str, float] = {}
_FORCED_FULL_MIN_S = 3600.0
_PROBE_ROWS = 3


def _mirror_went_back(conn: sqlite3.Connection, backend: Any, now: float) -> bool:
    """Whether the mirror lost rows it had been sent: one of the newest local
    rows already pushed (none waiting in the outbox) is not there.

    A restore of the shared database (``pg_restore`` of an older dump, the
    disaster-recovery runbook's step) does that. Each pass pushes only what
    changed (the outbox, M31) and those rows had not, so until the daily
    full comparison recall on the Postgres-primary path missed every memory
    written after the dump (the restore drill, 2026-10-14). Rows are inserted
    in rowid order, so the newest are what an older dump lacks. A row another
    region keeps out of the mirror looks the same: the hourly limit bounds
    what such a row costs.
    """
    present = getattr(backend, "present_ids", None)
    if present is None or now - _forced_full_at.get("at", 0.0) < _FORCED_FULL_MIN_S:
        return False
    for kind, local, table in (("episode", "episodes", "kazma_episodes"), ("belief", "beliefs", "kazma_beliefs")):
        try:
            ids = [str(r[0]) for r in conn.execute(
                f"SELECT id FROM {local} WHERE id NOT IN "
                "(SELECT row_id FROM memory_mirror_outbox WHERE kind = ?) ORDER BY rowid DESC LIMIT ?",
                (kind, _PROBE_ROWS),
            )]
        except sqlite3.OperationalError:  # a database older than the outbox
            return False
        if ids and len(present(table, ids)) < len(ids):
            _forced_full_at["at"] = now
            logger.warning(
                "[memory] mirror sync: the Postgres mirror lacks the newest %ss it was sent "
                "(a restore of the shared database?); comparing in full now", kind)
            return True
    return False


#: What a full comparison found; an incremental pass reports them as they stood.
_FINDINGS_KEY = "memory.v2.mirror_verify_result"
_FINDING_FIELDS = ("episodes_behind", "beliefs_behind", "mirror_only_episodes", "mirror_only_beliefs",
                   "kept_by_origin", "region_conflicts", "verified_at")


def _keep_findings(stats: dict[str, Any]) -> None:
    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_helpers import store_errors

    try:
        get_config_store().set(_FINDINGS_KEY, {k: stats[k] for k in _FINDING_FIELDS if k in stats},
                               category="memory")
    except store_errors():  # health shows them again after the next full comparison
        logger.warning("[memory] mirror sync: could not keep the comparison's findings", exc_info=True)


def _findings() -> dict[str, Any]:
    prior = last_mirror_sync()
    if "verified_at" in prior:
        return {k: prior[k] for k in _FINDING_FIELDS if k in prior}
    from kazma_core.config_store import get_config_store
    from kazma_core.db.pg_helpers import store_errors

    try:
        kept = get_config_store().get(_FINDINGS_KEY)
    except store_errors():
        logger.debug("[memory] mirror sync: kept findings unreadable", exc_info=True)
        return {}
    return {k: kept[k] for k in _FINDING_FIELDS if isinstance(kept, dict) and k in kept}


def _clear_outbox(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DELETE FROM memory_mirror_outbox")
        conn.commit()
    except sqlite3.OperationalError:  # a database older than the outbox
        logger.debug("[memory] mirror sync: no outbox to clear", exc_info=True)


def _outbox_depth(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) FROM memory_mirror_outbox").fetchone()[0])
    except sqlite3.OperationalError:  # a database older than the outbox
        return 0


def _owners(backend: Any, table: str, ids: list[str]) -> dict[str, str]:
    try:
        return backend.row_regions(table, ids)
    except TypeError:  # a backend that reads every row's region only
        wanted = set(ids)
        return {k: v for k, v in backend.row_regions(table).items() if k in wanted}


def _drain_outbox(conn: sqlite3.Connection, budget: int, backend: Any = None) -> dict[str, Any]:
    """Push the rows the outbox names, oldest change first, at most *budget*.

    A row is pushed as it is NOW (one push however often it changed); its
    outbox entries are removed once the push succeeds. A failed push stays
    queued with an attempt counted, and leaves after
    :data:`_OUTBOX_MAX_ATTEMPTS` -- a region conflict refuses for good, and
    the full comparison counts and reports those.
    """
    out: dict[str, Any] = {"episodes": 0, "beliefs": 0, "failed": 0, "dropped": 0, "conflicts": []}
    try:
        rows = conn.execute(
            "SELECT seq, kind, row_id FROM memory_mirror_outbox ORDER BY seq LIMIT ?", (max(1, budget) * 8,)
        ).fetchall()
    except sqlite3.OperationalError:  # a database older than the outbox: the full comparison covers it
        logger.debug("[memory] mirror sync: no outbox", exc_info=True)
        return out
    latest: dict[tuple[str, str], int] = {}
    for seq, kind, row_id in rows:
        latest[(kind, row_id)] = seq
    picked = sorted(latest.items(), key=lambda kv: kv[1])[: max(0, budget)]
    # Under origin_wins / fail_closed a row another region wrote is not this
    # install's to overwrite (as in the full comparison): counted, dequeued.
    policy, here = state_conflict_policy(), state_region()
    owners: dict[str, dict[str, str]] = {}
    if backend is not None and policy != "last_write_wins":
        for kind, table in (("episode", "kazma_episodes"), ("belief", "kazma_beliefs")):
            ids = [rid for (k, rid), _seq in picked if k == kind]
            if ids:
                owners[kind] = _owners(backend, table, ids)
    pushable: list[tuple[tuple[str, str], int]] = []
    for (kind, row_id), seq in picked:
        owner = owners.get(kind, {}).get(row_id)
        if owner and not should_apply_remote_write({"region": owner}, region=here, policy=policy)[0]:
            out["conflicts"].append(row_id)
            conn.execute("DELETE FROM memory_mirror_outbox WHERE kind = ? AND row_id = ? AND seq <= ?",
                         (kind, row_id, seq))
            conn.commit()
            continue
        pushable.append(((kind, row_id), seq))
    episode_ids = [rid for (kind, rid), _seq in pushable if kind == "episode"]
    here_ids = {str(r[0]) for start in range(0, len(episode_ids), _PUSH_BATCH)
                for r in conn.execute(
                    f"SELECT id FROM episodes WHERE id IN ({','.join('?' * len(episode_ids[start:start + _PUSH_BATCH]))})",
                    episode_ids[start:start + _PUSH_BATCH])}
    pushed_eps = set(_push_episodes(conn, backend, [e for e in episode_ids if e in here_ids])) if backend else set()
    for (kind, row_id), seq in pushable:
        if kind == "episode":
            # Deleted here: the mirror keeps its copy, as before.
            ok = row_id not in here_ids or row_id in pushed_eps
        else:
            ok = remirror_belief_by_id(conn, row_id)
        if ok:
            if kind == "belief" or row_id in pushed_eps:  # a row deleted here is dequeued, not pushed
                out["episodes" if kind == "episode" else "beliefs"] += 1
            conn.execute("DELETE FROM memory_mirror_outbox WHERE kind = ? AND row_id = ? AND seq <= ?",
                         (kind, row_id, seq))
        else:
            out["failed"] += 1
            conn.execute("UPDATE memory_mirror_outbox SET attempts = attempts + 1 "
                         "WHERE kind = ? AND row_id = ? AND seq <= ?", (kind, row_id, seq))
        conn.commit()
    gone = conn.execute("DELETE FROM memory_mirror_outbox WHERE attempts >= ?", (_OUTBOX_MAX_ATTEMPTS,))
    out["dropped"] = gone.rowcount or 0
    conn.commit()
    return out


def sync_state_mirror(
    conn: sqlite3.Connection, *, limit: int = _SYNC_LIMIT, full: bool | None = None,
) -> dict[str, Any]:
    """Keep the Postgres mirror equal to the local store: each pass pushes the
    rows the change outbox names; a full comparison runs when
    ``memory.v2.mirror_verify_hours`` have passed since the last one (or
    *full*). Without a configured mirror the outbox is emptied (nothing will
    read it). Memory audit M31: every pass used to read every local text and
    every mirror digest.
    """
    backend = get_state_backend()
    if isinstance(backend, _NullStateBackend) and not mirrors_to_postgres():
        _clear_outbox(conn)
        return {"skipped": "no mirror"}
    if isinstance(backend, _NullStateBackend) or not getattr(backend, "available", False):
        # Configured but down: the outbox keeps every change for the next pass.
        return {"skipped": "mirror unavailable", "outbox": _outbox_depth(conn)}
    now = time.time()
    if full if full is not None else (_full_verify_due(now) or _mirror_went_back(conn, backend, now)):
        drained = _drain_outbox(conn, limit, backend)
        stats = _compare_in_full(conn, backend, limit=max(0, limit - drained["episodes"] - drained["beliefs"]))
        stats["episodes_pushed"] += drained["episodes"]
        stats["beliefs_pushed"] += drained["beliefs"]
        stats["failed"] += drained["failed"]
        stats["mode"] = "full"
        _keep_findings(stats)
        # Caught up, or nothing more it can push: the next comparison in a
        # day. Still pushing with more behind (a restore took thousands of
        # rows): the next pass compares in full again, not a day later.
        catching_up = (stats["episodes_pushed"] or stats["beliefs_pushed"]) and (
            stats["episodes_behind"] or stats["beliefs_behind"])
        stats["catching_up"] = bool(catching_up)
        if not catching_up:
            from kazma_core.observability.cadence import stamp_run

            stamp_run(_FULL_VERIFY_KEY, now)
    else:
        drained = _drain_outbox(conn, limit, backend)
        stats = {
            "episodes_pushed": drained["episodes"],
            "beliefs_pushed": drained["beliefs"],
            "failed": drained["failed"],
            "mode": "incremental",
            "at": now,
        }
        # The last full comparison's findings stand until the next (kept in
        # the settings store, so a restart does not lose them).
        stats.update(_findings())
    stats["outbox"] = _outbox_depth(conn)
    stats["outbox_dropped"] = drained["dropped"]
    if stats["mode"] == "incremental" and drained["conflicts"]:
        stats["outbox_conflicts"] = len(drained["conflicts"])
        if state_conflict_policy() == "fail_closed":
            _report_region_conflicts(drained["conflicts"], state_region())
    _last_sync.clear()
    _last_sync.update(stats)
    if stats["mode"] == "incremental" and (drained["episodes"] or drained["beliefs"]):
        logger.debug("[memory] mirror sync: pushed %d changed memories and %d changed facts",
                     drained["episodes"], drained["beliefs"])
    if drained["failed"]:
        logger.warning("[memory] mirror sync: %d pushes failed; the next pass retries them", drained["failed"])
    return stats


def _compare_in_full(conn: sqlite3.Connection, backend: Any, *, limit: int) -> dict[str, Any]:
    """Anti-entropy for the Postgres mirror: every local memory reaches it as
    it is now.

    Mirror writes are best effort. One made while Postgres was down, or by a
    path that did not mirror (the working-turn promotion, until 2026-09-27),
    left the copy behind for good: live that day 116 turns were still
    "working" in the mirror and 17 facts had never reached it. A pass compares
    the whole mirror with the local store -- id, tier and a digest of the
    texts; for a fact, whether it is current and its value -- and pushes what
    differs, newest first, at most *limit* rows. A row only the mirror holds
    is counted and left alone, never deleted and never copied back: another
    install may have written it, and recall's top-up already leaves this
    install's own out (:func:`written_elsewhere`).

    Runs when Postgres is the primary store too (Memory audit M06): recall
    reads it there, and a tombstone a blip kept from it was never repaired.
    It pushes this install's local rows; a row only the mirror holds (another
    replica's) is counted and left alone, never pulled. Skipped without a
    mirror. Raises
    when the mirror cannot be read (the maintenance runner logs it).
    """
    # Under origin_wins / fail_closed a row another region wrote is not this
    # install's to overwrite: it is counted, never pushed -- and never spent
    # from the push budget, or 500 of them would stop every other push.
    policy = state_conflict_policy()
    here = state_region()
    guarded = policy != "last_write_wins"
    conflicts: list[str] = []

    def _theirs(owners: dict[str, str], row_id: str) -> bool:
        owner = owners.get(row_id)
        if not owner:
            return False
        ok, _reason = should_apply_remote_write({"region": owner}, region=here, policy=policy)
        return not ok

    remote_ep = backend.episode_digest()
    owners_ep = backend.row_regions("kazma_episodes") if guarded else {}
    local_ep: dict[str, float] = {}
    behind_ep: list[tuple[float, str]] = []
    for eid, tier, user, assistant, summary, created in conn.execute(
        "SELECT id, tier, user_text, assistant_text, summary_text, created_at FROM episodes"
    ):
        local_ep[eid] = float(created or 0)
        if remote_ep.get(eid) != _episode_digest(tier, user, assistant, summary):
            if _theirs(owners_ep, eid):
                conflicts.append(eid)
            else:
                behind_ep.append((float(created or 0), eid))

    remote_bel = backend.belief_digest()
    owners_bel = backend.row_regions("kazma_beliefs") if guarded else {}
    local_bel: set[str] = set()
    behind_bel: list[tuple[float, str]] = []
    for bid, obj, until, invalidated, ingested in conn.execute(
        "SELECT id, object, valid_until, invalidated_at, ingested_at FROM beliefs"
    ):
        local_bel.add(bid)
        mine = (until is None and invalidated is None,
                hashlib.md5(str(_pg_text(obj) or "").encode("utf-8"), usedforsecurity=False).hexdigest())
        if remote_bel.get(bid) != mine:
            if _theirs(owners_bel, bid):
                conflicts.append(bid)
            else:
                behind_bel.append((float(ingested or 0), bid))

    budget = max(0, int(limit))
    pushed_bel = 0
    # In batches, as the outbox drain pushes (one region read, one
    # transaction per 500): row by row a restore's thousands took minutes.
    ep_ids = [eid for _, eid in sorted(behind_ep, reverse=True)[:budget]]
    pushed_ep = len(_push_episodes(conn, backend, ep_ids)) if ep_ids else 0
    failed = len(ep_ids) - pushed_ep
    for _, bid in sorted(behind_bel, reverse=True)[: max(0, budget - pushed_ep - failed)]:
        if remirror_belief_by_id(conn, bid):
            pushed_bel += 1
        else:
            failed += 1

    stats = {
        "episodes_pushed": pushed_ep,
        "beliefs_pushed": pushed_bel,
        "failed": failed,
        "episodes_behind": len(behind_ep) - pushed_ep,
        "beliefs_behind": len(behind_bel) - pushed_bel,
        "mirror_only_episodes": len(set(remote_ep) - set(local_ep)),
        "mirror_only_beliefs": len(set(remote_bel) - local_bel),
        "at": time.time(),
        "verified_at": time.time(),
    }
    if policy == "origin_wins":
        stats["kept_by_origin"] = len(conflicts)
    elif policy == "fail_closed":
        stats["region_conflicts"] = len(conflicts)
    if policy == "fail_closed" and conflicts:
        _report_region_conflicts(conflicts, here)
    if pushed_ep or pushed_bel:
        logger.info(
            "[memory] mirror sync: pushed %d memories and %d facts the Postgres mirror "
            "was missing or held stale; %d memories and %d facts only in the mirror (left alone)",
            pushed_ep, pushed_bel, stats["mirror_only_episodes"], stats["mirror_only_beliefs"],
        )
    if failed:
        logger.warning("[memory] mirror sync: %d pushes failed; the next pass retries them", failed)
    return stats


def _report_region_conflicts(ids: list[str], here: str) -> None:
    """``fail_closed``: a local change another region's row refuses is the
    operator's to settle -- said once per pass here, and paged (6-hour
    cooldown). ``origin_wins`` is the same refusal made quietly, by choice."""
    logger.warning(
        "[memory] mirror sync: %d local change(s) not mirrored -- another region wrote "
        "those rows and the conflict policy is fail_closed (this region %r; first id %s)",
        len(ids), here, ids[0],
    )
    try:
        from kazma_core.observability.ops_alerts import alert

        alert(
            "memory.region_conflict",
            "Memory mirror: changes another region owns were refused",
            f"{len(ids)} local memory change(s) were not mirrored: another region wrote "
            f"those rows, and memory.backends.state.conflict_policy is fail_closed. "
            f"This region: {here or '(unset)'}.",
            severity="warn",
            cooldown_s=6 * 3600,
        )
    except ImportError:
        logger.debug("[memory] ops alerts unavailable", exc_info=True)


def last_mirror_sync() -> dict[str, Any]:
    """What the last :func:`sync_state_mirror` pass found (memory health)."""
    return dict(_last_sync)


def reconcile_state_beliefs(conn: Any, *, dry_run: bool = False) -> dict[str, int]:
    """One-shot heal: make the mirror match the SQLite SoT exactly.

    - ids only in the mirror            → hard-deleted
    - shared ids with divergent flags   → re-pushed from the local row
    - ids only in SQLite                → inserted (full row)

    Returns stats. Safe to run while the server is up (short transactions).
    """
    stats = {
        "sqlite_rows": 0,
        "mirror_rows": 0,
        "inserted": 0,
        "tombstoned": 0,
        "deleted_mirror_only": 0,
        "errors": 0,
        "dry_run": 1 if dry_run else 0,
    }
    try:
        backend = get_state_backend()
        if not getattr(backend, "available", False):
            stats["errors"] = 1
            return stats
        lite = {
            r["id"]: dict(r)
            for r in conn.execute("SELECT * FROM beliefs").fetchall()
        }
        stats["sqlite_rows"] = len(lite)

        mirror = getattr(backend, "mirror_belief_snapshot", None)
        mirror = mirror() if callable(mirror) else {}
        stats["mirror_rows"] = len(mirror)

        for bid in list(mirror.keys()):
            if bid in lite:
                continue
            stats["deleted_mirror_only"] += 1
            if not dry_run:
                unmirror_belief_to_state(bid)

        for bid, row in lite.items():
            live_mirror = mirror.get(bid)
            live_local = (
                row.get("invalidated_at") is None and row.get("valid_until") is None
            )
            needs_push = (
                bid not in mirror
                or (live_mirror != live_local)
            )
            if not needs_push:
                continue
            if live_mirror is not None and live_mirror != live_local:
                stats["tombstoned"] += 1
            else:
                stats["inserted"] += 1
            if dry_run:
                continue
            ok = mirror_belief_to_state(row)
            if not ok:
                stats["errors"] += 1
    except Exception:
        logger.warning("[state_backend] reconcile failed", exc_info=True)
        stats["errors"] += 1
    return stats


def mirror_drift_summary(conn: Any, *, sample: int = 5) -> dict[str, Any]:
    """Cheap nightly assertion input: count mirror rows that disagree with
    the SQLite SoT (missing locally / dead-locally-but-live-in-mirror)."""
    out: dict[str, Any] = {
        "only_in_mirror": 0,
        "dead_mismatch": 0,
        "checked": 0,
        "samples": [],
    }
    try:
        backend = get_state_backend()
        if not getattr(backend, "available", False):
            out["skipped"] = "backend unavailable"
            return out
        snapshot = getattr(backend, "mirror_belief_snapshot", None)
        mirror = snapshot() if callable(snapshot) else {}
        lite = {
            r["id"]: (r["invalidated_at"] is None and r["valid_until"] is None)
            for r in conn.execute(
                "SELECT id, invalidated_at, valid_until FROM beliefs"
            ).fetchall()
        }
        out["checked"] = len(mirror)
        for bid, m_live in mirror.items():
            l_live = lite.get(bid)
            if l_live is None:
                out["only_in_mirror"] += 1
                if len(out["samples"]) < sample:
                    out["samples"].append({"id": bid, "why": "mirror_only"})
            elif m_live and not l_live:
                out["dead_mismatch"] += 1
                if len(out["samples"]) < sample:
                    out["samples"].append({"id": bid, "why": "mirror_live_sqlite_dead"})
    except Exception:
        logger.debug("[state_backend] drift summary failed", exc_info=True)
        out["errors"] = 1
    return out


def backfill_state_mirror(*, tenant_id: str = "default") -> dict[str, Any]:
    """One-shot: push existing SQLite beliefs + episodes into the Postgres mirror.

    The dual-mirror is write-forward only — it copies rows created AFTER it was
    enabled, so existing memory is left behind. This backfill closes that gap so
    the mirror isn't near-empty right after enabling, and re-running it re-syncs
    after bulk edits. Idempotent (mirror_*_to_state use ``ON CONFLICT (id) DO
    UPDATE``). SQLite stays the source of truth; this only writes copies.
    """
    be = get_state_backend()
    if type(be).__name__ == "_NullStateBackend" or not getattr(be, "available", False):
        return {
            "ok": False,
            "synced": 0,
            "error": (
                "Postgres state backend not available — set provider=postgres "
                "and a working DSN, then Save"
            ),
        }
    try:
        import sqlite3

        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db())
        conn.row_factory = sqlite3.Row
        # Shared pragma set (audit L-23): a bare connect without
        # busy_timeout can raise "database is locked" under WAL contention.
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(conn)
        ep_rows = conn.execute(
            """
            SELECT id, tenant_id, session_id, turn_number, user_text,
                   assistant_text, summary_text, tier, structural_importance,
                   created_at, metadata_json
            FROM episodes WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchall()
        bel_rows = conn.execute(
            """
            SELECT id, tenant_id, subject, predicate, predicate_type, object,
                   confidence, structural_importance, source_trust_weight,
                   valid_from, valid_until, invalidated_at, metadata_json
            FROM beliefs WHERE tenant_id = ?
            """,
            (tenant_id,),
        ).fetchall()
        conn.close()
    except Exception as exc:
        return {"ok": False, "synced": 0, "error": f"read sqlite failed: {exc}"[:300]}

    ep_ok = ep_fail = bel_ok = bel_fail = 0
    for r in ep_rows:
        try:
            if mirror_episode_to_state(dict(r)):
                ep_ok += 1
            else:
                ep_fail += 1
        except Exception:
            ep_fail += 1
    for r in bel_rows:
        try:
            if mirror_belief_to_state(dict(r)):
                bel_ok += 1
            else:
                bel_fail += 1
        except Exception:
            bel_fail += 1

    total_ok = ep_ok + bel_ok
    total_fail = ep_fail + bel_fail
    detail = (
        f"Synced {total_ok} rows ({bel_ok} beliefs, {ep_ok} episodes) to Postgres"
        + (f", {total_fail} failed" if total_fail else "")
    )
    return {
        "ok": total_ok > 0 or total_fail == 0,
        "synced": total_ok,
        "beliefs": bel_ok,
        "episodes": ep_ok,
        "failed": total_fail,
        "detail": detail,
    }


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
