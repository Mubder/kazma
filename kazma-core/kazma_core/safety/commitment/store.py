"""Commitment store + TTL/GC (Commitment Layer §3.9 — the Phase 2 ship-blocker).

Persists :class:`Commitment` objects on the existing ops SQLite plane
(``memory_ops.db`` via :func:`kazma_core.paths.memory_ops_db`) — NOT a new DB
file, NOT checkpointer-only (plan §R1.2 #6). Uses short-lived per-op
connections (same pattern as ``memory/task_queue.py``) so background GC writes
don't WAL-contend with chat recall reads on ``memory_state.db`` (AGENTS.md §15D
split-DB design is load-bearing).

The TTL/GC policy here is BINDING (plan §3.9): without it implemented, the
commitment table must not ship to production. Tests in
``tests/test_commitment_store_gc.py`` cover every rule.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from kazma_core.paths import memory_ops_db

logger = logging.getLogger(__name__)

__all__ = [
    "Commitment",
    "TTL_DEFAULTS",
    "ttl_for_status",
    "ensure_commitment_schema",
    "create_commitment",
    "get_commitment",
    "update_status",
    "list_by_thread",
    "sweep_expired",
    "enforce_pending_cap",
    "abort_pending_for_thread",
    "delete_retained",
]

# ── statuses / policy decisions ────────────────────────────────────────────
STATUSES = ("draft", "needs_clarify", "needs_confirm", "ready",
            "committed", "aborted", "expired")
# Statuses that count toward the per-thread pending cap + can be expired/superseded.
PENDING_STATUSES = ("draft", "needs_clarify", "needs_confirm", "ready")
# Acts retained long-term under the critical-retention tier (§3.9 rule 3).
CRITICAL_ACTS = ("revise_fact", "config_change", "soul_delta", "identity")

# TTL defaults in seconds, by status (plan §3.9). ``None`` = no expiry (terminal).
TTL_DEFAULTS: dict[str, float | None] = {
    "draft": 3600.0,           # 1 hour
    "needs_clarify": 86400.0,  # 24 hours
    "needs_confirm": 86400.0,  # 24 hours
    "ready": 900.0,            # 15 minutes
    "committed": None,
    "aborted": None,
    "expired": None,
}

# Retention defaults (days) — tiered (§3.9 rule 3).
RETENTION_EPHEMERAL_DAYS = 30
RETENTION_CRITICAL_DAYS = 365
PENDING_CAP_PER_THREAD = 20


def ttl_for_status(status: str, cfg: dict[str, Any] | None = None) -> float | None:
    """Resolve the TTL (seconds) for a status, allowing config override."""
    overrides = ((cfg or {}).get("agent") or {}).get("commitment") or {}
    ttl_cfg = overrides.get("ttl") or {}
    if status in ttl_cfg and ttl_cfg[status] is not None:
        try:
            return float(ttl_cfg[status])
        except (TypeError, ValueError):
            pass
    return TTL_DEFAULTS.get(status)


@dataclass
class Commitment:
    """A structured decision to affect the world (plan §3.3)."""
    commitment_id: str = ""
    thread_id: str = ""
    turn_id: str | None = None
    parent_commitment_id: str | None = None
    act: str = ""
    status: str = "draft"
    goal_text: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    policy_decision: str = "allow"
    confidence: float = 0.0
    tool_name: str = ""
    args_digest: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    request_at: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float | None = None
    resolved_at: float | None = None
    tenant_id: str = "default"


# ── schema ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commitments (
  commitment_id        TEXT PRIMARY KEY,
  thread_id            TEXT NOT NULL,
  turn_id              TEXT,
  parent_commitment_id TEXT,
  act                  TEXT NOT NULL,
  status               TEXT NOT NULL,
  goal_text            TEXT,
  slots_json           TEXT,
  evidence_json        TEXT,
  conflicts_json       TEXT,
  policy_decision      TEXT,
  confidence           REAL,
  tool_name            TEXT,
  args_digest          TEXT,
  result_json          TEXT,
  request_at           REAL NOT NULL,
  created_at           REAL NOT NULL,
  updated_at           REAL NOT NULL,
  expires_at           REAL,
  resolved_at          REAL,
  tenant_id            TEXT NOT NULL DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_commitments_thread ON commitments(thread_id, status);
CREATE INDEX IF NOT EXISTS idx_commitments_expires ON commitments(expires_at)
  WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments(status);

CREATE TABLE IF NOT EXISTS commitment_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  commitment_id TEXT NOT NULL,
  event_type    TEXT NOT NULL,
  payload_json  TEXT,
  created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commitment_events_cid ON commitment_events(commitment_id);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def ensure_commitment_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create the commitments tables if absent. Idempotent."""
    own = conn is None
    if own:
        conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        if own:
            conn.close()


