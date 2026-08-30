"""Durable turn-artifact store (context-integrity S1-2).

The fix that makes context loss survivable. Scratchpad findings and
outbound-draft proposals live in SQLite keyed ``(tenant_id, thread_id, key)``
so they survive deterministic trim, turn boundaries, process restarts, and a
corrupt LangGraph checkpoint. The graph state holds a read-through cache; the
store is the durable source of truth.

House patterns (not optional — AGENTS.md §8/§15E/§21F):
  - Connection opened through ``apply_sqlite_pragmas()`` (WAL +
    busy_timeout=5000 + synchronous=NORMAL) — the shared helper every other
    store uses; never hand-roll the pragmas.
  - DB lives under ``kazma-data/`` → covered by the universal WAL-safe
    backup for free, no new backup path.
  - Writes here are turn-artifact writes, off the hot chat-recall read path
    (the ops/state split rationale): short-lived per-call connections, no
    long-lived cursors.

Env override ``KAZMA_ARTIFACTS_DB`` (absolute path) for tests.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

__all__ = [
    "ArtifactStore",
    "get_artifact_store",
    "reset_artifact_store",
]

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_artifacts (
    tenant_id    TEXT NOT NULL,
    thread_id    TEXT NOT NULL,
    key          TEXT NOT NULL,
    value        TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'finding',
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (tenant_id, thread_id, key)
);
CREATE INDEX IF NOT EXISTS idx_agent_artifacts_key ON agent_artifacts(key);
CREATE INDEX IF NOT EXISTS idx_agent_artifacts_updated ON agent_artifacts(updated_at);
"""

# Retention (GC is wired into the existing commitment-GC cadence, not a new
# sweeper): per-thread cap + age-out. Scratchpad entries churn fast; a
# proposal awaiting approval must NOT age out from under a pending card, so
# proposals get a longer horizon.
_MAX_PER_THREAD = 128
_MAX_AGE_DAYS_FINDING = 14.0
_MAX_AGE_DAYS_PROPOSAL = 90.0

_PROPOSAL_PREFIX = "proposal:"


def _content_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()[:32]


