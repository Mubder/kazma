"""V2 Belief mutation engine — type-correct predicate handling.

The single source of truth for writing beliefs into the bi-temporal
``beliefs`` table. Implements the three mutation rules from §4.3 of the
specification:

  - **functional** (single-valued: lives_in, name_is, works_at, ...):
    the prior active belief's ``valid_until`` is closed to when the new
    statement was made, and the new belief links back via
    ``supersedes_id``. This is how "I moved to London" supersedes "I live in
    Paris" without losing history. A statement OLDER than the current fact
    (a reconciled old turn, a slow extraction) never replaces it: it is
    recorded as history in its place on the timeline, so the order facts
    are written in never changes what is true now.
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

The derived class is stored in ``metadata_json.memory_class`` as a label.
Nothing decides on it today: it was meant for a macro-consolidation decay
score that no rule ever read, removed on 2026-09-23 (``macro_sleep.py``).
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import hashlib
import json
import logging
import sqlite3
import threading
import time
from typing import Any

# The one id rule (Memory audit M02): a copy here kept a-z0-9 only.
from kazma_core.memory.entity_resolution import slug as _slug
from kazma_core.spaces.scope import space_named_or_bound

logger = logging.getLogger(__name__)

__all__ = [
    "derive_memory_class",
    "kazma_classifies",
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

# Single-valued names a person often holds several of at once: two jobs,
# projects in parallel, two roles. Kazma's class wins over a writer's list
# label on them too -- except a list from the user's own words (the review of
# the memory framework audit's F7: F7 collapsed the user's own list of jobs
# to the last one written). Every other name above was confirmed to hold one
# value at a time: one name (name_is, preferred_name), one home and one
# location (lives_in, located_in), one favourite of a kind, one next reset
# per service.
_LISTABLE = frozenset({"works_at", "active_project", "current_role"})


@functools.lru_cache(maxsize=1)
def _kazma_names() -> dict[frozenset[str], str]:
    from kazma_core.memory.predicates import _predicate_key

    return {_predicate_key(name): name for name in sorted(_FUNCTIONAL_PREDICATES | _STATE_PREDICATES)}


def _kazma_name(predicate: str) -> str | None:
    """Kazma's own single-valued or state name that *predicate* is the same
    words as ("live_in", "in_lives", "lives_in_the" -> "lives_in"; "name",
    "is_name", "the_name" -> "name_is"), or None. Compared as ``predicates``
    compares names: order, filler words and a plural "s" aside. Read from the
    exact name, a writer's "in_lives" labelled a list stood beside the user's
    "lives_in", past the trust gate (the review of F7). Never a name that
    puts Kazma's thing in the plural ("names": ``_names_a_list``)."""
    from kazma_core.memory.predicates import _predicate_key

    key = _predicate_key(predicate)
    named = _kazma_names().get(key) if key else None
    return None if named is not None and _plural_of(predicate, named) else named


def one_value_name(predicate: str) -> str | None:
    """Kazma's own name for the single value *predicate* says
    (:func:`_kazma_name`), or None: a favourite, a list, or no name of Kazma's."""
    return _kazma_name(predicate)


def _name_words(predicate: str) -> list[str]:
    return [w for w in (predicate or "").strip().lower().replace(" ", "_").split("_") if w]


def _is_plural(word: str) -> bool:
    """A plural noun's "s", as ``predicates._predicate_key`` drops it."""
    return len(word) > 3 and word.endswith("s") and not word.endswith("ss")


def _plural_of(predicate: str, named: str) -> bool:
    """Whether *predicate* says in the plural a word Kazma's *named* says in
    the singular ("names" for "name_is"). "lives_in" and "works_at" keep
    their own "s": a verb's, not a plural's."""
    own = set(named.split("_"))
    return any(_is_plural(w) and w not in own and w[:-1] in own for w in _name_words(predicate))


def _names_a_list(predicate: str) -> bool:
    """Whether *predicate* names in the plural what Kazma keeps one of:
    "names", "favorite_foods", "colors_favorite". A plural name is a list,
    whatever the writer labels it (the security review of F1's second half,
    2026-11-18: "names" replaced the user's name, and "favorite_foods" kept
    one food)."""
    words = _name_words(predicate)
    if _is_favorite(predicate) and any(_is_plural(w) for w in words if w != "favorite"):
        return True
    from kazma_core.memory.predicates import _predicate_key

    key = _predicate_key(predicate)
    named = _kazma_names().get(key) if key else None
    if named is not None and _plural_of(predicate, named):
        return True
    from kazma_core.memory.fact_names import plural_of_a_kind

    return plural_of_a_kind(predicate)  # "homes", "employers": a model's names in the plural


def _is_favorite(predicate: str) -> bool:
    return predicate.startswith("favorite_") or "favorite" in predicate.split("_")

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


def _classify_predicate(predicate: str, explicit: str | None, *, source: str | None = None) -> str:
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
    # Kazma's own class wins over the caller's label: the agent's memory tools
    # label a fact "set" by default, and a list label on "lives_in" added a
    # second home beside the user's, past the trust gate (2026-11, the memory
    # framework audit's F7). Read from the name's words, never its exact
    # spelling (``_kazma_name``), and favorite_* stays single-valued -- but a
    # list the user's own words made on a name a person holds several of
    # stays a list (``_LISTABLE``). A plural name is a list ("names",
    # "favorite_foods": ``_names_a_list``).
    if _names_a_list(p):
        return "set"
    named = _kazma_name(p)
    if named in _FUNCTIONAL_PREDICATES or _is_favorite(p):
        from kazma_core.memory.provenance import USER_SOURCES

        if named in _LISTABLE and explicit == "set" and source in USER_SOURCES:
            return "set"
        return "functional"
    if named in _STATE_PREDICATES:
        return "state"
    if explicit in ("functional", "set", "state"):
        return explicit
    return "set"


def kazma_classifies(predicate: str) -> bool:
    """Whether Kazma itself says how many values *predicate* holds (the
    functional and state lists, ``favorite_*``, the rotating current facts),
    whatever the extractor's label. A plural of one of them is a list."""
    if _kazma_name(predicate) is not None or _is_favorite(predicate) or _names_a_list(predicate):
        return True
    from kazma_core.memory.current_facts import is_functional_current_predicate

    return bool(is_functional_current_predicate(predicate))


def _established_kind(
    conn: sqlite3.Connection, *, tenant_id: str, subject: str, predicate: str, ptype: str,
) -> str:
    """A predicate the subject already keeps as a list stays a list.

    The extractor labels each fact's type itself, and a single-valued label
    on a list's predicate made the write supersede the newest entry and close
    every other one as "extra current values": live 2026-10-19 the deep pass
    stored ``user noted "test codeword is ..."`` as functional and closed 57
    of the owner's notes. A predicate Kazma itself classifies (the functional
    and state lists, ``favorite_*``, the rotating current facts) keeps the
    type Kazma gives it.
    """
    if kazma_classifies(predicate):
        return ptype
    if ptype == "set":
        # The reverse (the third review of F1, item 5): a value labelled a
        # list on a fact the user keeps single ("blood_type") takes the
        # class of the user's own row, so it never stands beside it.
        try:
            held = conn.execute(
                "SELECT predicate_type FROM beliefs WHERE tenant_id=? AND subject=? AND predicate=? "
                "AND predicate_type IN ('functional', 'state') "
                "AND extraction_method IN ('user_explicit', 'user_stated') "
                "AND valid_until IS NULL AND invalidated_at IS NULL LIMIT 1",
                (tenant_id, subject, predicate),
            ).fetchone()
        except sqlite3.Error:
            logger.debug("[belief_mutate] could not read %s %s's kind; keeping %s", subject, predicate, ptype)
            return ptype
        if held:
            logger.info("[belief_mutate] %s %s is the user's single value: this list-labelled value is one too",
                        subject, predicate)
            return str(held[0])
        return ptype
    if ptype not in ("functional", "state"):
        return ptype
    try:
        holds_list = conn.execute(
            "SELECT 1 FROM beliefs WHERE tenant_id=? AND subject=? AND predicate=? "
            "AND predicate_type='set' AND valid_until IS NULL AND invalidated_at IS NULL LIMIT 1",
            (tenant_id, subject, predicate),
        ).fetchone()
    except sqlite3.Error:
        logger.debug("[belief_mutate] could not read %s %s's kind; keeping %s", subject, predicate, ptype)
        return ptype
    if holds_list:
        logger.info("[belief_mutate] %s %s holds a list: writing this %s value as one more entry",
                    subject, predicate, ptype)
        return "set"
    return ptype


