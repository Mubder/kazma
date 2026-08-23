"""Optional shared state backend — multi-replica foundation (P2-2).

Default remains local SQLite (``memory_state.db``). When
``memory.backends.state.provider=postgres`` and a DSN is set, writes can
**dual-mirror** beliefs/episodes to Postgres so multiple app processes share
a durable copy for APIs / future cutover.

Default recall still prefers SQLite FTS/dense with this module as a
**mirror + ILIKE assist**. Set ``memory.backends.state.role=primary``
(or ``KAZMA_MEMORY_STATE_ROLE=primary``) to make StateBackend the
recall SoT — fail-closed if Postgres is down (no silent SQLite lie).
"""

from __future__ import annotations

import json
import logging
import threading
import time

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

    def delete_belief(self, row_id: str) -> bool:
        del row_id
        return False

    def mirror_belief_snapshot(self) -> dict[str, bool]:
        return {}

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
        self._ready_at: float = 0.0
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
            logger.info("[state_backend] skip %s %s: %s", table, row_id, reason)
        return ok

    def mirror_episode(self, row: dict[str, Any]) -> bool:
        if not self._dsn or not row.get("id"):
            return False
        try:
            conn = self._connect()
            try:
                self._ensure(conn)
                if not self._may_write(conn, "kazma_episodes", str(row.get("id"))):
                    return False
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
                        _stamp_region_meta(row),
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


def state_role(cfg: dict[str, Any] | None = None) -> str:
    """``mirror`` (default) or ``primary``."""
    st = (cfg or _cfg()).get("state") or {}
    role = str(st.get("role") or "mirror").strip().lower()
    return role if role in ("primary", "mirror") else "mirror"


def is_state_primary(cfg: dict[str, Any] | None = None) -> bool:
    return state_role(cfg) == "primary"


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

    * ``last_write_wins`` — always apply (default).
    * ``origin_wins`` — first writer / same region keeps the row.
    * ``fail_closed`` — refuse when another region already owns the id.
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
    meta["updated_at"] = time.time()
    meta["conflict_policy"] = state_conflict_policy()
    return json.dumps(meta, ensure_ascii=False)


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


def unmirror_belief_to_state(row_id: str) -> bool:
    """Hard-delete a mirrored belief whose SQLite row no longer exists."""
    try:
        return bool(get_state_backend().delete_belief(row_id))
    except Exception:
        return False


def remirror_belief_by_id(conn: Any, belief_id: str) -> bool:
    """Tombstone/sync primitive: push a belief's CURRENT local row to the
    mirror — including its death flags (valid_until / invalidated_at).

    Call after ANY local life/death transition (supersede close, invalidate,
    restore, edit). A row that vanished from SQLite mid-flight is treated as
    a delete. Never raises.
    """
    try:
        row = conn.execute(
            "SELECT * FROM beliefs WHERE id = ?", (belief_id,)
        ).fetchone()
        if row is None:
            return unmirror_belief_to_state(belief_id)
        d = dict(row)
        if isinstance(d.get("metadata_json"), str) and "metadata" not in d:
            d["metadata_json"] = d["metadata_json"]
        return mirror_belief_to_state(d)
    except Exception:
        logger.debug("[state_backend] remirror failed for %s", belief_id, exc_info=True)
        return False


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
    if type(be).__name__ == "NullStateBackend" or not getattr(be, "available", False):
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
