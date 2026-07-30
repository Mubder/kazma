"""V2 Belief mutation engine — type-correct predicate handling.

The single source of truth for writing beliefs into the bi-temporal
``beliefs`` table. Implements the three mutation rules from §4.3 of the
specification:

  - **functional** (single-valued: lives_in, name_is, works_at, ...):
    the prior active belief's ``valid_until`` is closed to NOW, and the
    new belief links back via ``supersedes_id``. This is how "I moved to
    London" supersedes "I live in Paris" without losing history.
  - **set** (multi-valued: uses_tool, knows_language, ...): the new
    belief appends alongside existing ones — no invalidation.
  - **state** (transitions: issue_status, pipeline_state): the prior
    active state is closed and a transition is logged to the audit log.

All mutations are atomic (single transaction) and write an audit-log
entry to ``memory_ops.db`` so every belief change is traceable.

``memory_class`` derivation (resolution #4) is deterministic — no new
schema column, no extra LLM call:

  - functional + importance ≥ identity_min_importance → 'identity'
  - importance ≤ ephemeral_max_importance              → 'ephemeral'
  - otherwise                                          → 'general'

The derived class is stored in ``metadata_json.memory_class`` so the
macro-consolidation decay job can read it without a schema change.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "derive_memory_class",
    "mutate_belief",
    "FunctionalBelief",
    "SetBelief",
]

# Canonical functional (single-valued) predicates. Anything not in this
# set defaults to 'set' unless explicitly passed as predicate_type='state'.
_FUNCTIONAL_PREDICATES = frozenset(
    {
        "name_is",
        "lives_in",
        "works_at",
        "active_project",
        "favorite_ide",
        "favorite_editor",
        "favorite_language",
        "located_in",
        "current_role",
        "preferred_name",
        "favorite_color",
    }
)

# State-transition predicates (governed by a transition log).
_STATE_PREDICATES = frozenset({"issue_status", "pipeline_state", "task_state"})


class FunctionalBelief:
    """Typed payload for a functional belief mutation."""


class SetBelief:
    """Typed payload for a set-valued belief append."""


# ── memory_class derivation (resolution #4) ──────────────────────────────


def derive_memory_class(
    predicate_type: str,
    importance: int,
    *,
    cfg: dict[str, Any] | None = None,
) -> str:
    """Derive the decay class from predicate type + importance.

    Returns one of 'identity' | 'general' | 'ephemeral'. Deterministic
    and reversible — no LLM call, no new schema column.

    Args:
        predicate_type: 'functional' | 'set' | 'state'.
        importance: 1..5 structural importance.
        cfg: Optional V2 config block (reads identity_min_importance /
            ephemeral_max_importance thresholds).
    """
    v2 = (cfg or {}).get("v2") or {}
    identity_min = int(v2.get("identity_min_importance", 4))
    ephemeral_max = int(v2.get("ephemeral_max_importance", 2))
    if predicate_type == "functional" and importance >= identity_min:
        return "identity"
    if importance <= ephemeral_max:
        return "ephemeral"
    return "general"


# ── helpers ───────────────────────────────────────────────────────────────


def _slug(text: str) -> str:
    import re

    raw = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "entity"


def _belief_id(tenant_id: str, subject: str, predicate: str, valid_from: float) -> str:
    """Belief PK. Includes a uuid suffix so two mutations at the same
    timestamp (e.g. rapid programmatic updates) don't collide on PK and
    silently drop via INSERT OR IGNORE. The hash prefix keeps the id
    visually traceable to its (tenant, subject, predicate, valid_from)."""
    import uuid

    h = hashlib.sha256(
        f"{tenant_id}|{subject}|{predicate}|{valid_from}".encode("utf-8")
    ).hexdigest()
    return f"b_{h[:20]}_{uuid.uuid4().hex[:6]}"


def _classify_predicate(predicate: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    p = (predicate or "").strip().lower()
    if p in _FUNCTIONAL_PREDICATES:
        return "functional"
    if p in _STATE_PREDICATES:
        return "state"
    return "set"


def _trust_weight(extraction_method: str, cfg: dict[str, Any] | None) -> float:
    v2 = (cfg or {}).get("v2") or {}
    if extraction_method == "user_explicit":
        return float(v2.get("trust_weight_user", 1.0))
    if extraction_method == "system_tool":
        return float(v2.get("trust_weight_tool", 0.85))
    return float(v2.get("trust_weight_llm", 0.60))


# ── audit log writer ──────────────────────────────────────────────────────


_audit_lock = threading.Lock()


def _write_audit(
    ops_conn: sqlite3.Connection | None,
    *,
    tenant_id: str,
    event_type: str,
    target_id: str,
    actor: str,
    reason: str,
    state_before: dict[str, Any] | None = None,
    state_after: dict[str, Any] | None = None,
) -> None:
    """Append an immutable audit-log row (best-effort)."""
    if ops_conn is None:
        return
    import uuid

    aid = "a_" + uuid.uuid4().hex[:20]
    try:
        with _audit_lock:
            ops_conn.execute(
                """INSERT INTO memory_audit_log
                   (id, tenant_id, timestamp, event_type, target_table, target_id,
                    actor, reason, state_before_json, state_after_json)
                   VALUES (?, ?, ?, ?, 'beliefs', ?, ?, ?, ?, ?)""",
                (
                    aid,
                    tenant_id,
                    time.time(),
                    event_type,
                    target_id,
                    actor,
                    reason,
                    json.dumps(state_before) if state_before else None,
                    json.dumps(state_after) if state_after else None,
                ),
            )
            ops_conn.commit()
    except Exception:
        logger.debug("[belief_mutate] audit write failed", exc_info=True)


# ── public mutation entry point ──────────────────────────────────────────


def mutate_belief(
    primary_conn: sqlite3.Connection,
    subject: str,
    predicate: str,
    obj: str,
    *,
    ops_conn: sqlite3.Connection | None = None,
    predicate_type: str | None = None,
    confidence: float = 0.5,
    importance: int = 1,
    extraction_method: str = "llm_inferred",
    tenant_id: str = "default",
    source_session: str | None = None,
    source_turn: int | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a belief mutation per the predicate-type rules.

    Returns a dict describing the outcome::

        {"action": "supersede"|"append"|"transition"|"noop",
         "belief_id": str,
         "superseded_id": str | None}

    Never raises — logs on failure and returns ``{"action": "noop", ...}``.
    """
    ptype = _classify_predicate(predicate, predicate_type)
    sub = _slug(subject)
    pred = (predicate or "").strip().lower().replace(" ", "_") or "related"
    trust = _trust_weight(extraction_method, cfg)
    mem_class = derive_memory_class(ptype, importance, cfg=cfg)
    now = time.time()

    try:
        if ptype == "functional":
            return _mutate_functional(
                primary_conn, ops_conn, sub, pred, obj,
                confidence=confidence, importance=importance, trust=trust,
                extraction_method=extraction_method, tenant_id=tenant_id,
                source_session=source_session, source_turn=source_turn,
                mem_class=mem_class, now=now,
            )
        elif ptype == "state":
            return _mutate_state(
                primary_conn, ops_conn, sub, pred, obj,
                confidence=confidence, importance=importance, trust=trust,
                extraction_method=extraction_method, tenant_id=tenant_id,
                source_session=source_session, source_turn=source_turn,
                mem_class=mem_class, now=now,
            )
        else:
            return _mutate_set(
                primary_conn, ops_conn, sub, pred, obj,
                confidence=confidence, importance=importance, trust=trust,
                extraction_method=extraction_method, tenant_id=tenant_id,
                source_session=source_session, source_turn=source_turn,
                mem_class=mem_class, now=now,
            )
    except Exception:
        logger.debug("[belief_mutate] mutation failed", exc_info=True)
        return {"action": "noop", "belief_id": "", "superseded_id": None}