# ── row (de)serialization ──────────────────────────────────────────────────

def _row_to_commitment(row: sqlite3.Row) -> Commitment:
    def _loads(s):
        return json.loads(s) if s else None
    return Commitment(
        commitment_id=row["commitment_id"],
        thread_id=row["thread_id"],
        turn_id=row["turn_id"],
        parent_commitment_id=row["parent_commitment_id"],
        act=row["act"],
        status=row["status"],
        goal_text=row["goal_text"] or "",
        slots=_loads(row["slots_json"]) or {},
        evidence=_loads(row["evidence_json"]) or {},
        conflicts=_loads(row["conflicts_json"]) or [],
        policy_decision=row["policy_decision"] or "allow",
        confidence=row["confidence"] or 0.0,
        tool_name=row["tool_name"] or "",
        args_digest=row["args_digest"] or "",
        result=_loads(row["result_json"]) or {},
        request_at=row["request_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
        resolved_at=row["resolved_at"],
        tenant_id=row["tenant_id"],
    )


# ── CRUD ───────────────────────────────────────────────────────────────────

def _emit_event(conn: sqlite3.Connection, cid: str, event_type: str,
                payload: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO commitment_events (commitment_id, event_type, payload_json, created_at) "
        "VALUES (?, ?, ?, ?)",
        (cid, event_type, json.dumps(payload) if payload else None, time.time()),
    )


def create_commitment(c: Commitment, *, cfg: dict[str, Any] | None = None) -> str:
    """Insert a commitment, stamping ids/timestamps + expires_at by status.

    Returns the commitment_id. Idempotent on the id (INSERT OR REPLACE so a
    re-create with a known id upserts — used by resume-after-clarify).
    """
    ensure_commitment_schema()
    now = time.time()
    if not c.commitment_id:
        c.commitment_id = "cmt_" + uuid.uuid4().hex[:20]
    if not c.created_at:
        c.created_at = now
    c.updated_at = now
    if not c.request_at:
        c.request_at = now
    ttl = ttl_for_status(c.status, cfg)
    if c.expires_at is None and ttl is not None:
        c.expires_at = now + ttl
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO commitments
               (commitment_id, thread_id, turn_id, parent_commitment_id, act, status,
                goal_text, slots_json, evidence_json, conflicts_json, policy_decision,
                confidence, tool_name, args_digest, result_json, request_at,
                created_at, updated_at, expires_at, resolved_at, tenant_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (c.commitment_id, c.thread_id, c.turn_id, c.parent_commitment_id,
             c.act, c.status, c.goal_text, json.dumps(c.slots, ensure_ascii=False),
             json.dumps(c.evidence, ensure_ascii=False),
             json.dumps(c.conflicts, ensure_ascii=False), c.policy_decision,
             c.confidence, c.tool_name, c.args_digest,
             json.dumps(c.result, ensure_ascii=False), c.request_at,
             c.created_at, c.updated_at, c.expires_at, c.resolved_at, c.tenant_id),
        )
        _emit_event(conn, c.commitment_id, "created", {"act": c.act, "status": c.status})
        conn.commit()
    return c.commitment_id


def get_commitment(commitment_id: str) -> Commitment | None:
    ensure_commitment_schema()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM commitments WHERE commitment_id=?", (commitment_id,),
        ).fetchone()
    return _row_to_commitment(row) if row else None


