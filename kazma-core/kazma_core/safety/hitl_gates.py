"""HITL Gate Registry — one gate, one row, one truth.

The single source of truth for the *lifecycle* of every human-in-the-loop
approval gate across all Kazma surfaces (web SSE chat, dashboard, gateway
platforms, TUI, swarm bus, pipeline checkpoints, commitment semantic cards).
Plan SoT: ``docs/plans/HITL_GATE_REGISTRY_PLAN.md``.

Design laws (binding):

* **Decision truth lives here; execution truth lives in the checkpointer.**
  The reconciler converges them; disagreement is metered, never hidden.
* **Every transition is a single compare-and-set UPDATE.** Zero rows
  affected ⇒ :class:`TransitionConflict` carrying the row's *actual* state —
  that IS the HTTP 409 body. No lock ordering to get wrong.
* **``pending`` is the only state a card renders live buttons for.**
* **Ambiguity resolves toward showing a live card and refusing to assume
  approval** (same posture as the default-deny HITL floor).
* **Surfaces render; they never mint.** Gate transitions publish through
  :class:`GateEvents` into the EXISTING turn story (the turn journal), never
  a parallel event stream.
* This registry unifies gate *lifecycle*, not gate *policy* — the danger
  tool list SoT (``CANONICAL_DANGER_TOOLS``) is untouched.

Storage: ``kazma-data/hitl_gates.db`` (WAL + busy_timeout — house pattern).
Single-process truth, exactly like the live turn journal. Short-lived per-op
connections (same pattern as ``memory/task_queue.py`` / ``commitment/store.py``).

Sync core + thin async wrappers via ``asyncio.to_thread`` (§23 — never block
the SelectorEventLoop).

Kill-switch: ``KAZMA_GATE_REGISTRY=0`` (env, checked live) reverts every
consumer to legacy derivation. Mirrors the ``get_hitl_config`` live-read
pattern.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = [
    "GATE_STATES",
    "LIVE_STATES",
    "GateRow",
    "TransitionConflict",
    "GateEvents",
    "gate_events",
    "gate_registry_enabled",
    "make_gate_id",
    "ensure_gate_schema",
    "register_gate",
    "claim_gate",
    "mark_resuming",
    "settle_gate",
    "fail_gate",
    "supersede_gate",
    "set_db_path_for_tests",
    "gate_for",
    "live_gates",
    "pending_gates",
    "expire_due_gates",
    "boot_sweep",
    "boot_sweep_async",
    "register_gate_async",
    "claim_gate_async",
    "mark_resuming_async",
    "settle_gate_async",
    "gate_for_async",
    "live_gates_async",
    "pending_gates_async",
    "expire_due_gates_async",
]

# ── states / transitions ────────────────────────────────────────────────────

GATE_STATES = (
    "pending",     # waiting for a human — the ONLY buttoned state
    "claimed",     # a decision was recorded (CAS winner)
    "resuming",    # the graph/bus resume is in flight
    "settled",     # terminal — outcome in `decision`/`outcome`
    "timeout",     # terminal — TTL expired unanswered (auto-deny posture)
    "superseded",  # terminal — same execution pause re-emitted under a new id
    "error",       # terminal — resume raised; user was told
)

#: States that mean "this gate still matters" (turn must stay open).
LIVE_STATES = ("pending", "claimed", "resuming")

#: Legal CAS transitions: from-state -> allowed to-states.
_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "pending": ("claimed", "timeout", "superseded", "settled"),
    "claimed": ("resuming", "timeout", "settled", "error"),
    "resuming": ("settled", "error"),
}

_DEFAULT_TTL_SECONDS = 30 * 60  # 30 min — matches session grant scale


class TransitionConflict(Exception):
    """A CAS transition affected 0 rows.

    Carries the row's *actual* state (and decision, when claimed) so callers
    can build an honest 409 body without a second read race.
    """

    def __init__(
        self,
        gate_id: str,
        expected: str,
        actual: str | None,
        decision: str = "",
        actor: str = "",
    ) -> None:
        self.gate_id = gate_id
        self.expected = expected
        self.actual = actual  # None = row does not exist
        self.decision = decision
        self.actor = actor
        super().__init__(
            f"gate {gate_id}: expected state={expected!r}, actual={actual!r}"
        )


@dataclass
class GateRow:
    """One approval gate. ``gate_id`` prefers the LangGraph interrupt id."""

    gate_id: str
    thread_id: str
    tool: str
    mechanism: str = "graph"  # graph | swarm_bus | pipeline | semantic
    kind: str = "security"    # security | semantic_clarify | semantic_confirm
    alias_id: str = ""        # pre-pause hash id when it differs (two-id window)
    tenant_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    args_json: str = "{}"
    message: str = ""
    payload_json: str = "{}"
    state: str = "pending"
    decision: str = ""        # approve | deny | yolo | option:<id> | orphaned…
    actor: str = ""
    supersedes: str = ""
    created_at: float = field(default_factory=time.time)
    claimed_at: float | None = None
    settled_at: float | None = None
    expires_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_live(self) -> bool:
        return self.state in LIVE_STATES

    def args(self) -> dict[str, Any]:
        try:
            v = json.loads(self.args_json or "{}")
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}

    def payload(self) -> dict[str, Any]:
        try:
            v = json.loads(self.payload_json or "{}")
            return v if isinstance(v, dict) else {}
        except Exception:
            return {}


# ── kill-switch (live-read; mirrors get_hitl_config) ───────────────────────


def gate_registry_enabled() -> bool:
    """Live check of the ``KAZMA_GATE_REGISTRY`` kill-switch (default ON)."""
    raw = (os.environ.get("KAZMA_GATE_REGISTRY") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return False
    return True


# ── db plumbing ─────────────────────────────────────────────────────────────

_db_path_override: str | None = None
_schema_ready = False
_schema_lock = threading.Lock()
# Serialize register/claim so a native-id row and a hash-id row for the
# SAME pause cannot both insert (SSE scan + ensure_paused_gate race,
# 2026-09-02: two dashboard cards, one Approve 409s "No longer pending").
_write_lock = threading.Lock()


def _db_path() -> str:
    if _db_path_override:
        return _db_path_override
    from kazma_core.paths import data_dir

    return str(data_dir() / "hitl_gates.db")


def set_db_path_for_tests(path: str | None) -> None:
    """Point the registry at a different file (tests only)."""
    global _db_path_override, _schema_ready
    _db_path_override = path
    _schema_ready = False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        from kazma_core.config_store import apply_sqlite_pragmas

        apply_sqlite_pragmas(conn)
    except Exception:  # pragma: no cover — pragmas are best-effort
        pass
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS hitl_gates (
  gate_id      TEXT PRIMARY KEY,
  alias_id     TEXT NOT NULL DEFAULT '',
  thread_id    TEXT NOT NULL,
  tenant_id    TEXT NOT NULL DEFAULT '',
  session_id   TEXT NOT NULL DEFAULT '',
  turn_id      TEXT NOT NULL DEFAULT '',
  mechanism    TEXT NOT NULL DEFAULT 'graph',
  kind         TEXT NOT NULL DEFAULT 'security',
  tool         TEXT NOT NULL,
  args_json    TEXT NOT NULL DEFAULT '{}',
  message      TEXT NOT NULL DEFAULT '',
  payload_json TEXT NOT NULL DEFAULT '{}',
  state        TEXT NOT NULL DEFAULT 'pending',
  decision     TEXT NOT NULL DEFAULT '',
  actor        TEXT NOT NULL DEFAULT '',
  supersedes   TEXT NOT NULL DEFAULT '',
  created_at   REAL NOT NULL,
  claimed_at   REAL,
  settled_at   REAL,
  expires_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_gates_thread ON hitl_gates(thread_id, state);
CREATE INDEX IF NOT EXISTS idx_gates_state  ON hitl_gates(state, expires_at);
CREATE INDEX IF NOT EXISTS idx_gates_alias  ON hitl_gates(alias_id) WHERE alias_id != '';
"""


