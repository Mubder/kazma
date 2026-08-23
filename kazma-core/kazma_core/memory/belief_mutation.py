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
# Current entitlements (grok_next_reset, *_weekly_reset, …) are also
# functional via :func:`current_facts.is_functional_current_predicate`.
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
        # Weekly entitlement / quota current values (one active per service)
        "next_reset",
        "next_reset_time",
        "weekly_reset",
        "quota_reset",
        "grok_next_reset",
        "supergrok_next_reset",
        "zcode_next_reset",
        "claude_next_reset",
        "cursor_next_reset",
        "copilot_next_reset",
        "openai_next_reset",
        "chatgpt_next_reset",
    }
)

# State-transition predicates (governed by a transition log).
_STATE_PREDICATES = frozenset({"issue_status", "pipeline_state", "task_state"})

# Time-bound / scheduled predicates that must NEVER be superseded.
# These are forced to 'set' type (append-only) regardless of LLM classification,
# so multiple reminders/events can coexist without replacing each other.
# Uses substring matching to catch whatever predicate name the LLM invents.
_NEVER_SUPERSEDE_PATTERNS = (
    "reminder",
    "scheduled",
    "appointment",
    "event_",
    "cron_",
)


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
    p = (predicate or "").strip().lower().replace(" ", "_")
    # Time-bound predicates are NEVER functional — they accumulate, not replace.
    # This check runs BEFORE the explicit override so even LLM-classified
    # "functional" reminders are forced to 'set' (append-only).
    # Exception: *_next_reset / entitlement current-facts are single-valued
    # even if the word "scheduled" appears elsewhere in free text (not in pred).
    if any(pat in p for pat in _NEVER_SUPERSEDE_PATTERNS):
        return "set"
    # Rotating current facts (Grok/ZCode next weekly reset, …) always
    # supersede — even if the LLM omitted predicate_type.
    try:
        from kazma_core.memory.current_facts import is_functional_current_predicate

        if is_functional_current_predicate(p):
            return "functional"
    except Exception:
        pass
    if explicit in ("functional", "set", "state"):
        return explicit
    if p in _FUNCTIONAL_PREDICATES:
        return "functional"
    if p in _STATE_PREDICATES:
        return "state"
    # favorite_* stays single-valued
    if p.startswith("favorite_"):
        return "functional"
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