#: The account whose words the write in progress are (``mutate_belief``'s
#: *said_by*), read where the row is built.
_SAID_BY: contextvars.ContextVar[str | None] = contextvars.ContextVar("kazma_belief_said_by", default=None)
#: When the fact the write in progress records began, as its turn named it
#: (``mutate_belief``'s *started*), read where the row is built.
_STARTED: contextvars.ContextVar[str | None] = contextvars.ContextVar("kazma_belief_started", default=None)
#: Whether that start is the day an event happened (``mutate_belief``'s
#: *happened*: "I went to a pottery exhibition last Saturday"), not the day
#: a lasting fact began (the memory plan's W4, dated event lines).
_HAPPENED: contextvars.ContextVar[bool] = contextvars.ContextVar("kazma_belief_happened", default=False)
#: The day an event is planned for (``mutate_belief``'s *planned*).
_PLANNED: contextvars.ContextVar[str | None] = contextvars.ContextVar("kazma_belief_planned", default=None)
#: ``(the write's own source, the day's source when it is another's)``, each
#: ``fact_days``' ``day_source`` shape: who gave the day.
_DAY_SOURCES: contextvars.ContextVar[tuple[dict[str, Any] | None, dict[str, Any] | None]] = contextvars.ContextVar(
    "kazma_belief_day_sources", default=(None, None))
#: Whether the user said the event's day by counting from the turn's own.
_DAY_SURE: contextvars.ContextVar[bool] = contextvars.ContextVar("kazma_belief_day_sure", default=False)