def ensure_gate_schema() -> None:
    """Idempotent schema init (once per process, thread-safe)."""
    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
            _schema_ready = True
        finally:
            conn.close()


# ── event hook (renders into the EXISTING turn story — never a new stream) ─


class GateEvents:
    """Tiny synchronous fan-out for gate transitions.

    Subscribers are called in-line with ``(event, GateRow)`` where event is
    one of ``gate_pending`` / ``gate_claimed`` / ``gate_resuming`` /
    ``gate_settled`` (settled covers timeout/superseded/error too — the row's
    ``state`` disambiguates). In the web app the subscriber updates the
    ``hitl`` part of the gate's turn in the turn journal — the SAME
    TurnDocument the browser is already tailing. Subscribers must never
    raise into the registry; failures are logged and swallowed.
    """

    def __init__(self) -> None:
        self._subs: list[Callable[[str, GateRow], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[str, GateRow], None]) -> None:
        with self._lock:
            if fn not in self._subs:
                self._subs.append(fn)

    def unsubscribe(self, fn: Callable[[str, GateRow], None]) -> None:
        with self._lock:
            try:
                self._subs.remove(fn)
            except ValueError:
                pass

    def publish(self, event: str, row: GateRow) -> None:
        with self._lock:
            subs = list(self._subs)
        for fn in subs:
            try:
                fn(event, row)
            except Exception:
                logger.exception("[HitlGates] subscriber failed on %s", event)