class ArtifactStore:
    """SQLite-backed durable store for scratchpad findings and proposals."""

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._ensure_schema()

    # ── internals ────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        try:
            from kazma_core.config_store import apply_sqlite_pragmas

            apply_sqlite_pragmas(conn)
        except Exception:  # pragma: no cover - helper always present in prod
            pass
        return conn

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _put(
        self,
        tenant_id: str,
        thread_id: str,
        key: str,
        value: str,
        kind: str,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_artifacts
                    (tenant_id, thread_id, key, value, kind, created_at, updated_at, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, thread_id, key) DO UPDATE SET
                    value = excluded.value,
                    kind = excluded.kind,
                    updated_at = excluded.updated_at,
                    content_hash = excluded.content_hash
                """,
                (
                    tenant_id or "default",
                    thread_id or "_default",
                    str(key),
                    str(value),
                    kind,
                    now,
                    now,
                    _content_hash(str(value)),
                ),
            )

    # ── scratchpad ───────────────────────────────────────────────────

    def put_scratchpad(
        self, tenant_id: str, thread_id: str, key: str, value: str
    ) -> None:
        """Durable write-through from apply_scratchpad_write (kind=finding)."""
        self._put(tenant_id, thread_id, f"scratchpad:{key[:80]}", value, "finding")

    def list_scratchpad(
        self, thread_id: str, *, tenant_id: str = "default"
    ) -> dict[str, str]:
        """Read-through for the working-memory anchor: key → value."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT key, value FROM agent_artifacts
                WHERE tenant_id = ? AND thread_id = ?
                  AND kind = 'finding' AND key LIKE 'scratchpad:%'
                ORDER BY updated_at ASC
                """,
                (tenant_id or "default", thread_id or "_default"),
            ).fetchall()
        out: dict[str, str] = {}
        for k, v in rows:
            out[str(k)[len("scratchpad:"):]] = str(v)
        return out

    # ── proposals (S1-3) ─────────────────────────────────────────────

    def save_proposal(
        self,
        tenant_id: str,
        thread_id: str,
        kind: str,
        items: list[Any],
    ) -> dict[str, Any]:
        """Persist an enumerated set of outbound drafts; returns stable IDs.

        The proposal is one artifact row whose value is JSON:
        ``{"kind": ..., "items": [{"id": ..., "text": ...}, ...]}``.
        IDs resolve across threads/restarts — approval must never depend on
        the drafts still being in conversation context.
        """
        clean = [str(i).strip() for i in (items or []) if str(i).strip()]
        if not clean:
            raise ValueError("save_proposal requires at least one non-empty item")
        proposal_id = f"prop_{uuid.uuid4().hex[:12]}"
        payload = {
            "proposal_id": proposal_id,
            "kind": str(kind or "drafts")[:40],
            "items": [
                {"id": f"{proposal_id}:{n}", "text": t[:8000]}
                for n, t in enumerate(clean, start=1)
            ],
            "created_at": time.time(),
            "thread_id": thread_id or "",
        }
        self._put(
            tenant_id,
            thread_id,
            f"{_PROPOSAL_PREFIX}{proposal_id}",
            json.dumps(payload, ensure_ascii=False),
            "proposal",
        )
        return payload

    def resolve_proposal(
        self, ref: str, *, tenant_id: str = "default"
    ) -> dict[str, Any] | None:
        """Resolve a proposal id, a single item id, or the id + '#N' form.

        Returns ``{"proposal_id", "kind", "items": [...]}`` (single-item refs
        return a one-item list) or None when the id does not resolve.
        """
        ref = str(ref or "").strip()
        if not ref:
            return None
        item_no: int | None = None
        if "#" in ref:
            base, _, num = ref.partition("#")
            try:
                item_no = int(num)
                ref = base.strip()
            except ValueError:
                item_no = None
        # Full item ids ("prop_x:3") decompose into proposal key + item number.
        base, sep, tail = ref.rpartition(":")
        if sep and base.startswith("prop_") and tail.isdigit():
            try:
                item_no = int(tail)
                ref = base
            except ValueError:
                pass
        key = ref if ref.startswith(_PROPOSAL_PREFIX) else f"{_PROPOSAL_PREFIX}{ref}"
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT value FROM agent_artifacts
                WHERE key = ? ORDER BY updated_at DESC LIMIT 1
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row[0]))
        except Exception:
            return None
        items = list(payload.get("items") or [])
        if item_no is not None:
            items = [i for i in items if str(i.get("id", "")).endswith(f":{item_no}")]
            if not items:
                return None
        return {
            "proposal_id": str(payload.get("proposal_id") or ref),
            "kind": str(payload.get("kind") or "drafts"),
            "items": items,
            "texts": [str(i.get("text") or "") for i in items],
        }

    def proposal_posted(self, ref: str, *, tenant_id: str = "default") -> None:
        """Mark a proposal consumed (posted/sent) — kept for audit, not deleted."""
        info = self.resolve_proposal(ref, tenant_id=tenant_id)
        if not info:
            return
        pid = info["proposal_id"]
        # rewrite kind → proposal_posted via direct update (idempotent)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_artifacts SET kind = 'proposal_posted', updated_at = ?
                WHERE key = ?
                """,
                (time.time(), f"{_PROPOSAL_PREFIX}{pid}"),
            )

    # ── GC (wired into the commitment-GC cadence, not a new sweeper) ──

    def gc_sweep(
        self,
        *,
        max_per_thread: int = _MAX_PER_THREAD,
    ) -> dict[str, int]:
        """Per-thread cap + age-out. Returns counts of evicted rows."""
        now = time.time()
        evicted = 0
        with self._connect() as conn:
            # age-out by kind
            for kind, days in (
                ("finding", _MAX_AGE_DAYS_FINDING),
                ("proposal", _MAX_AGE_DAYS_PROPOSAL),
                ("proposal_posted", _MAX_AGE_DAYS_FINDING),
            ):
                cur = conn.execute(
                    "DELETE FROM agent_artifacts WHERE kind = ? AND updated_at < ?",
                    (kind, now - days * 86400.0),
                )
                evicted += cur.rowcount or 0
            # per-thread cap (oldest first)
            threads = conn.execute(
                "SELECT DISTINCT tenant_id, thread_id FROM agent_artifacts"
            ).fetchall()
            for tenant, thread in threads:
                n = conn.execute(
                    "SELECT COUNT(*) FROM agent_artifacts WHERE tenant_id=? AND thread_id=?",
                    (tenant, thread),
                ).fetchone()[0]
                if n > max_per_thread:
                    cur = conn.execute(
                        """
                        DELETE FROM agent_artifacts WHERE rowid IN (
                            SELECT rowid FROM agent_artifacts
                            WHERE tenant_id=? AND thread_id=?
                            ORDER BY updated_at ASC LIMIT ?
                        )
                        """,
                        (tenant, thread, n - max_per_thread),
                    )
                    evicted += cur.rowcount or 0
        if evicted:
            logger.info("[artifacts] GC evicted %d rows", evicted)
        return {"evicted": evicted}


# ── singleton ────────────────────────────────────────────────────────

_store: ArtifactStore | None = None
_store_lock = threading.Lock()


def _default_db_path() -> str:
    override = (os.environ.get("KAZMA_ARTIFACTS_DB") or "").strip()
    if override:
        return override
    from kazma_core.paths import data_dir

    return os.path.join(str(data_dir()), "agent_artifacts.db")


def get_artifact_store() -> ArtifactStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ArtifactStore(_default_db_path())
    return _store


def reset_artifact_store() -> None:
    """Test helper: drop the singleton so a new env/db path takes effect."""
    global _store
    with _store_lock:
        _store = None