# Serializes belief mutations across threads. The functional-supersede
# logic does a read-then-write (find active belief → close it → insert
# new) which is inherently racy if two threads mutate the same
# (subject, predicate) concurrently — both could see no active belief
# and both insert, losing the supersede. This lock makes mutations atomic.
_mutation_lock = threading.Lock()


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
    subject_id: str | None = None,
) -> dict[str, Any]:
    """Apply a belief mutation per the predicate-type rules.

    Returns a dict describing the outcome::

        {"action": "supersede"|"append"|"transition"|"noop",
         "belief_id": str,
         "superseded_id": str | None}

    Never raises — logs on failure and returns ``{"action": "noop", ...}``.
    """
    # Hygiene: reject version/stack-name subjects (e.g. kazma_v2_4_0)
    try:
        from kazma_core.memory.hygiene import is_blocked_belief_triple

        if is_blocked_belief_triple(subject, predicate, obj):
            logger.info(
                "[belief_mutate] rejected blocked subject %r",
                (subject or "")[:60],
            )
            return {
                "action": "noop",
                "belief_id": "",
                "superseded_id": None,
                "rejected": "blocked_subject",
            }
    except Exception:
        pass

    ptype = _classify_predicate(predicate, predicate_type)
    # Operator-driven links may carry an explicit subject_id (the exact
    # graph node id the operator clicked, e.g. a virtual-fact id like
    # "ShipX — Deployment Modes"). Slugifying that id would mint a
    # different entity and detach the edge from the node the operator
    # clicked. When subject_id is provided, use it verbatim; otherwise
    # slugify the free-text subject (the historical behavior).
    sub = subject_id if subject_id else _slug(subject)
    # Follow merged_into so beliefs land on the canonical entity, not a
    # retired id. This is the write-side fix for the mubder→user re-orphan
    # bug: extraction kept minting under 'mubder' after the merge because
    # nothing read merged_into. Also applied to obj below (objects can be
    # entity ids). Best-effort; on any failure the id is unchanged.
    try:
        from kazma_core.memory.entity_resolution import canonical_entity_id
        sub = canonical_entity_id(primary_conn, sub)
    except Exception:
        pass
    # Also follow merged_into for the object when it's an entity id. Objects
    # can be entity ids (e.g. operator links), and a retired object id would
    # otherwise leave a dangling edge to a merged-away node.
    try:
        from kazma_core.memory.entity_resolution import canonical_entity_id
        obj = canonical_entity_id(primary_conn, obj)
    except Exception:
        pass
    pred = (predicate or "").strip().lower().replace(" ", "_") or "related"
    # Re-check after slugify (spaces → underscores)
    try:
        from kazma_core.memory.hygiene import is_blocked_belief_subject

        if is_blocked_belief_subject(sub):
            return {
                "action": "noop",
                "belief_id": "",
                "superseded_id": None,
                "rejected": "blocked_subject",
            }
    except Exception:
        pass
    trust = _trust_weight(extraction_method, cfg)
    mem_class = derive_memory_class(ptype, importance, cfg=cfg)
    now = time.time()

    try:
        with _mutation_lock:
            if ptype == "functional":
                result = _mutate_functional(
                    primary_conn, ops_conn, sub, pred, obj,
                    confidence=confidence, importance=importance, trust=trust,
                    extraction_method=extraction_method, tenant_id=tenant_id,
                    source_session=source_session, source_turn=source_turn,
                    mem_class=mem_class, now=now, cfg=cfg,
                )
            elif ptype == "state":
                result = _mutate_state(
                    primary_conn, ops_conn, sub, pred, obj,
                    confidence=confidence, importance=importance, trust=trust,
                    extraction_method=extraction_method, tenant_id=tenant_id,
                    source_session=source_session, source_turn=source_turn,
                    mem_class=mem_class, now=now, cfg=cfg,
                )
            else:
                result = _mutate_set(
                    primary_conn, ops_conn, sub, pred, obj,
                    confidence=confidence, importance=importance, trust=trust,
                    extraction_method=extraction_method, tenant_id=tenant_id,
                    source_session=source_session, source_turn=source_turn,
                    mem_class=mem_class, now=now, cfg=cfg,
                )
        # Phase 0 instrumentation (Commitment Layer): surface every functional
        # supersede so ``belief.supersede_without_user_assert`` (plan §8.1) is
        # observable. The signal of interest is a supersede whose incoming
        # source is NOT a direct user assertion (extraction_method !=
        # "user_explicit") — that is the CoPilot-class memory-corruption path.
        # A numeric counter / metric framework wraps this log line later.
        if result.get("action") == "supersede":
            try:
                from kazma_core.memory.current_facts import (
                    is_functional_current_predicate,
                )

                if is_functional_current_predicate(pred):
                    logger.info(
                        "[belief_mutate] functional_supersede predicate=%s "
                        "source=%s trust=%.2f subject=%s",
                        pred, extraction_method, trust, sub,
                    )
            except Exception:
                pass
        # Best-effort dual-write to shared state / graph backends (P2-2/P2-3)
        if result.get("action") not in ("noop", None) and result.get("belief_id"):
            try:
                from kazma_core.memory.graph_backend import upsert_belief_edge
                from kazma_core.memory.state_backend import remirror_belief_by_id

                bid = str(result["belief_id"])
                # Mirror the PERSISTED row (M-04): guarantees the mirror sees
                # exactly what SQLite committed, death flags included — never
                # a hand-built "definitely alive" dict.
                remirror_belief_by_id(conn, bid)
                upsert_belief_edge(
                    subject=sub,
                    predicate=pred,
                    obj=obj,
                    belief_id=bid,
                    tenant_id=tenant_id,
                    confidence=float(confidence or 0.5),
                )
            except Exception:
                logger.debug("[belief_mutate] dual-write backends failed", exc_info=True)
            try:
                from kazma_core.memory.unified_index import upsert_unified

                upsert_unified(
                    item_id=str(result["belief_id"]),
                    kind="belief",
                    text=f"{sub} {pred} {obj}".strip(),
                    tenant_id=tenant_id,
                )
            except Exception:
                logger.debug("[belief_mutate] unified index skipped", exc_info=True)

        # Phase 3: keep the materialized entity belief_count / graph_degree
        # columns in sync. Recompute the affected subject + object so the
        # operator /memory page reads precomputed values instead of running
        # per-row correlated subqueries. Only on a real mutation (not noop).
        # The object is included even when it's a literal — recompute is a
        # no-op for ids with no entity row. Caller owns the commit.
        try:
            from kazma_core.memory.entity_counts import recompute_entity_counts

            recompute_entity_counts(primary_conn, [sub, obj], tenant_id=tenant_id)
            primary_conn.commit()
        except Exception:
            logger.debug("[belief_mutate] entity count recompute failed", exc_info=True)

        return result
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
    try:
        from kazma_core.memory.embedder import get_embedding_model_name

        emb_version = get_embedding_model_name()
    except Exception:
        emb_version = ""
    from kazma_core.memory.hygiene import beliefs_write

    beliefs_write(
        conn,
        """INSERT OR IGNORE INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at, supersedes_id, source_session,
            source_turn, extraction_method, metadata_json,
            embedding_model_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            bid, tenant_id, sub, pred, ptype, obj,
            float(confidence), int(importance), float(trust),
            now, now, supersedes_id, source_session, source_turn,
            extraction_method, json.dumps(meta, ensure_ascii=False),
            emb_version,
        ),
    )
    # Compute + store the belief embedding so dense (semantic) recall can
    # match beliefs whose literal tokens don't overlap the query (e.g. the
    # query "where do I live" should semantically match "user lives_in Paris"
    # even before the episode bridge fires). Embeds the canonical SPO text.
    # Best-effort: a missing/broken embedder leaves embedding NULL (belief
    # recall still works via the FTS token + bridge paths above).
    try:
        from kazma_core.memory.embedder import encode_text_to_blob

        emb = encode_text_to_blob(f"{sub} {pred} {obj}")
        if emb is not None:
            conn.execute(
                "UPDATE beliefs SET embedding=? WHERE id=? AND embedding IS NULL",
                (emb, bid),
            )
    except Exception:
        logger.debug("[belief_mutation] belief embedding failed for %s", bid, exc_info=True)
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
    """Single-valued: close the prior active belief, insert a new one.

    ``kw`` may carry ``audit_event_type`` (default ``"supersede"``) so the
    state-predicate path can record a ``"transition"`` audit row via the same
    code path instead of mislabeling every state change as a supersede.
    """
    tenant_id = kw["tenant_id"]
    now = kw["now"]
    audit_event_type = kw.get("audit_event_type", "supersede")
    # BEGIN IMMEDIATE acquires a write lock up front so the read below
    # sees ALL prior committed writes (no stale WAL snapshot). This is
    # essential when mutations run across separate connections/threads —
    # a deferred read transaction could miss a just-committed supersede.
    _began = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        _began = True
    except Exception:
        pass  # already in a transaction
    # Find the currently-active belief for this (subject, predicate)
    existing = conn.execute(
        """SELECT id, object, extraction_method FROM beliefs
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
        existing_method = (
            existing["extraction_method"]
            if isinstance(existing, sqlite3.Row) else existing[2]
        )
        # If the new object equals the existing one, this is a no-op
        if old_obj == obj:
            # Release the BEGIN IMMEDIATE lock we acquired (no writes to
            # commit). Only roll back if WE started the txn — if the caller
            # already had one open, leave it to the caller (audit finding:
            # early returns leaked the write lock until the connection closed
            # if the caller's later commit raised).
            if _began:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"action": "noop", "belief_id": superseded_id, "superseded_id": None}
        # Commitment Layer Phase 1 — source-trust gate (plan §3.6 rule 2):
        # a user_explicit (gold-standard) functional belief may NOT be
        # superseded by a lower-trust source (llm_inferred / system_tool).
        # This is the memory-corruption half of the CoPilot incident — the
        # post-turn extractor (llm_inferred) invented a date and overwrote a
        # user-asserted copilot_next_reset. Fail-closed: drop the inferred
        # overwrite; the user fact stands. Kill-switch:
        # cfg.v2.functional_supersede_requires_user_assert (default True).
        gate_cfg = ((kw.get("cfg") or {}).get("v2") or {})
        if (bool(gate_cfg.get("functional_supersede_requires_user_assert", True))
                and existing_method == "user_explicit"
                and kw["extraction_method"] != "user_explicit"):
            logger.info(
                "[belief_mutate] blocked_supersede predicate=%s subject=%s "
                "existing_source=user_explicit incoming_source=%s — user fact stands",
                pred, sub, kw["extraction_method"],
            )
            _write_audit(
                ops_conn, tenant_id=tenant_id, event_type="blocked_supersede",
                target_id=superseded_id, actor="source_trust_gate",
                reason=(f"lower-trust ({kw['extraction_method']}) cannot "
                        f"supersede user_explicit {pred}"),
                state_before={"id": superseded_id, "object": old_obj},
                state_after={"id": superseded_id, "object": old_obj, "blocked": True},
            )
            if _began:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"action": "noop", "belief_id": superseded_id,
                    "superseded_id": None, "blocked": "lower_trust_source"}
        state_before = {"id": superseded_id, "object": old_obj}
        from kazma_core.memory.hygiene import beliefs_write

        beliefs_write(
            conn,
            "UPDATE beliefs SET valid_until=?, invalidated_at=? WHERE id=?",
            (now, now, superseded_id),
        )
        # Dual-write cleanup: drop superseded edge from Neo4j
        try:
            from kazma_core.memory.graph_backend import delete_belief_edge

            delete_belief_edge(
                belief_id=str(superseded_id),
                subject=sub,
                predicate=pred,
                obj=str(old_obj or ""),
                tenant_id=tenant_id,
            )
        except Exception:
            logger.debug("[belief_mutate] neo4j delete on supersede skipped", exc_info=True)
        # Mirror tombstone: push the superseded row's death flags to shared
        # state (M-04 — the mirror previously stayed live forever).
        try:
            from kazma_core.memory.state_backend import remirror_belief_by_id

            remirror_belief_by_id(conn, str(superseded_id))
        except Exception:
            logger.debug("[belief_mutate] mirror tombstone skipped", exc_info=True)
    bid = _belief_id(tenant_id, sub, pred, now)
    state_after = _insert_belief(
        conn, bid, tenant_id, sub, pred, "functional", obj,
        supersedes_id=superseded_id, now=now, **_insert_kw(kw),
    )
    conn.commit()
    _write_audit(
        ops_conn, tenant_id=tenant_id, event_type=audit_event_type,
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
    # The kwarg flows through to _mutate_functional's _write_audit call so the
    # persisted audit row is labelled correctly (not as a generic supersede).
    result = _mutate_functional(conn, ops_conn, sub, pred, obj, audit_event_type="transition", **kw)
    if result["action"] == "supersede":
        result["action"] = "transition"
    return result


def _normalize_noted_key(obj: str) -> str:
    """Stable key for noted near-dedupe (collapse whitespace, lower, trim)."""
    import re

    return re.sub(r"\s+", " ", (obj or "").strip().lower())[:160]


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

    # noted diary blobs: near-dedupe so two "ShipX Overview" saves minutes
    # apart (whitespace/punctuation drift) do not stack forever.
    if pred == "noted":
        key = _normalize_noted_key(obj)
        if key:
            candidates = conn.execute(
                """SELECT id, object FROM beliefs
                   WHERE subject=? AND predicate='noted' AND tenant_id=?
                     AND valid_until IS NULL AND invalidated_at IS NULL
                   ORDER BY valid_from DESC LIMIT 80""",
                (sub, tenant_id),
            ).fetchall()
            for row in candidates:
                existing_obj = (
                    row["object"] if isinstance(row, sqlite3.Row) else row[1]
                )
                if _normalize_noted_key(str(existing_obj or "")) == key:
                    eid = row["id"] if isinstance(row, sqlite3.Row) else row[0]
                    return {
                        "action": "noop",
                        "belief_id": eid,
                        "superseded_id": None,
                        "deduped": "noted_near",
                    }
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