gate_events = GateEvents()


# ── id helpers ──────────────────────────────────────────────────────────────


def make_gate_id(thread_id: str, tool: str, args: Any = None, *, seq: int = 0) -> str:
    """Deterministic fallback id when no native interrupt id exists yet."""
    try:
        args_repr = json.dumps(args, sort_keys=True, default=str) if args else ""
    except Exception:
        args_repr = str(args)
    h = hashlib.sha256(
        f"{thread_id}|{tool}|{args_repr}|{seq}".encode("utf-8", "replace")
    ).hexdigest()[:16]
    return f"gate-{h}"


def _row_to_gate(r: sqlite3.Row) -> GateRow:
    return GateRow(
        gate_id=r["gate_id"],
        alias_id=r["alias_id"],
        thread_id=r["thread_id"],
        tenant_id=r["tenant_id"],
        session_id=r["session_id"],
        turn_id=r["turn_id"],
        mechanism=r["mechanism"],
        kind=r["kind"],
        tool=r["tool"],
        args_json=r["args_json"],
        message=r["message"],
        payload_json=r["payload_json"],
        state=r["state"],
        decision=r["decision"],
        actor=r["actor"],
        supersedes=r["supersedes"],
        created_at=r["created_at"],
        claimed_at=r["claimed_at"],
        settled_at=r["settled_at"],
        expires_at=r["expires_at"],
    )


# ── core API (sync) ─────────────────────────────────────────────────────────