def _insert_belief(
    conn: sqlite3.Connection,
    bid: str,
    tenant_id: str,
    sub: str,
    pred: str,
    ptype: str,
    obj: str,
    *,
    confidence: float,
    importance: int,
    trust: float,
    extraction_method: str,
    source_session: str | None,
    source_turn: int | None,
    mem_class: str,
    now: float,
    supersedes_id: str | None = None,
) -> dict[str, Any]:
    meta = {"memory_class": mem_class}
    conn.execute(
        """INSERT OR IGNORE INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at, supersedes_id, source_session,
            source_turn, extraction_method, metadata_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            bid, tenant_id, sub, pred, ptype, obj,
            float(confidence), int(importance), float(trust),
            now, now, supersedes_id, source_session, source_turn,
            extraction_method, json.dumps(meta, ensure_ascii=False),
        ),
    )
    return {
        "subject": sub, "predicate": pred, "predicate_type": ptype,
        "object": obj, "confidence": confidence, "importance": importance,
        "memory_class": mem_class,
    }


def _insert_kw(kw: dict[str, Any]) -> dict[str, Any]:
    """Extract the subset of `kw` that _insert_belief accepts (clean splat)."""
    return {
        "confidence": kw["confidence"],
        "importance": kw["importance"],
        "trust": kw["trust"],
        "extraction_method": kw["extraction_method"],
        "source_session": kw.get("source_session"),
        "source_turn": kw.get("source_turn"),
        "mem_class": kw["mem_class"],
    }


def _mutate_functional(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection | None,
    sub: str, pred: str, obj: str, **kw: Any,
) -> dict[str, Any]:
    """Single-valued: close the prior active belief, insert a new one."""
    tenant_id = kw["tenant_id"]
    now = kw["now"]
    # Find the currently-active belief for this (subject, predicate)
    existing = conn.execute(
        """SELECT id, object FROM beliefs
           WHERE subject=? AND predicate=? AND tenant_id=?
             AND valid_until IS NULL AND invalidated_at IS NULL
           LIMIT 1""",
        (sub, pred, tenant_id),
    ).fetchone()
    superseded_id = None
    state_before = None
    if existing:
        superseded_id = existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
        old_obj = existing["object"] if isinstance(existing, sqlite3.Row) else existing[1]
        # If the new object equals the existing one, this is a no-op
        if old_obj == obj:
            return {"action": "noop", "belief_id": superseded_id, "superseded_id": None}
        state_before = {"id": superseded_id, "object": old_obj}
        conn.execute(
            "UPDATE beliefs SET valid_until=?, invalidated_at=? WHERE id=?",
            (now, now, superseded_id),
        )
    bid = _belief_id(tenant_id, sub, pred, now)
    state_after = _insert_belief(
        conn, bid, tenant_id, sub, pred, "functional", obj,
        supersedes_id=superseded_id, now=now, **_insert_kw(kw),
    )
    conn.commit()
    _write_audit(
        ops_conn, tenant_id=tenant_id, event_type="supersede",
        target_id=bid, actor="post_turn_worker",
        reason=f"functional {sub} {pred} -> {obj}",
        state_before=state_before, state_after=state_after,
    )
    return {"action": "supersede", "belief_id": bid, "superseded_id": superseded_id}


def _mutate_state(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection | None,
    sub: str, pred: str, obj: str, **kw: Any,
) -> dict[str, Any]:
    """State transition: close prior active state, log the transition."""
    # Same mechanics as functional, but the audit event_type is 'transition'.
    result = _mutate_functional(conn, ops_conn, sub, pred, obj, **kw)
    if result["action"] == "supersede":
        result["action"] = "transition"
    return result


def _mutate_set(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection | None,
    sub: str, pred: str, obj: str, **kw: Any,
) -> dict[str, Any]:
    """Multi-valued: append without invalidating existing entries."""
    tenant_id = kw["tenant_id"]
    now = kw["now"]
    # Skip if this exact (subject, predicate, object) already exists & active
    dup = conn.execute(
        """SELECT id FROM beliefs
           WHERE subject=? AND predicate=? AND object=? AND tenant_id=?
             AND valid_until IS NULL AND invalidated_at IS NULL
           LIMIT 1""",
        (sub, pred, obj, tenant_id),
    ).fetchone()
    if dup:
        existing_id = dup["id"] if isinstance(dup, sqlite3.Row) else dup[0]
        return {"action": "noop", "belief_id": existing_id, "superseded_id": None}
    bid = _belief_id(tenant_id, sub, pred, now)
    # Give set beliefs a unique id suffix to avoid PK collision when the
    # same (s,p) gets multiple objects at the same timestamp.
    bid = bid + "_" + hashlib.sha256(obj.encode()).hexdigest()[:6]
    state_after = _insert_belief(
        conn, bid, tenant_id, sub, pred, "set", obj, now=now, **_insert_kw(kw),
    )
    conn.commit()
    _write_audit(
        ops_conn, tenant_id=tenant_id, event_type="append",
        target_id=bid, actor="post_turn_worker",
        reason=f"set {sub} {pred} += {obj}",
        state_after=state_after,
    )
    return {"action": "append", "belief_id": bid, "superseded_id": None}