def _trust_weight(extraction_method: str, cfg: dict[str, Any] | None) -> float:
    """The ladder of ``memory.provenance``, as a weight."""
    v2 = (cfg or {}).get("v2") or {}
    if extraction_method == "user_explicit":
        return float(v2.get("trust_weight_user", 1.0))
    if extraction_method == "user_stated":
        return float(v2.get("trust_weight_user_stated", 0.9))
    if extraction_method == "system_tool":
        return float(v2.get("trust_weight_tool", 0.85))
    if extraction_method == "agent_asserted":
        return float(v2.get("trust_weight_agent", 0.65))
    if extraction_method == "member_stated":
        # One member's words about another (Spaces S1-11, N34): a person's
        # word, but not theirs about themself.
        return float(v2.get("trust_weight_member", 0.7))
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
    tenant_id: str | None = None,
    source_session: str | None = None,
    source_turn: int | None = None,
    cfg: dict[str, Any] | None = None,
    subject_id: str | None = None,
    now: float | None = None,
    private: bool = False,
    confirms_change: tuple[str, str] | None = None,
    replaces_users_fact: bool = True,
    said_by: str | None = None,
    started: str | None = None,
    happened: bool = False,
    planned: str | None = None,
    day_sure: bool = False,
    day_source: dict[str, Any] | None = None,
    corrects: str | None = None,
) -> dict[str, Any]:
    """Apply a belief mutation per the predicate-type rules.

    *started* is when the fact began, as the turn named it (``YYYY``,
    ``YYYY-MM`` or ``YYYY-MM-DD``: "we moved last month"; framework audit
    F2 step 2), kept in the row's metadata (``started``) and shown by
    recall. It never moves *now*: validity and supersession follow the order
    facts were said in, whatever start a statement names. *happened*: that
    start is the day an event happened ("I went to a pottery exhibition
    last Saturday"; the memory plan's W4), kept as ``happened`` beside it,
    so recall shows "[on 13 May 2023]", not "[since ...]". *planned*: the day
    an event is planned for, after the turn that told it. Both are a list
    entry's only: on a single-valued fact the day is its start. *day_sure*:
    the user counted to that day ("yesterday"; ``fact_days``). *day_source*
    names who gave the day when it is not this write's own turn
    (``fact_days``: a day taken over from the entry this one replaces).
    *corrects* is the list entry this write tells again, whose day it may
    correct (the same words with the day's number: "replaces").

    *said_by* is the account whose words these are, in a shared space
    (Spaces S1-11, ``memory/speakers.py``): kept in the fact's metadata, so
    the fact names who said it. Nothing else reads it here: the keys and the
    rules are the same for every speaker. None (every personal space) keeps
    the row exactly as it was.

    *confirms_change* is ``(change id, actor)`` of the pending change this
    write answers (``changes.confirm``): marked confirmed in the write's own
    transaction, never superseded by it.

    *replaces_users_fact* False: the write may add a fact, never replace one
    the user stated, whatever its source -- it answers ``blocked:
    lower_trust_source``, as the trust gate does. A memory tool's write is
    the model's choice of subject and name even when the value is the
    user's (the third review of F1, item 1): a replacement it would make is
    put to the user instead (``changes.record_pending``).

    *now* is when the fact was stated (default: this moment): the valid
    time (``valid_from`` / ``valid_until``). Something that records a fact
    after the fact -- turn reconcile, the retrieval benchmark on its fixed
    clock -- passes the statement's own time, and validity and supersession
    follow the order the facts were SAID in, whatever order they are written
    in. ``ingested_at`` / ``invalidated_at`` are when this write happened.

    *private*: *primary_conn* is a private database (the retrieval
    benchmark's), and nothing may leave it -- no state mirror, graph backend
    or remote vector index write. Those are process-wide, so
    without this a private write landed in the install's shared stores (the
    golden eval's class, AGENTS.md section 15F item I).

    Returns a dict describing the outcome::

        {"action": "supersede"|"append"|"transition"|"noop",
         "belief_id": str,
         "superseded_id": str | None}

    Raises ``sqlite3.Error`` when the store refuses the write itself (a
    locked or busy database is an outage the caller retries, never a "noop":
    AUD-027). Any other failure is logged at WARNING and returns
    ``{"action": "noop", ..., "error": <type>}``; a failure after the write
    committed returns the write's result.
    """
    tenant_id = space_named_or_bound(tenant_id)
    # One set of limits for every writer (memory/limits.py, Memory audit
    # M36): refused, never cut -- a cut value is a different fact.
    from kazma_core.memory.limits import too_long

    field = too_long(subject=str(subject_id or subject or ""), predicate=str(predicate or ""), obj=str(obj or ""))
    if field:
        return {"action": "noop", "belief_id": "", "superseded_id": None, "rejected": f"{field}_too_long"}
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

    # Classified by the name's words ("in_lives" is "lives_in"'s class);
    # canonical_predicate below then writes it under the name the subject
    # already uses for those words, so the trust gate sees the user's fact.
    pred = (predicate or "").strip().lower().replace(" ", "_") or "related"
    ptype = _classify_predicate(pred, predicate_type, source=extraction_method)
    # Operator-driven links may carry an explicit subject_id (the exact
    # graph node id the operator clicked, e.g. a virtual-fact id like
    # "ShipX — Deployment Modes"). Slugifying that id would mint a
    # different entity and detach the edge from the node the operator
    # clicked. When subject_id is provided, use it verbatim; otherwise
    # slugify the free-text subject (the historical behavior).
    if not subject_id and not (subject or "").strip():
        # An empty subject names nothing; it used to become the id "entity".
        return {"action": "noop", "belief_id": "", "superseded_id": None,
                "rejected": "empty_subject"}
    sub = subject_id if subject_id else _slug(subject)
    # Follow merged_into so beliefs land on the canonical entity, not a
    # retired id. This is the write-side fix for the mubder→user re-orphan
    # bug: extraction kept minting under 'mubder' after the merge because
    # nothing read merged_into. Also applied to obj below (objects can be
    # entity ids). Best-effort; on any failure the id is unchanged.
    try:
        from kazma_core.memory.entity_resolution import canonical_entity_id
        sub = canonical_entity_id(primary_conn, sub, tenant_id=tenant_id)
    except Exception:
        pass
    # Also follow merged_into for the object when it's an entity id. Objects
    # can be entity ids (e.g. operator links), and a retired object id would
    # otherwise leave a dangling edge to a merged-away node.
    try:
        from kazma_core.memory.entity_resolution import canonical_entity_id
        obj = canonical_entity_id(primary_conn, obj, tenant_id=tenant_id)
    except Exception:
        pass
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
    recorded = time.time()
    now = recorded if now is None else float(now)

    result: dict[str, Any] | None = None
    own = {"session": source_session, "turn": source_turn, "method": extraction_method, "at": recorded}
    tokens = _bind_write_context(said_by, started, happened, planned, own, day_source, day_sure)
    try:
        with _mutation_lock:
            # W6: the name the subject already uses for the same words and
            # type (memory/predicates.py) -- so the same value is a no-op, a
            # new value supersedes and a set appends, instead of a second
            # name holding a second, conflicting value.
            from kazma_core.memory.predicates import canonical_predicate

            try:
                pred = canonical_predicate(
                    primary_conn, tenant_id=tenant_id, subject=sub,
                    predicate=pred, predicate_type=ptype,
                )
            except sqlite3.Error:
                logger.debug("[belief_mutate] predicate names unreadable; keeping %r", pred)
            ptype = _established_kind(primary_conn, tenant_id=tenant_id, subject=sub,
                                      predicate=pred, ptype=ptype)
            if ptype != "set":
                # A day something happened, or is planned for, is a list
                # entry's; on a single value a day that has come is its start.
                _HAPPENED.set(False)
                _PLANNED.set(None)
            if ptype == "functional":
                result = _mutate_functional(
                    primary_conn, ops_conn, sub, pred, obj,
                    confidence=confidence, importance=importance, trust=trust,
                    extraction_method=extraction_method, tenant_id=tenant_id,
                    source_session=source_session, source_turn=source_turn,
                    mem_class=mem_class, now=now, recorded=recorded, cfg=cfg,
                    private=private, confirms_change=confirms_change,
                    replaces_users_fact=replaces_users_fact,
                )
            elif ptype == "state":
                result = _mutate_state(
                    primary_conn, ops_conn, sub, pred, obj,
                    confidence=confidence, importance=importance, trust=trust,
                    extraction_method=extraction_method, tenant_id=tenant_id,
                    source_session=source_session, source_turn=source_turn,
                    mem_class=mem_class, now=now, recorded=recorded, cfg=cfg,
                    private=private, replaces_users_fact=replaces_users_fact,
                )
            else:
                result = _mutate_set(
                    primary_conn, ops_conn, sub, pred, obj,
                    confidence=confidence, importance=importance, trust=trust,
                    extraction_method=extraction_method, tenant_id=tenant_id,
                    source_session=source_session, source_turn=source_turn,
                    mem_class=mem_class, now=now, recorded=recorded, cfg=cfg,
                    private=private, corrects=corrects,
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
        # Best-effort dual-write to shared state / graph backends (P2-2/P2-3).
        # A history row (W1) and the neighbours it re-bounded reach the state
        # mirror with their validity; the graph holds current facts only, so
        # a history row does not go there. (The "unified index" copy of every
        # fact is gone: it had no delete path, so a forgotten value stayed
        # searchable -- memory_stores.py, 2026-10-13.)
        history = result.get("action") == "history"
        if not private and result.get("superseded_id"):
            # The superseded fact's edge leaves the graph, and its death flags
            # reach the mirror -- after the commit (Memory audit M14).
            try:
                from kazma_core.memory.graph_backend import delete_belief_edge

                delete_belief_edge(
                    belief_id=str(result["superseded_id"]), subject=sub, predicate=pred,
                    obj=str(result.get("superseded_object") or ""), tenant_id=tenant_id,
                )
            except Exception:
                logger.debug("[belief_mutate] neo4j delete on supersede skipped", exc_info=True)
        if not private and result.get("action") not in ("noop", None) and result.get("belief_id"):
            try:
                from kazma_core.memory.graph_backend import upsert_belief_edge
                from kazma_core.memory.state_backend import remirror_belief_by_id

                bid = str(result["belief_id"])
                # Mirror the PERSISTED row (M-04): guarantees the mirror sees
                # exactly what SQLite committed, death flags included — never
                # a hand-built "definitely alive" dict. (2026-08-26 audit: was
                # `conn`, an undefined name — the NameError was swallowed and
                # the new-belief mirror/graph dual-write never ran.)
                for mirrored in [bid, *(result.get("touched") or []),
                                 *([result["superseded_id"]] if result.get("superseded_id") else [])]:
                    remirror_belief_by_id(primary_conn, str(mirrored))
                if not history:
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
        # Phase 3: keep the materialized entity belief_count / graph_degree
        # columns in sync. Recompute the affected subject + object so the
        # operator /memory page reads precomputed values instead of running
        # per-row correlated subqueries. Only on a real mutation (not noop).
        # The object is included even when it's a literal — recompute is a
        # no-op for ids with no entity row. Caller owns the commit.
        affected = [sub, obj]
        gone = ""
        from kazma_core.memory.entity_counts import entity_ids_named, recompute_entity_counts

        try:
            # M-09: a supersede also DROPS the old row — its former object
            # entity (when it was an entity id) loses a belief that nobody
            # recomputed otherwise.
            if result.get("superseded_id"):
                srow = primary_conn.execute(
                    "SELECT object FROM beliefs WHERE id=?",
                    (str(result["superseded_id"]),),
                ).fetchone()
                if srow and srow[0]:
                    gone = str(srow[0])
                    affected.append(gone)
            recompute_entity_counts(primary_conn, entity_ids_named(primary_conn, affected, tenant_id=tenant_id),
                                    tenant_id=tenant_id)
            primary_conn.commit()
        except Exception:
            logger.debug("[belief_mutate] entity count recompute failed", exc_info=True)
        # A superseded value's entity may now hold nothing (entity_retire).
        if gone:
            from kazma_core.memory.entity_retire import retire_empty_entities

            try:
                if retire_empty_entities(primary_conn, entity_ids_named(primary_conn, [gone], tenant_id=tenant_id),
                                         tenant_id=tenant_id):
                    primary_conn.commit()
            except sqlite3.Error:
                logger.warning("[belief_mutate] empty-entity cleanup failed", exc_info=True)
        if result.get("action") not in ("noop", None) and result.get("belief_id"):
            _store_belief_vector(primary_conn, str(result["belief_id"]), sub, pred, obj,
                                 tenant_id=tenant_id, mem_class=mem_class, private=private)

        return result
    except Exception as exc:
        return _after_failure(exc, result)
    finally:
        _unbind_write_context(tokens)


def file_under(conn: sqlite3.Connection, belief_id: str, name: str, *, tenant_id: str) -> bool:
    """File a current fact of the user's anew under *name*, Kazma's name for
    what it says (``fact_names.name_to_file``): a role held as "has_occupation"
    from before the table. The value, its source and its history stay; the
    change reaches the mirror and the graph as any does (their outboxes). What
    is then written replaces it, or is refused, as under any one name."""
    from kazma_core.memory.hygiene import beliefs_write

    with _mutation_lock:
        began = not conn.in_transaction
        done = beliefs_write(conn, "UPDATE beliefs SET predicate = ? WHERE id = ? AND tenant_id = ? "
                                   "AND valid_until IS NULL AND invalidated_at IS NULL", (name, belief_id, tenant_id))
        if began and conn.in_transaction:
            conn.commit()
    return bool(getattr(done, "rowcount", 0))


def end_belief(
    primary_conn: sqlite3.Connection,
    belief_id: str,
    *,
    tenant_id: str,
    extraction_method: str,
    now: float | None = None,
    ops_conn: sqlite3.Connection | None = None,
    source_session: str | None = None,
    source_turn: int | None = None,
    confirms_change: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """End a current fact: it stopped being true ("I stopped", "no longer";
    the memory framework audit's part C2, retractions). *now* is when that
    was said: the fact held until then.

    The trust gate decides as for any change (``provenance.may_overrule``):
    a fact the user stated ends only by the user's own words. A model's
    reading answers ``blocked: lower_trust_source`` and changes nothing; the
    caller puts it to the user (``changes.record_pending_end``). The user's
    own fact ended records an update they can undo (``changes.record_update``
    with no new value); *confirms_change* is the pending change it answers.
    Closed under the mutation lock in one transaction, which queues the
    mirror's and the graph's copies (their outboxes): nothing else leaves
    the database here."""
    from kazma_core.memory import changes
    from kazma_core.memory.hygiene import beliefs_write
    from kazma_core.memory.provenance import USER_SOURCES, may_overrule

    recorded = time.time()
    ended_at = recorded if now is None else float(now)
    change_id = None
    with _mutation_lock:
        began = not primary_conn.in_transaction
        if began:
            primary_conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            row = primary_conn.execute(
                "SELECT subject, predicate, object, extraction_method, valid_from FROM beliefs WHERE id = ? "
                "AND tenant_id = ? AND valid_until IS NULL AND invalidated_at IS NULL AND COALESCE(object, '') != ''",
                (belief_id, tenant_id)).fetchone()
            if row is None or not may_overrule(row[3], extraction_method):
                blocked = {"blocked": "lower_trust_source"} if row is not None else {"reason": "not_current"}
                return {"action": "noop", "belief_id": belief_id, **blocked}
            beliefs_write(primary_conn, "UPDATE beliefs SET valid_until = ?, invalidated_at = ? WHERE id = ? "
                          "AND tenant_id = ? AND valid_until IS NULL",
                          (max(float(row[4] or ended_at), ended_at), recorded, belief_id, tenant_id))
            if row[3] in USER_SOURCES:
                change_id = changes.record_update(
                    primary_conn, tenant_id=tenant_id, subject=str(row[0]), predicate=str(row[1]),
                    old_belief_id=belief_id, old_value=str(row[2] or ""), new_belief_id=None, new_value="",
                    session=source_session, turn=source_turn, now=recorded, confirms=confirms_change)
            if began:
                primary_conn.commit()
            committed = True
        finally:
            # A refusal or a failure leaves nothing changed, and the write
            # lock released (only a transaction this call opened).
            if began and not committed and primary_conn.in_transaction:
                primary_conn.rollback()
    _write_audit(ops_conn, tenant_id=tenant_id, event_type="end", target_id=belief_id,
                 actor=str(extraction_method), reason="the fact stopped being true",
                 state_before={"id": belief_id}, state_after={"id": belief_id, "ended": True})
    _after_ending(primary_conn, row, tenant_id=tenant_id)
    return {"action": "ended", "belief_id": belief_id, "change_id": change_id}


def _after_ending(conn: sqlite3.Connection, row: Any, *, tenant_id: str) -> None:
    """What follows a fact's end, after its transaction: the entities'
    counts, and an entity it was the last fact of, holding nothing else, goes
    with it, as a forgotten fact's does (until 2026-11-24 "no longer true"
    left its value on the map as a bare entity). The state mirror and the graph copy follow from their outboxes,
    which the close queued in its own transaction (``schema_v2``'s
    triggers; the mirror and graph syncs drain them), so a *private*
    database sends nothing anywhere."""
    try:
        from kazma_core.memory.entity_counts import entity_ids_named, recompute_entity_counts
        from kazma_core.memory.entity_retire import retire_empty_entities

        named = entity_ids_named(conn, [row[0], row[2]], tenant_id=tenant_id)
        recompute_entity_counts(conn, named, tenant_id=tenant_id)
        retire_empty_entities(conn, named, tenant_id=tenant_id)
        if conn.in_transaction:
            conn.commit()
    except sqlite3.Error:
        logger.warning("[belief_mutate] entity counts after an ending were not recomputed", exc_info=True)


def _same_fact_again(conn: sqlite3.Connection, belief_id: str, tenant_id: str, *, began: bool) -> dict:
    """The outcome of writing the fact *belief_id* already holds: a start
    its turn names is added (:func:`_add_named_start`), else nothing."""
    gained = _add_named_start(conn, belief_id, tenant_id, began=began)
    if gained:
        return gained
    # Release the BEGIN IMMEDIATE lock we acquired (no writes to commit).
    # Only roll back if WE started the txn -- if the caller already had one
    # open, leave it to the caller (audit finding: early returns leaked the
    # write lock until the connection closed if the caller's later commit
    # raised).
    if began:
        try:
            conn.rollback()
        except Exception:
            pass
    return {"action": "noop", "belief_id": belief_id, "superseded_id": None}


def _written_day(*, gained: bool) -> dict[str, Any]:
    """The day the write in progress names (``fact_days``), with who gave it:
    always when a row of another turn gains it, only another turn's when the
    row is this write's own."""
    day: dict[str, Any] = {}
    if _STARTED.get():
        day["started"] = _STARTED.get()
        if _HAPPENED.get():
            day["happened"] = True
            if _DAY_SURE.get():
                day["day_sure"] = True
    if _PLANNED.get():
        day["planned"] = _PLANNED.get()
    own, others = _DAY_SOURCES.get()
    source = others or (own if gained else None)
    if day and source:
        day["day_source"] = {k: v for k, v in source.items() if v is not None}
    return day


def _add_named_start(conn: sqlite3.Connection, belief_id: str, tenant_id: str, *, began: bool,
                     correct: bool = False) -> dict | None:
    """The same fact again, now with the start its turn names ("since 2019";
    framework audit F2 step 2) or the day it happened (W4): kept on the row
    that holds none, never over one it holds -- but an event's day said less
    exactly ("in May") takes the exact one, and with *correct* another day
    replaces it (the day it held, and who gave it, stay behind it:
    ``fact_days``). The value and its source stay; the day keeps its own. The
    write's outcome, or None when there is nothing to add."""
    from kazma_core.memory import fact_days
    from kazma_core.memory.hygiene import beliefs_write

    day = _written_day(gained=True)
    if not day:
        return None
    row = conn.execute("SELECT metadata_json FROM beliefs WHERE id = ? AND tenant_id = ?",
                       (belief_id, tenant_id)).fetchone()
    if row is None:
        return None
    held = fact_days.metadata_of(_col(row, "metadata_json", 0))
    told, kept = fact_days.event_day(day), fact_days.any_day(held)
    if fact_days.has_day(held):
        # A planned day that came, or a start that was one day's: now the day
        # it happened.
        came = bool(day.get("happened") and not held.get("happened") and fact_days.compatible(told, kept))
        if not told or told == kept and not came or not (came or correct or fact_days.vaguer(kept, told)):
            return None
        if came and fact_days.vaguer(told, kept):
            day["started"] = kept
    open_before = conn.in_transaction
    # An event is a list entry: told first without its day, it may be a row
    # the model labelled single-valued (the user's own never: its write would
    # have taken that kind, ``_established_kind``).
    cur = beliefs_write(
        conn, "UPDATE beliefs SET metadata_json = ?, predicate_type = CASE WHEN ? THEN 'set' ELSE predicate_type END "
        "WHERE id = ? AND tenant_id = ? AND metadata_json IS ?",
        (json.dumps(fact_days.with_day(held, day), ensure_ascii=False), 1 if told else 0, belief_id, tenant_id,
         _col(row, "metadata_json", 0)))
    if not getattr(cur, "rowcount", 0):
        # The UPDATE opened a transaction on a default-mode connection even
        # though it matched nothing; left open it holds the write lock. A
        # caller's own transaction stays the caller's.
        if not open_before and conn.in_transaction:
            conn.rollback()
        return None
    if began:
        conn.commit()
    return {"action": "noop", "belief_id": belief_id, "superseded_id": None, "started_recorded": True}


def _bind_write_context(said_by: str | None, started: str | None, happened: bool = False,
                        planned: str | None = None, own: dict[str, Any] | None = None,
                        day_source: dict[str, Any] | None = None, day_sure: bool = False) -> tuple[Any, ...]:
    """Bind who said the write in progress and the day its turn named (a
    start, an event's day, a planned day, and whose day it is), read where
    the row is built (:func:`_insert_belief`)."""
    return (_SAID_BY.set(str(said_by) if said_by else None), _STARTED.set(str(started) if started else None),
            _HAPPENED.set(bool(happened and started)), _PLANNED.set(str(planned) if planned else None),
            _DAY_SOURCES.set((own, day_source)), _DAY_SURE.set(bool(day_sure)))


def _unbind_write_context(tokens: tuple[Any, ...]) -> None:
    said, started, happened, planned, sources, sure = tokens
    _DAY_SURE.reset(sure)
    _DAY_SOURCES.reset(sources)
    _PLANNED.reset(planned)
    _HAPPENED.reset(happened)
    _STARTED.reset(started)
    _SAID_BY.reset(said)


def _after_failure(exc: Exception, result: dict[str, Any] | None) -> dict[str, Any]:
    """What :func:`mutate_belief` answers when *exc* ended it (called from its
    ``except``); *result* is the write's outcome when it had committed.

    A store refusal (locked, busy, read-only, corrupt) before the write is an
    outage, never a verdict, and is raised. Returned as a "noop" it was a fact
    lost in silence: turn reconcile moved its cursor past it and the post-turn
    pass counted the turn ok, so nothing retried (audit 2026-10-26 AUD-027;
    Memory audits M12, M27). The callers own the retry.
    """
    if result is not None:
        # The fact is written; a follow-up step after the commit failed.
        logger.warning("[belief_mutate] follow-up after the write failed", exc_info=exc)
        return result
    if isinstance(exc, sqlite3.Error):
        logger.warning("[belief_mutate] the memory store refused the write", exc_info=exc)
        raise exc
    logger.warning("[belief_mutate] mutation failed", exc_info=exc)
    return {"action": "noop", "belief_id": "", "superseded_id": None, "error": type(exc).__name__}


def _store_belief_vector(
    conn: sqlite3.Connection, bid: str, sub: str, pred: str, obj: str, *,
    tenant_id: str, mem_class: str, private: bool,
) -> None:
    """The fact's vector (its subject, predicate and object), embedded once
    with no transaction open and outside the mutation lock, written only while
    the row still holds *obj* (a forget empties it). The remote index is
    skipped for a private database (Memory audits M14, M15)."""
    from kazma_core.memory.vector_write import encode_vector, index_remotely, write_local_vector

    from kazma_core.memory.speakers import fact_vector_text

    vec = encode_vector(fact_vector_text(sub, pred, obj))
    if not vec:
        return
    try:
        write_local_vector(conn, "beliefs", bid, vec, still_sql="object = ?", still_params=(obj,))
    except sqlite3.Error:
        logger.warning("[belief_mutate] could not store the vector of %s (vector repair retries)", bid,
                       exc_info=True)
    if private:
        return

    def _still_held() -> bool:
        return conn.execute("SELECT 1 FROM beliefs WHERE id = ? AND object = ?", (bid, obj)).fetchone() is not None

    index_remotely(bid, vec, tenant_id=tenant_id, meta={"kind": "belief", "tier": mem_class or "semantic"},
                   still_held=_still_held)


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
    private: bool = False,
    recorded: float | None = None,
) -> dict[str, Any]:
    meta = {"memory_class": mem_class}
    said_by = _SAID_BY.get()
    if said_by:
        meta["said_by"] = said_by
    meta.update(_written_day(gained=False))
    try:
        from kazma_core.memory.embedder import get_embedding_model_name

        emb_version = get_embedding_model_name()
    except Exception:
        emb_version = ""
    from kazma_core.memory.hygiene import beliefs_write

    params = (
        bid, tenant_id, sub, pred, ptype, obj,
        float(confidence), int(importance), float(trust),
        now, now if recorded is None else recorded, supersedes_id, source_session, source_turn,
        extraction_method, json.dumps(meta, ensure_ascii=False),
        emb_version,
    )
    insert_sql = """INSERT OR IGNORE INTO beliefs
           (id, tenant_id, subject, predicate, predicate_type, object,
            confidence, structural_importance, source_trust_weight,
            valid_from, ingested_at, supersedes_id, source_session,
            source_turn, extraction_method, metadata_json,
            embedding_model_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    cur = beliefs_write(conn, insert_sql, params)
    # M-11: INSERT OR IGNORE can drop a PK collision silently — the functional
    # path already closed the prior fact, so a no-row insert would leave no
    # successor. Retry once with a fresh uuid; if still ignored, raise so
    # the caller rolls back the supersede close.
    if not _insert_landed(conn, cur, bid):
        import uuid as _uuid

        bid = "b_" + _uuid.uuid4().hex[:26]
        params = (bid,) + params[1:]
        cur = beliefs_write(conn, insert_sql, params)
        if not _insert_landed(conn, cur, bid):
            raise sqlite3.IntegrityError(
                f"belief insert ignored (id collision, retry failed) subject={sub} pred={pred}"
            )
    # The fact's vector is written by mutate_belief AFTER the transaction and
    # outside the mutation lock (_store_belief_vector, Memory audit M14): an
    # embedding here ran under BEGIN IMMEDIATE and the process-wide lock, and
    # the local upsert committed the caller's transaction (M15).
    del private
    return {
        "id": bid,
        "subject": sub, "predicate": pred, "predicate_type": ptype,
        "object": obj, "confidence": confidence, "importance": importance,
        "memory_class": mem_class,
    }


def _insert_landed(
    conn: sqlite3.Connection,
    cur: sqlite3.Cursor,
    bid: str,
) -> bool:
    """True when INSERT OR IGNORE actually wrote ``bid``.

    ``Cursor.rowcount`` is 0 on a ignored PK collision; some drivers report
    -1, in which case we fall back to a SELECT.
    """
    n = int(cur.rowcount if cur.rowcount is not None else -1)
    if n > 0:
        return True
    if n == 0:
        return False
    try:
        return conn.execute("SELECT 1 FROM beliefs WHERE id=? LIMIT 1", (bid,)).fetchone() is not None
    except Exception:
        return False


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
        "private": bool(kw.get("private")),
        "recorded": kw.get("recorded"),
    }


def _same_value(old: Any, new: Any) -> bool:
    """Whether two values are one value written two ways: compared after
    folding case, underscores, spacing and Arabic letter forms and marks
    (``documents.arabic.fold_for_search``)."""
    if old == new:
        return True
    if not old or not new:
        return False
    from kazma_core.documents.arabic import fold_for_search

    def folded(value: Any) -> str:
        return " ".join(fold_for_search(str(value).replace("_", " ")).split())

    return folded(old) == folded(new)


def _survivor(current: list[Any]) -> Any:
    """Which of a single-valued fact's current rows (newest first) stays
    current when there are several: the newest the user said, else the
    newest. A value no lower source could have replaced (the trust gate,
    ``provenance.may_overrule``) is never closed as an extra: an agent's write
    labelled a list once stood beside the user's own fact, and the clean-up
    kept the newer one and closed the user's (F7)."""
    from kazma_core.memory.provenance import USER_SOURCES

    if not current:
        return None
    for row in current:
        if _col(row, "extraction_method", 2) in USER_SOURCES:
            return row
    return current[0]


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
    # BEGIN IMMEDIATE acquires a write lock up front so the read below
    # sees ALL prior committed writes (no stale WAL snapshot). This is
    # essential when mutations run across separate connections/threads —
    # a deferred read transaction could miss a just-committed supersede.
    began = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        began = True
    except sqlite3.Error:
        pass  # already in the caller's transaction, which the caller ends
    ended = False
    try:
        result = _mutate_functional_locked(conn, ops_conn, sub, pred, obj, began=began, **kw)
        ended = True
        return result
    finally:
        # A transaction this call began ends here, whatever went wrong in
        # it: left open it held memory_state.db's write lock until the
        # connection closed (tests/test_sqlite_kept_connections.py).
        if not ended and began and conn.in_transaction:
            conn.rollback()


def _mutate_functional_locked(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection | None,
    sub: str, pred: str, obj: str, *, began: bool, **kw: Any,
) -> dict[str, Any]:
    """:func:`_mutate_functional` inside its transaction (``began``: this
    call opened it, so its early exits end it)."""
    tenant_id = kw["tenant_id"]
    now = kw["now"]
    audit_event_type = kw.get("audit_event_type", "supersede")
    # The currently-active belief for this (subject, predicate): the newest,
    # and only one (Memory audit M26 -- ``LIMIT 1`` with no order read any of
    # several). Extra current rows (a crash between two writes) are closed at
    # the newest's start, so a single-valued fact has one value again. A list
    # entry ('set') is never one of them: closing those closed 57 notes on
    # live (2026-10-19, ``_established_kind``) -- unless Kazma itself says
    # the predicate holds one value, where an older row labelled a list is a
    # stale value of it.
    lists_kept = "" if kazma_classifies(pred) else "AND COALESCE(predicate_type, '') != 'set'"
    current = conn.execute(
        f"""SELECT id, object, extraction_method, valid_from, source_session, source_turn, predicate_type
           FROM beliefs
           WHERE subject=? AND predicate=? AND tenant_id=?
             {lists_kept}
             AND valid_until IS NULL AND invalidated_at IS NULL
           ORDER BY valid_from DESC, ingested_at DESC""",  # nosec B608 - a fixed clause, values bound
        (sub, pred, tenant_id),
    ).fetchall()
    existing = _survivor(current)
    # The user's own list on a name a person holds several of (``_LISTABLE``)
    # is no set of stale values: it stays until a write that may replace the
    # user's value replaces all of it, below. Closed here, a refused agent
    # write closed the user's other jobs first.
    held_list = [r for r in current if r is not existing and _users_list_entry(pred, r)]
    extra = [r for r in current if r is not existing and not _users_list_entry(pred, r)]
    if extra:
        from kazma_core.memory.hygiene import beliefs_write

        kept_from = float(_col(existing, "valid_from", 3) or now)
        logger.warning("[belief_mutate] %d current values of %s %s: keeping %s, closing %s",
                       len(current), sub, pred, _col(existing, "id", 0), [str(_col(r, "id", 0)) for r in extra])
        for row in extra:
            # An older value held until the kept one began; a newer one never
            # held in its place (it closes where it began).
            ends = max(kept_from, float(_col(row, "valid_from", 3) or kept_from))
            beliefs_write(conn, "UPDATE beliefs SET valid_until=?, invalidated_at=? WHERE id=?",
                          (ends, kw.get("recorded") or now, str(_col(row, "id", 0))))
        if began:
            # Kept whatever this call decides next (a no-op rolls back).
            conn.commit()
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error:
                conn.rollback()  # nothing is open; the caller's finally needs no transaction
                raise
    if existing is not None and now < float(_col(existing, "valid_from", 3) or 0.0):
        # Said before the current fact was: history, never a replacement.
        return _record_earlier_statement(conn, ops_conn, sub, pred, obj, began=began, **kw)
    superseded_id = None
    state_before = None
    users_fact_replaced = False
    if existing:
        superseded_id = existing["id"] if isinstance(existing, sqlite3.Row) else existing[0]
        old_obj = existing["object"] if isinstance(existing, sqlite3.Row) else existing[1]
        existing_method = (
            existing["extraction_method"]
            if isinstance(existing, sqlite3.Row) else existing[2]
        )
        # The same value written another way ("lisbon", "gulf_bank", a vowel
        # mark) is no change, and the stored spelling -- the user's -- stays:
        # compared letter for letter, the model's respelling replaced the
        # user's own fact in its own turn and took the user's trust with it
        # (2026-11, the memory framework audit's F1). Except the user's own
        # word for what the model read: the fact is then the user's, at the
        # user's trust (a confirmed first reading, memory/changes.py).
        from kazma_core.memory.provenance import USER_SOURCES as _USER_SOURCES

        raises_trust = kw["extraction_method"] in _USER_SOURCES and existing_method not in _USER_SOURCES
        if _same_value(old_obj, obj) and not raises_trust:
            return _same_fact_again(conn, str(superseded_id), tenant_id, began=began)
        # Commitment Layer Phase 1 — source-trust gate (plan §3.6 rule 2):
        # a fact the user stated may NOT be superseded by a lower-trust
        # source (``memory.provenance.may_overrule``: an agent tool call, the
        # model pass, a migration). This is the memory-corruption half of
        # the CoPilot incident — the post-turn extractor invented a date and
        # overwrote a user-asserted copilot_next_reset. Fail-closed: drop the
        # overwrite; the user fact stands. Kill-switch:
        # cfg.v2.functional_supersede_requires_user_assert (default True).
        from kazma_core.memory.provenance import may_overrule

        gate_cfg = ((kw.get("cfg") or {}).get("v2") or {})
        same_turn = (
            kw.get("source_session") is not None
            and _col(existing, "source_session", 4) == kw.get("source_session")
            and _col(existing, "source_turn", 5) == kw.get("source_turn")
        )
        if _gate_refuses(existing_method, kw, gate_cfg, may_overrule):
            logger.info(
                "[belief_mutate] blocked_supersede predicate=%s subject=%s "
                "existing_source=%s incoming_source=%s — user fact stands",
                pred, sub, existing_method, kw["extraction_method"],
            )
            _write_audit(
                ops_conn, tenant_id=tenant_id, event_type="blocked_supersede",
                target_id=superseded_id, actor="source_trust_gate",
                reason=(f"lower-trust ({kw['extraction_method']}) cannot "
                        f"supersede {existing_method} {pred}"),
                state_before={"id": superseded_id, "object": old_obj},
                state_after={"id": superseded_id, "object": old_obj, "blocked": True},
            )
            if began:
                try:
                    conn.rollback()
                except Exception:
                    pass
            return {"action": "noop", "belief_id": superseded_id,
                    "superseded_id": None, "blocked": "lower_trust_source"}
        state_before = {"id": superseded_id, "object": old_obj}
        # A fact the user stated, replaced from another turn: an update the
        # user sees and can undo (memory/changes.py, F1).
        from kazma_core.memory.provenance import USER_SOURCES

        users_fact_replaced = (audit_event_type == "supersede" and existing_method in USER_SOURCES
                               and not same_turn)
        from kazma_core.memory.hygiene import beliefs_write

        beliefs_write(
            conn,
            "UPDATE beliefs SET valid_until=?, invalidated_at=? WHERE id=?",
            (now, kw.get("recorded") or now, superseded_id),
        )
        for row in held_list:
            beliefs_write(conn, "UPDATE beliefs SET valid_until=?, invalidated_at=? WHERE id=? AND tenant_id=?",
                          (now, kw.get("recorded") or now, str(_col(row, "id", 0)), tenant_id))
        # The superseded row's graph edge and mirror tombstone (M-04) go
        # after the commit, in mutate_belief: a Neo4j or Postgres round trip
        # here held memory_state.db's write lock (Memory audit M14).
    bid = _belief_id(tenant_id, sub, pred, now)
    try:
        state_after = _insert_belief(
            conn, bid, tenant_id, sub, pred, "functional", obj,
            supersedes_id=superseded_id, now=now, **_insert_kw(kw),
        )
    except sqlite3.IntegrityError:
        logger.warning(
            "[belief_mutate] insert ignored after supersede close — rolling back "
            "subject=%s pred=%s", sub, pred,
        )
        if began:
            try:
                conn.rollback()
            except Exception:
                pass
        return {
            "action": "noop",
            "belief_id": superseded_id or "",
            "superseded_id": None,
            "blocked": "insert_ignored",
        }
    bid = str(state_after.get("id") or bid)
    _note_in_change_log(conn, kw, tenant_id=tenant_id, sub=sub, pred=pred, obj=obj, bid=bid, now=now,
                        replaced=(str(superseded_id), state_before) if users_fact_replaced else None,
                        also_closed=[str(_col(r, "id", 0)) for r in held_list] if users_fact_replaced else [])
    conn.commit()
    _write_audit(
        ops_conn, tenant_id=tenant_id, event_type=audit_event_type,
        target_id=bid, actor="post_turn_worker",
        reason=f"functional {sub} {pred} -> {obj}",
        state_before=state_before, state_after=state_after,
    )
    return {"action": "supersede", "belief_id": bid, "superseded_id": superseded_id,
            "superseded_object": str(state_before["object"] or "") if state_before else None}


def _gate_refuses(existing_method: Any, kw: dict[str, Any], gate_cfg: dict[str, Any], may_overrule: Any) -> bool:
    """Whether the trust gate keeps the existing fact: a lower source may not
    overrule it (``provenance.may_overrule``), and a write that may not
    replace the user's fact (``replaces_users_fact`` False: a memory tool's)
    never replaces one the user stated."""
    from kazma_core.memory.provenance import USER_SOURCES

    if (bool(gate_cfg.get("functional_supersede_requires_user_assert", True))
            and not may_overrule(existing_method, kw["extraction_method"])):
        return True
    return not kw.get("replaces_users_fact", True) and existing_method in USER_SOURCES


def _note_in_change_log(
    conn: sqlite3.Connection, kw: dict[str, Any], *, tenant_id: str, sub: str, pred: str, obj: str, bid: str,
    now: float, replaced: tuple[str, dict[str, Any] | None] | None, also_closed: list[str] | None = None,
) -> None:
    """In the write's own transaction (``memory/changes.py``): the user's fact
    *replaced* (``(old id, state before)``) is an update the user can undo,
    with the user's other list entries the write closed (*also_closed*); any
    other fact of the user's own answers the changes waiting on it. A write
    that is not the user's records nothing."""
    from kazma_core.memory import changes
    from kazma_core.memory.provenance import USER_SOURCES

    if replaced is None and kw["extraction_method"] not in USER_SOURCES:
        return
    try:
        if replaced is not None:
            old_id, before = replaced
            changes.record_update(
                conn, tenant_id=tenant_id, subject=sub, predicate=pred, old_belief_id=old_id,
                old_value=str(before["object"] if before else ""), new_belief_id=bid, new_value=obj,
                session=kw.get("source_session"), turn=kw.get("source_turn"), now=now,
                confirms=kw.get("confirms_change"), also_closed=also_closed,
            )
        else:
            changes.answer_pending(conn, tenant_id=tenant_id, subject=sub, predicate=pred, now=now,
                                   confirms=kw.get("confirms_change"))
    except sqlite3.OperationalError as exc:
        if "memory_changes" not in str(exc):
            raise
        # A store opened without the current schema: the write stands,
        # unrecorded (ensure_primary_schema creates the table).
        logger.warning("[belief_mutate] no change log in this store; %s %s written unrecorded", sub, pred)


def _col(row: Any, name: str, index: int) -> Any:
    """A column of a row read with or without ``sqlite3.Row``."""
    return row[name] if isinstance(row, sqlite3.Row) else row[index]


def _users_list_entry(pred: str, row: Any) -> bool:
    """Whether *row* is an entry of the user's own list on a name a person
    holds several of (``_LISTABLE``)."""
    from kazma_core.memory.provenance import USER_SOURCES

    return (pred in _LISTABLE and _col(row, "predicate_type", 6) == "set"
            and _col(row, "extraction_method", 2) in USER_SOURCES)


def _record_earlier_statement(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection | None,
    sub: str, pred: str, obj: str, *, began: bool, **kw: Any,
) -> dict[str, Any]:
    """Record a statement made before the current fact, as history.

    The values of a single-valued (subject, predicate) form a timeline
    ordered by when each was stated. A statement that arrives late -- an old
    turn reconciled from the chat store, a slow extraction -- is placed where
    it belongs: it holds until the next statement and cuts the one before it
    short. The current fact is never touched, so writing "I live in Paris"
    (said in March) after "I moved to London" (said in May) leaves London
    current; until 2026-09-27 the later write won (plan W1).

    Nothing is recorded when the statement adds nothing -- the value before
    it or after it is the same -- or when it would cut short a user-stated
    value with a lower-trust source (the trust gate of the supersede path).
    """
    tenant_id, now = kw["tenant_id"], kw["now"]

    def _nothing(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        if began:
            with contextlib.suppress(sqlite3.Error):
                conn.rollback()
        return {"action": "noop", "belief_id": "", "superseded_id": None, **(extra or {})}

    rows = conn.execute(
        """SELECT id, object, extraction_method, valid_from, valid_until, source_session, source_turn
           FROM beliefs
           WHERE subject=? AND predicate=? AND tenant_id=?
           ORDER BY valid_from, ingested_at""",
        (sub, pred, tenant_id),
    ).fetchall()
    before = [r for r in rows if float(_col(r, "valid_from", 3)) <= now]
    after = [r for r in rows if float(_col(r, "valid_from", 3)) > now]
    prev = before[-1] if before else None
    nxt = after[0]  # the current fact at the latest
    if prev is not None and _col(prev, "object", 1) == obj:
        return _nothing({"belief_id": _col(prev, "id", 0), "already": "recorded"})
    if _col(nxt, "object", 1) == obj:
        return _nothing({"belief_id": _col(nxt, "id", 0), "already": "stated_later"})
    from kazma_core.memory.provenance import may_overrule

    gate_cfg = ((kw.get("cfg") or {}).get("v2") or {})
    if (prev is not None
            and bool(gate_cfg.get("functional_supersede_requires_user_assert", True))
            and not may_overrule(_col(prev, "extraction_method", 2), kw["extraction_method"])):
        logger.info(
            "[belief_mutate] blocked_history predicate=%s subject=%s -- a %s statement "
            "cannot cut short a %s one", pred, sub, kw["extraction_method"],
            _col(prev, "extraction_method", 2),
        )
        return _nothing({"blocked": "lower_trust_source"})
    from kazma_core.memory.hygiene import beliefs_write

    prev_id = str(_col(prev, "id", 0)) if prev is not None else None
    next_id = str(_col(nxt, "id", 0))
    ends = float(_col(nxt, "valid_from", 3))
    bid = _belief_id(tenant_id, sub, pred, now)
    try:
        state_after = _insert_belief(
            conn, bid, tenant_id, sub, pred, "functional", obj,
            supersedes_id=prev_id, now=now, **_insert_kw(kw),
        )
    except sqlite3.IntegrityError:
        return _nothing({"blocked": "insert_ignored"})
    bid = str(state_after.get("id") or bid)
    beliefs_write(conn, "UPDATE beliefs SET valid_until=?, invalidated_at=? WHERE id=?",
                  (ends, kw.get("recorded") or now, bid))
    if prev is not None:
        prev_until = _col(prev, "valid_until", 4)
        if prev_until is None or float(prev_until) > now:
            beliefs_write(conn, "UPDATE beliefs SET valid_until=?, invalidated_at=COALESCE("
                          "invalidated_at, ?) WHERE id=?", (now, kw.get("recorded") or now, prev_id))
    beliefs_write(conn, "UPDATE beliefs SET supersedes_id=? WHERE id=?", (bid, next_id))
    conn.commit()
    _write_audit(
        ops_conn, tenant_id=tenant_id, event_type="history",
        target_id=bid, actor="post_turn_worker",
        reason=f"earlier statement {sub} {pred} -> {obj}, before {next_id}",
        state_after={**state_after, "valid_until": ends},
    )
    return {"action": "history", "belief_id": bid, "superseded_id": None,
            "touched": [i for i in (prev_id, next_id) if i]}


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


#: How many of a name's newest entries a write compares with its value folded
#: (spelling, case, Arabic letter forms); the same spelling is found among all
#: of them by the database. Folding every entry of a name with 5,000 held the
#: mutation lock for 52 ms a write (2026-11-24).
_FOLDED_ENTRIES = 300


def _entry_told_again(conn: sqlite3.Connection, sub: str, pred: str, obj: str, tenant_id: str,
                      corrects: str | None) -> tuple[str | None, bool]:
    """``(the list entry this write tells again, whether its day is
    corrected)``, or ``(None, False)``: the write is an entry of its own.

    The same words are the same entry -- an event's however they are spelled
    ("The pottery exhibition was Saturday", :func:`_same_value`) -- but an
    event told with another day is another occasion ("visited the dentist" in
    May and in November: the second was dropped until 2026-11-24). An entry
    with no day takes this one's only when it can have happened by the time
    it was first told.
    *corrects* names the entry the turn says it tells again (its number, the
    same value): another day is then a correction of it, under the same
    rule."""
    from kazma_core.memory import fact_days

    day = fact_days.event_day(_written_day(gained=False))
    current = ("SELECT id, object, valid_from, metadata_json FROM beliefs WHERE subject=? AND predicate=? "
               "AND tenant_id=? AND valid_until IS NULL AND invalidated_at IS NULL ")
    rows = conn.execute(current + "AND (object = ? OR id = ?) ORDER BY valid_from DESC",
                        (sub, pred, tenant_id, obj, corrects or "")).fetchall()
    found = {str(_col(r, "id", 0)) for r in rows}
    rows += [r for r in conn.execute(current + "ORDER BY valid_from DESC LIMIT ?",
                                     (sub, pred, tenant_id, _FOLDED_ENTRIES)).fetchall()
             if str(_col(r, "id", 0)) not in found]
    rows.sort(key=lambda r: float(_col(r, "valid_from", 2) or 0.0), reverse=True)

    def same_words(r: Any) -> bool:
        if _col(r, "object", 1) == obj:
            return True
        event = day or _col(r, "id", 0) == corrects or fact_days.event_day(
            fact_days.metadata_of(_col(r, "metadata_json", 3)))
        return bool(event) and _same_value(_col(r, "object", 1), obj)

    rows = [r for r in rows if same_words(r)]
    if not rows:
        return None, False
    named = next((str(_col(r, "id", 0)) for r in rows if str(_col(r, "id", 0)) == corrects), None)
    if not day:
        return named or str(_col(rows[0], "id", 0)), False
    held = {str(_col(r, "id", 0)): (fact_days.metadata_of(_col(r, "metadata_json", 3)), _col(r, "valid_from", 2))
            for r in rows}

    def may_be(bid: str) -> bool:
        meta, told_at = held[bid]
        return bool(meta.get("planned")) or fact_days.could_have_happened_by(day, told_at)

    for bid in [named] if named else list(held):
        if fact_days.compatible(fact_days.any_day(held[bid][0]), day):
            return bid, False
    if named and fact_days.event_day(held[named][0]) and may_be(named):
        return named, True
    for bid in held:
        if not fact_days.has_day(held[bid][0]) and may_be(bid):
            return bid, False
    return None, False


def _mutate_set(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection | None,
    sub: str, pred: str, obj: str, **kw: Any,
) -> dict[str, Any]:
    """Multi-valued: append without invalidating existing entries."""
    tenant_id = kw["tenant_id"]
    now = kw["now"]
    # Skip if this exact (subject, predicate, object) already exists & active
    existing_id, correct = _entry_told_again(conn, sub, pred, obj, tenant_id, kw.get("corrects"))
    if existing_id is not None:
        # The same entry told again with the day it happened, or the start
        # it names, gains it (W4: "I went to the pottery exhibition" then
        # "it was last Saturday"), as a single value does.
        gained = _add_named_start(conn, existing_id, tenant_id, began=not conn.in_transaction, correct=correct)
        if gained:
            return gained
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
    try:
        state_after = _insert_belief(
            conn, bid, tenant_id, sub, pred, "set", obj, now=now, **_insert_kw(kw),
        )
    except sqlite3.IntegrityError:
        logger.warning(
            "[belief_mutate] set-append insert ignored subject=%s pred=%s", sub, pred
        )
        return {"action": "noop", "belief_id": "", "superseded_id": None, "blocked": "insert_ignored"}
    bid = str(state_after.get("id") or bid)
    conn.commit()
    _write_audit(
        ops_conn, tenant_id=tenant_id, event_type="append",
        target_id=bid, actor="post_turn_worker",
        reason=f"set {sub} {pred} += {obj}",
        state_after=state_after,
    )
    return {"action": "append", "belief_id": bid, "superseded_id": None}