def update_status(
    commitment_id: str, status: str, *,
    event_type: str | None = None, payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None, cfg: dict[str, Any] | None = None,
) -> Commitment | None:
    """Transition a commitment to *status*, recompute expires_at, emit an event.

    Terminal transitions (committed/aborted/expired) stamp resolved_at and clear
    expires_at. ``event_type`` defaults to the status name.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown commitment status: {status}")
    ensure_commitment_schema()
    now = time.time()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM commitments WHERE commitment_id=?", (commitment_id,),
        ).fetchone()
        if row is None:
            return None
        is_terminal = status in ("committed", "aborted", "expired")
        ttl = None if is_terminal else ttl_for_status(status, cfg)
        expires_at = None if is_terminal else (
            now + ttl if ttl is not None else None
        )
        resolved_at = now if is_terminal else None
        result_json = json.dumps(result, ensure_ascii=False) if result is not None else row["result_json"]
        conn.execute(
            """UPDATE commitments SET status=?, updated_at=?, expires_at=?,
               resolved_at=?, result_json=? WHERE commitment_id=?""",
            (status, now, expires_at, resolved_at, result_json, commitment_id),
        )
        _emit_event(conn, commitment_id, event_type or status, payload or {})
        conn.commit()
    return get_commitment(commitment_id)


def list_by_thread(thread_id: str, *, status: str | None = None,
                   tenant_id: str = "default") -> list[Commitment]:
    ensure_commitment_schema()
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM commitments WHERE thread_id=? AND tenant_id=? AND status=? "
                "ORDER BY created_at",
                (thread_id, tenant_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM commitments WHERE thread_id=? AND tenant_id=? "
                "ORDER BY created_at",
                (thread_id, tenant_id),
            ).fetchall()
    return [_row_to_commitment(r) for r in rows]


# ── TTL / GC (plan §3.9 — binding) ─────────────────────────────────────────

def sweep_expired(now: float | None = None, *, cfg: dict[str, Any] | None = None) -> int:
    """§3.9 rule 1+2: expire pending commitments past their TTL, emit events.

    Returns the count expired. The interrupt-coupling (fail-closed any open
    interrupt for an expired commitment) is the caller's job — the gate checks
    expiry on resume (an approve after expires_at is denied).
    """
    ensure_commitment_schema()
    now = now if now is not None else time.time()
    expired_count = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT commitment_id FROM commitments "
            "WHERE status IN ('draft','needs_clarify','needs_confirm','ready') "
            "AND expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        ).fetchall()
        for r in rows:
            cid = r["commitment_id"]
            conn.execute(
                "UPDATE commitments SET status='expired', updated_at=?, resolved_at=?, "
                "expires_at=? WHERE commitment_id=?",
                (now, now, now, cid),
            )
            _emit_event(conn, cid, "expired", {"swept_at": now})
            expired_count += 1
        conn.commit()
    if expired_count:
        logger.info("[commitment] sweep_expired: %d commitment(s) expired", expired_count)
    return expired_count


def enforce_pending_cap(thread_id: str, *, cap: int | None = None,
                        tenant_id: str = "default") -> int:
    """§3.9 rule 6: cap pending commitments per thread; abort oldest over cap."""
    ensure_commitment_schema()
    cap = cap if cap is not None else PENDING_CAP_PER_THREAD
    now = time.time()
    aborted = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT commitment_id FROM commitments "
            "WHERE thread_id=? AND tenant_id=? AND status IN "
            "('draft','needs_clarify','needs_confirm','ready') "
            "ORDER BY created_at",
            (thread_id, tenant_id),
        ).fetchall()
        if len(rows) <= cap:
            return 0
        for r in rows[: len(rows) - cap]:  # oldest over the cap
            cid = r["commitment_id"]
            conn.execute(
                "UPDATE commitments SET status='aborted', updated_at=?, resolved_at=?, "
                "expires_at=? WHERE commitment_id=?",
                (now, now, now, cid),
            )
            _emit_event(conn, cid, "aborted", {"reason": "pending_cap"})
            aborted += 1
        conn.commit()
    if aborted:
        logger.info("[commitment] pending_cap: aborted %d oldest for %s", aborted, thread_id)
    return aborted


def abort_pending_for_thread(thread_id: str, *, reason: str = "superseded_by_new_turn",
                             tenant_id: str = "default") -> int:
    """§3.9 rule 5: a new user turn supersedes pending HITL — abort pending."""
    ensure_commitment_schema()
    now = time.time()
    aborted = 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT commitment_id FROM commitments "
            "WHERE thread_id=? AND tenant_id=? AND status IN "
            "('draft','needs_clarify','needs_confirm','ready')",
            (thread_id, tenant_id),
        ).fetchall()
        for r in rows:
            cid = r["commitment_id"]
            conn.execute(
                "UPDATE commitments SET status='aborted', updated_at=?, resolved_at=?, "
                "expires_at=? WHERE commitment_id=?",
                (now, now, now, cid),
            )
            _emit_event(conn, cid, "aborted", {"reason": reason})
            aborted += 1
        conn.commit()
    return aborted


def delete_retained(now: float | None = None, *,
                    ephemeral_days: int | None = None,
                    critical_days: int | None = None) -> int:
    """§3.9 rule 3+4: tiered hard-GC of terminal commitments past retention.

    Ephemeral acts (remind/answer_only/...) hard-delete after ``ephemeral_days``
    (default 30). Critical acts (revise_fact/config_change/soul/identity) are
    retained ``critical_days`` (default 365) before hard-delete. Rows are
    deleted from BOTH commitments and commitment_events.
    """
    ensure_commitment_schema()
    now = now if now is not None else time.time()
    ephemeral_days = ephemeral_days if ephemeral_days is not None else RETENTION_EPHEMERAL_DAYS
    critical_days = critical_days if critical_days is not None else RETENTION_CRITICAL_DAYS
    ephem_cut = now - ephemeral_days * 86400
    critical_cut = now - critical_days * 86400
    deleted = 0
    with _connect() as conn:
        # Ephemeral-terminal rows past ephemeral retention
        rows = conn.execute(
            "SELECT commitment_id FROM commitments "
            "WHERE status IN ('committed','aborted','expired') "
            "AND act NOT IN (%s) AND resolved_at IS NOT NULL AND resolved_at < ?"
            % ",".join("?" for _ in CRITICAL_ACTS),
            (*CRITICAL_ACTS, ephem_cut),
        ).fetchall()
        # Critical-terminal rows past critical retention
        crit_rows = conn.execute(
            "SELECT commitment_id FROM commitments "
            "WHERE status IN ('committed','aborted','expired') "
            "AND act IN (%s) AND resolved_at IS NOT NULL AND resolved_at < ?"
            % ",".join("?" for _ in CRITICAL_ACTS),
            (*CRITICAL_ACTS, critical_cut),
        ).fetchall()
        for r in list(rows) + list(crit_rows):
            cid = r["commitment_id"]
            conn.execute("DELETE FROM commitment_events WHERE commitment_id=?", (cid,))
            conn.execute("DELETE FROM commitments WHERE commitment_id=?", (cid,))
            _emit_event(conn, cid, "gc", {})
            deleted += 1
        conn.commit()
    if deleted:
        logger.info("[commitment] delete_retained: hard-deleted %d terminal rows", deleted)
    return deleted