def register_gate(gate: GateRow, *, ttl_seconds: float | None = None) -> GateRow:
    """Insert a gate row, idempotent on BOTH ``gate_id`` and ``alias_id``.

    A lookup by either id lands on the same row — this is what closes the
    two-id window (hash id pre-pause, LangGraph id post-pause) so one pause
    can never draw two cards.

    If a row already exists under the ``alias_id`` with a provisional (hash)
    ``gate_id`` and the caller now knows the real id, the row is UPGRADED to
    the real ``gate_id`` in place (the alias keeps pointing at it).

    Returns the canonical row (existing or newly created). Publishes
    ``gate_pending`` only on a genuinely new row.
    """
    ensure_gate_schema()
    now = time.time()
    if gate.expires_at is None:
        ttl = _DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        gate.expires_at = now + ttl if ttl and ttl > 0 else None
    created = False
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = _lookup_live_gate(conn, gate)
            if existing is not None:
                if gate.alias_id and existing.gate_id == gate.alias_id:
                    # Row was registered under the provisional (hash) id and
                    # we now know the real id — upgrade in place; the old id
                    # becomes the alias so lookups by either id still land here.
                    conn.execute(
                        "UPDATE hitl_gates SET gate_id = ?, alias_id = ? WHERE gate_id = ?",
                        (gate.gate_id, existing.gate_id, existing.gate_id),
                    )
                    conn.commit()
                    existing.alias_id = existing.gate_id
                    existing.gate_id = gate.gate_id
                else:
                    conn.commit()
                return existing
            # Terminal collision under a HASH id: a NEW pause for the same
            # tool+args (the user asked again after a timeout/deny) must get
            # a fresh row — a settled row must not eat the new question.
            cur = conn.execute(
                "SELECT * FROM hitl_gates WHERE gate_id = ?", (gate.gate_id,)
            )
            r = cur.fetchone()
            if r is not None:
                existing = _row_to_gate(r)
                if existing.is_live:
                    conn.commit()
                    return existing
                if gate.gate_id.startswith("gate-"):
                    gate.alias_id = gate.gate_id
                    gate.gate_id = f"{gate.gate_id}-r{int(now * 1000) % 1_000_000}"
                else:
                    conn.commit()
                    return existing
            # 3) genuinely new
            gate.created_at = now
            conn.execute(
                """INSERT INTO hitl_gates
                   (gate_id, alias_id, thread_id, tenant_id, session_id, turn_id,
                    mechanism, kind, tool, args_json, message, payload_json,
                    state, decision, actor, supersedes,
                    created_at, claimed_at, settled_at, expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    gate.gate_id, gate.alias_id, gate.thread_id, gate.tenant_id,
                    gate.session_id, gate.turn_id, gate.mechanism, gate.kind,
                    gate.tool, gate.args_json, gate.message, gate.payload_json,
                    gate.state, gate.decision, gate.actor, gate.supersedes,
                    gate.created_at, gate.claimed_at, gate.settled_at,
                    gate.expires_at,
                ),
            )
            conn.commit()
            created = True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            conn.close()
    if created:
        gate_events.publish("gate_pending", gate)
    return gate


def _same_pause_args(a: str | None, b: str | None) -> bool:
    """True when two rows carry the same non-empty tool args (twin pause)."""
    left = (a or "").strip()
    right = (b or "").strip()
    if not left or not right or left in ("{}", "null") or right in ("{}", "null"):
        return False
    return left == right


def _lookup_live_gate(conn: sqlite3.Connection, gate: GateRow) -> GateRow | None:
    """Find an already-live row for this pause (id, alias, or same args)."""
    candidates = [x for x in (gate.alias_id, gate.gate_id) if x]
    for cid in candidates:
        cur = conn.execute(
            "SELECT * FROM hitl_gates WHERE gate_id = ? OR (alias_id != '' AND alias_id = ?) "
            "ORDER BY CASE WHEN state IN ('pending','claimed','resuming') THEN 0 ELSE 1 END, "
            "created_at DESC",
            (cid, cid),
        )
        r = cur.fetchone()
        if r is not None:
            existing = _row_to_gate(r)
            if existing.is_live:
                return existing
    if not (gate.thread_id and gate.tool):
        return None
    cur = conn.execute(
        "SELECT * FROM hitl_gates WHERE thread_id = ? AND tool = ? "
        "AND state IN ('pending','claimed','resuming') ORDER BY created_at ASC",
        (gate.thread_id, gate.tool),
    )
    for r in cur.fetchall():
        existing = _row_to_gate(r)
        if gate.gate_id and existing.gate_id == gate.gate_id:
            return existing
        if gate.alias_id and existing.gate_id == gate.alias_id:
            return existing
        if existing.alias_id and existing.alias_id in (gate.gate_id, gate.alias_id):
            return existing
        if _same_pause_args(existing.args_json, gate.args_json):
            if not existing.alias_id and gate.gate_id != existing.gate_id:
                conn.execute(
                    "UPDATE hitl_gates SET alias_id = ? WHERE gate_id = ?",
                    (gate.gate_id, existing.gate_id),
                )
                existing.alias_id = gate.gate_id
            return existing
    return None


# Columns _cas may set — the CAS helper interpolates COLUMN NAMES into SQL
# (values are always bound parameters); a whitelist keeps a future caller from
# ever turning a payload key into SQL.
_CAS_COLUMNS = frozenset(
    {"decision", "actor", "claimed_at", "settled_at", "message", "supersedes"}
)


def _cas(
    conn: sqlite3.Connection,
    gate_id: str,
    from_state: str,
    to_state: str,
    sets: dict[str, Any],
) -> bool:
    bad = set(sets) - _CAS_COLUMNS
    if bad:
        raise ValueError(f"_cas: illegal column(s) {sorted(bad)}")
    cols = "".join(f", {k} = ?" for k in sets)
    sql = f"UPDATE hitl_gates SET state = ?{cols} WHERE gate_id = ? AND state = ?"
    cur = conn.execute(sql, (to_state, *sets.values(), gate_id, from_state))
    conn.commit()
    return cur.rowcount > 0


def _conflict(conn: sqlite3.Connection, gate_id: str, expected: str) -> TransitionConflict:
    cur = conn.execute(
        "SELECT state, decision, actor FROM hitl_gates WHERE gate_id = ?", (gate_id,)
    )
    r = cur.fetchone()
    if r is None:
        return TransitionConflict(gate_id, expected, None)
    return TransitionConflict(gate_id, expected, r["state"], r["decision"], r["actor"])


def claim_gate(gate_id: str, decision: str, actor: str) -> GateRow:
    """CAS ``pending → claimed``. Exactly one winner.

    Idempotent: re-claiming an already-claimed gate with the SAME decision
    returns the row (HTTP 200 semantics). A DIFFERENT decision — or any
    other state — raises :class:`TransitionConflict` (the 409 body).
    """
    ensure_gate_schema()
    conn = _connect()
    try:
        ok = _cas(
            conn, gate_id, "pending", "claimed",
            {"decision": decision, "actor": actor, "claimed_at": time.time()},
        )
        if not ok:
            conflict = _conflict(conn, gate_id, "pending")
            if (
                conflict.actual in ("claimed", "resuming")
                and conflict.decision == decision
            ):
                row = _get(conn, gate_id)
                if row is not None:
                    return row
            raise conflict
        row = _get(conn, gate_id)
    finally:
        conn.close()
    assert row is not None
    _supersede_pending_twins(row)
    gate_events.publish("gate_claimed", row)
    return row


def _supersede_pending_twins(claimed: GateRow) -> None:
    """Drop the hash-id ghost of a pause we just claimed under the native id."""
    if not claimed.thread_id or not claimed.tool:
        return
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT * FROM hitl_gates WHERE thread_id = ? AND tool = ? "
            "AND state = 'pending' AND gate_id != ?",
            (claimed.thread_id, claimed.tool, claimed.gate_id),
        )
        twins = [_row_to_gate(r) for r in cur.fetchall()]
    finally:
        conn.close()
    for other in twins:
        same_id = bool(
            (claimed.alias_id and other.gate_id == claimed.alias_id)
            or (other.alias_id and other.alias_id in (claimed.gate_id, claimed.alias_id))
        )
        same_args = _same_pause_args(other.args_json, claimed.args_json)
        if same_id or same_args:
            supersede_gate(other.gate_id, claimed.gate_id)


def mark_resuming(gate_id: str) -> GateRow:
    """CAS ``claimed → resuming`` (the graph drive started)."""
    ensure_gate_schema()
    conn = _connect()
    try:
        ok = _cas(conn, gate_id, "claimed", "resuming", {})
        if not ok:
            conflict = _conflict(conn, gate_id, "claimed")
            if conflict.actual == "resuming":  # idempotent repeat
                row = _get(conn, gate_id)
                if row is not None:
                    return row
            raise conflict
        row = _get(conn, gate_id)
    finally:
        conn.close()
    assert row is not None
    gate_events.publish("gate_resuming", row)
    return row


def settle_gate(gate_id: str, outcome: str = "") -> GateRow:
    """Terminal ``→ settled`` from any live state (idempotent on settled)."""
    ensure_gate_schema()
    conn = _connect()
    try:
        now = time.time()
        for from_state in ("resuming", "claimed", "pending"):
            sets: dict[str, Any] = {"settled_at": now}
            if outcome:
                sets["decision"] = outcome
            if _cas(conn, gate_id, from_state, "settled", sets):
                row = _get(conn, gate_id)
                break
        else:
            conflict = _conflict(conn, gate_id, "live")
            if conflict.actual in ("settled", "timeout", "superseded", "error"):
                row = _get(conn, gate_id)
                if row is not None:
                    return row  # already terminal — idempotent, no re-emit
            raise conflict
    finally:
        conn.close()
    assert row is not None
    gate_events.publish("gate_settled", row)
    return row


def fail_gate(gate_id: str, error: str = "") -> GateRow:
    """Terminal ``→ error`` from claimed/resuming (resume raised)."""
    ensure_gate_schema()
    conn = _connect()
    try:
        now = time.time()
        for from_state in ("resuming", "claimed"):
            if _cas(
                conn, gate_id, from_state, "error",
                {"settled_at": now, "message": error[:2000] if error else ""},
            ):
                row = _get(conn, gate_id)
                break
        else:
            conflict = _conflict(conn, gate_id, "claimed|resuming")
            if conflict.actual == "error":
                row = _get(conn, gate_id)
                if row is not None:
                    return row
            raise conflict
    finally:
        conn.close()
    assert row is not None
    gate_events.publish("gate_settled", row)
    return row


def supersede_gate(gate_id: str, new_gate_id: str) -> GateRow | None:
    """Terminal ``pending → superseded`` (same pause re-emitted, new id)."""
    ensure_gate_schema()
    conn = _connect()
    try:
        ok = _cas(
            conn, gate_id, "pending", "superseded",
            {"settled_at": time.time(), "supersedes": new_gate_id},
        )
        row = _get(conn, gate_id) if ok else None
    finally:
        conn.close()
    if row is not None:
        gate_events.publish("gate_settled", row)
    return row


def _get(conn: sqlite3.Connection, gate_id: str) -> GateRow | None:
    cur = conn.execute("SELECT * FROM hitl_gates WHERE gate_id = ?", (gate_id,))
    r = cur.fetchone()
    return _row_to_gate(r) if r is not None else None


def gate_for(gate_id: str) -> GateRow | None:
    """Fetch by gate_id OR alias_id (either id lands on the same row)."""
    ensure_gate_schema()
    conn = _connect()
    try:
        row = _get(conn, gate_id)
        if row is not None:
            return row
        cur = conn.execute(
            "SELECT * FROM hitl_gates WHERE alias_id != '' AND alias_id = ? "
            "ORDER BY CASE WHEN state IN ('pending','claimed','resuming') THEN 0 ELSE 1 END, "
            "created_at DESC",
            (gate_id,),
        )
        r = cur.fetchone()
        return _row_to_gate(r) if r is not None else None
    finally:
        conn.close()


def live_gates(thread_id: str) -> list[GateRow]:
    """All non-terminal gates on a thread, oldest first.

    Any row here ⇒ the turn stays OPEN (no "done" while a question is
    outstanding — the silence rule).
    """
    ensure_gate_schema()
    conn = _connect()
    try:
        cur = conn.execute(
            f"SELECT * FROM hitl_gates WHERE thread_id = ? AND state IN "
            f"({','.join('?' * len(LIVE_STATES))}) ORDER BY created_at ASC",
            (thread_id, *LIVE_STATES),
        )
        return [_row_to_gate(r) for r in cur.fetchall()]
    finally:
        conn.close()


def pending_gates(tenant_id: str | None = None, *, limit: int = 200) -> list[GateRow]:
    """All ``pending`` gates (the dashboard/pending-approvals query)."""
    ensure_gate_schema()
    conn = _connect()
    try:
        if tenant_id is None:
            cur = conn.execute(
                "SELECT * FROM hitl_gates WHERE state = 'pending' "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,),
            )
        else:
            cur = conn.execute(
                "SELECT * FROM hitl_gates WHERE state = 'pending' AND tenant_id = ? "
                "ORDER BY created_at ASC LIMIT ?",
                (tenant_id, limit),
            )
        return [_row_to_gate(r) for r in cur.fetchall()]
    finally:
        conn.close()


def expire_due_gates(now: float | None = None) -> list[GateRow]:
    """TTL sweep: live gates past ``expires_at`` → ``timeout`` (auto-deny).

    Each expiry is emitted so the card visibly times out everywhere at once.
    """
    ensure_gate_schema()
    now = now if now is not None else time.time()
    expired: list[GateRow] = []
    conn = _connect()
    try:
        cur = conn.execute(
            f"SELECT gate_id, state FROM hitl_gates WHERE state IN "
            f"({','.join('?' * len(LIVE_STATES))}) "
            "AND expires_at IS NOT NULL AND expires_at <= ?",
            (*LIVE_STATES, now),
        )
        targets = [(r["gate_id"], r["state"]) for r in cur.fetchall()]
        for gid, st in targets:
            if _cas(conn, gid, st, "timeout", {"settled_at": now}):
                row = _get(conn, gid)
                if row is not None:
                    expired.append(row)
    finally:
        conn.close()
    for row in expired:
        gate_events.publish("gate_settled", row)
    if expired:
        logger.info("[HitlGates] expired %d gate(s) to timeout", len(expired))
    return expired


# ── async wrappers (§23 — the server loop must never block) ────────────────


async def register_gate_async(gate: GateRow, *, ttl_seconds: float | None = None) -> GateRow:
    return await asyncio.to_thread(register_gate, gate, ttl_seconds=ttl_seconds)


async def claim_gate_async(gate_id: str, decision: str, actor: str) -> GateRow:
    return await asyncio.to_thread(claim_gate, gate_id, decision, actor)


async def mark_resuming_async(gate_id: str) -> GateRow:
    return await asyncio.to_thread(mark_resuming, gate_id)


async def settle_gate_async(gate_id: str, outcome: str = "") -> GateRow:
    return await asyncio.to_thread(settle_gate, gate_id, outcome)


async def gate_for_async(gate_id: str) -> GateRow | None:
    return await asyncio.to_thread(gate_for, gate_id)


async def live_gates_async(thread_id: str) -> list[GateRow]:
    return await asyncio.to_thread(live_gates, thread_id)


async def pending_gates_async(tenant_id: str | None = None) -> list[GateRow]:
    return await asyncio.to_thread(pending_gates, tenant_id)


async def expire_due_gates_async(now: float | None = None) -> list[GateRow]:
    return await asyncio.to_thread(expire_due_gates, now)


# ── boot sweep (reconciler rule b — crash recovery) ─────────────────────────


def boot_sweep(grace_seconds: float = 300.0) -> dict[str, int]:
    """Converge rows a dead process left behind. Run once at startup.

    * ``claimed``/``resuming`` rows older than *grace_seconds*: the drive
      that owned them died with the process — settle as ``orphaned``. The
      user was already told (or will retry); a stuck in-flight stamp is the
      lie we refuse to keep.
    * ``pending`` rows are LEFT ALONE — the checkpoint pause survives a
      restart and the card must keep showing (fail toward a live card).
    * TTL expiry runs as part of the sweep.
    """
    ensure_gate_schema()
    out = {"orphaned": 0, "expired": 0}
    cutoff = time.time() - max(0.0, grace_seconds)
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT gate_id, state FROM hitl_gates WHERE state IN "
            "('claimed','resuming') AND COALESCE(claimed_at, created_at) <= ?",
            (cutoff,),
        )
        targets = [(r["gate_id"], r["state"]) for r in cur.fetchall()]
        now = time.time()
        for gid, st in targets:
            if _cas(conn, gid, st, "settled",
                    {"settled_at": now, "decision": "orphaned"}):
                out["orphaned"] += 1
    finally:
        conn.close()
    try:
        out["expired"] = len(expire_due_gates())
    except Exception:
        logger.debug("[HitlGates] boot-sweep TTL pass failed", exc_info=True)
    if out["orphaned"] or out["expired"]:
        logger.info("[HitlGates] boot sweep: %s", out)
        try:
            from kazma_core.metrics import record_hitl_gate_reconciled

            for _ in range(out["orphaned"]):
                record_hitl_gate_reconciled("orphaned")
        except Exception:
            pass
    return out


async def boot_sweep_async(grace_seconds: float = 300.0) -> dict[str, int]:
    return await asyncio.to_thread(boot_sweep, grace_seconds)
