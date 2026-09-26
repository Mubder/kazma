"""Macro-consolidation "sleep cycle" — tier lifecycle and archive.

Runs as a ``macro_sleep`` task on the durable queue during idle periods.
Every move is a rule on TTLs, importance and use:

  - **Working → episodic** — the active buffer drains after
    ``working_ttl_hours``.
  - **Episodic → recall** — important (``promote_to_recall_min_importance``)
    and used (``promote_to_recall_min_access``) episodes are promoted.
  - **Recall → episodic** — recall rows idle for ``recall_demote_idle_days``.
  - **Archive** — the memory moves to the cold ``archived`` tier and keeps
    its text and its vector (``_ARCHIVE_EPISODE_SQL``). Recall still reaches
    it, one step behind active memories, and a recalled archived memory
    returns to ``episodic`` at the next sweep. Only rows stale on BOTH clocks
    are archived — created past the TTL *and* not recalled within it — and
    below the promote floor. Until 2026-09-26 archiving DELETED the text and
    recall never searched the tier: a month of disuse lost the memory.
  - **Archive beliefs** — superseded beliefs older than
    ``archive_after_days`` move to ``beliefs_archive`` cold storage.

There used to be a V_retention decay score (``compute_retention``) here that
no rule consulted. Its decay rates were applied per SECOND — the usage term
of a "general" memory halved every ~70 s — so it could not have ranked by use
either; it and its Settings knobs were removed on 2026-09-23. Graded decay,
if it comes back, needs corrected units and a dry run on real data first.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["run_macro_sleep"]

# Archiving moves the tier and nothing else of substance: the question, the
# answer and the vector all stay. An empty summary is filled with the start of
# the question and of the answer so the row has a one-line gist in lists.
# (Until 2026-09-26 this statement also set user_text and assistant_text to
# NULL; memory/rehydrate.py recovers those rows from the chat history.)
_ARCHIVE_EPISODE_SQL = (
    "UPDATE episodes SET tier='archived', "
    "summary_text=COALESCE(NULLIF(TRIM(summary_text), ''), TRIM("
    "SUBSTR(COALESCE(user_text, ''), 1, 200) || "
    "CASE WHEN TRIM(COALESCE(user_text, '')) <> '' "
    "AND TRIM(COALESCE(assistant_text, '')) <> '' THEN ' — ' ELSE '' END || "
    "SUBSTR(COALESCE(assistant_text, ''), 1, 300))) "
    "WHERE id=?"
)

def _propagate_episode_moves(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    moved: list[str],
    archived: list[str],
) -> None:
    """Carry this sweep's tier moves to the optional shared mirrors.

    SQLite is the source of truth, but the Postgres state mirror and a remote
    vector index (pgvector / Qdrant) each hold their own copy, and both must
    learn the new tier. Both mirrors are best-effort and swallow their own
    failures; the default local setup has neither, and this returns without
    touching anything.
    """
    if not moved and not archived:
        return
    from kazma_core.memory.state_backend import (
        NullStateBackend,
        get_state_backend,
        remirror_episode_by_id,
    )

    if not isinstance(get_state_backend(), NullStateBackend):
        for eid in dict.fromkeys([*moved, *archived]):
            remirror_episode_by_id(conn, eid)

    if not archived:
        return
    from kazma_core.memory.backends import LocalSqliteVectorBackend, get_vector_backend

    try:
        backend = get_vector_backend(conn)
    except RuntimeError:  # failover=raise while the remote index is down
        logger.warning(
            "[macro_sleep] %d archived episode(s) left in the remote vector index "
            "(backend unavailable, failover=raise)",
            len(archived),
        )
        return
    # Archived memories stay searchable by meaning. The local index is the row
    # itself, so it keeps the vector with no work; a remote index is re-tagged
    # with the new tier. (Until 2026-09-26 it was told to DELETE archived rows,
    # which made them unreachable by meaning on every remote-index install.)
    if isinstance(backend, LocalSqliteVectorBackend):
        return
    import struct

    for eid in archived:
        row = conn.execute(
            "SELECT embedding, session_id FROM episodes WHERE id=?", (eid,)
        ).fetchone()
        blob = bytes(row[0]) if row is not None and row[0] else b""
        if not blob or len(blob) % 4:
            continue  # no vector yet: the repair sweep re-encodes and upserts it
        vec = list(struct.unpack(f"{len(blob) // 4}f", blob))
        # A backend reports a failed write by returning False.
        if not backend.upsert(
            eid,
            vec,
            tenant_id=tenant_id,
            meta={"kind": "episode", "tier": "archived", "session_id": row[1]},
        ):
            logger.warning("[macro_sleep] remote index kept %s under its old tier", eid)


def run_macro_sleep(
    primary_conn: sqlite3.Connection,
    *,
    cfg: dict[str, Any] | None = None,
    tenant_id: str = "default",
    now: float | None = None,
) -> dict[str, Any]:
    """Execute one macro-consolidation sweep.

    Returns a stats dict: ``{demoted_recall, demoted_episodic,
    archived_beliefs, scored_episodes}``.
    """
    now = now if now is not None else time.time()
    v2 = (cfg or {}).get("v2") or {}
    recall_idle = float(v2.get("recall_demote_idle_days", 30)) * 86400
    episodic_ttl = float(v2.get("episodic_ttl_days", 30)) * 86400
    # Age-based recall archival (audit M2): the key existed in config but was
    # never enforced — recall-tier rows previously lived forever unless they
    # went idle. 0 disables (default keeps backward-compatible behavior).
    recall_ttl = float(v2.get("recall_ttl_days", 90)) * 86400
    archive_after = float(v2.get("archive_after_days", 180)) * 86400
    promote_min_importance = int(v2.get("promote_to_recall_min_importance", 3))
    promote_min_access = int(v2.get("promote_to_recall_min_access", 2))

    working_ttl = float(v2.get("working_ttl_hours", 24)) * 3600
    stats = {
        "demoted_recall": 0,
        "demoted_episodic": 0,
        "demoted_working": 0,
        "archived_beliefs": 0,
        "scored_episodes": 0,
        "promoted_to_recall": 0,
        "revived_archived": 0,
    }
    try:
        # ── Episode decay + tier transitions ──
        rows = primary_conn.execute(
            """SELECT id, tier, structural_importance, access_count,
                      COALESCE(last_accessed, created_at) AS last_touch,
                      created_at
               FROM episodes WHERE tenant_id=?""",
            (tenant_id,),
        ).fetchall()
        # Batched writes (audit L-22): transitions are collected per bucket
        # and applied with executemany after the scan — one round trip per
        # transition class instead of one per row (a multi-year tenant used
        # to pay N executes inside one transaction every 6h sweep).
        _to_episodic: list[str] = []
        _to_recall: list[str] = []
        _archive_recall: list[str] = []
        _idle_to_episodic: list[str] = []
        _archive_episodic: list[str] = []
        _revive: list[str] = []
        for r in rows:
            eid = r["id"]
            tier = r["tier"]
            importance = int(r["structural_importance"])
            access = int(r["access_count"])
            last_touch = float(r["last_touch"] or now)
            age = max(0.0, now - last_touch)
            created_age = max(0.0, now - float(r["created_at"] or now))
            stats["scored_episodes"] += 1

            # A recalled archived memory comes back: it was archived because
            # nobody used it, and recall bumps last_accessed, so a fresh touch
            # means it is in use again.
            if tier == "archived" and age < episodic_ttl:
                _revive.append(eid)
                stats["revived_archived"] += 1
            # Working-tier TTL → episodic (active buffer must not grow forever)
            elif tier == "working" and created_age > working_ttl:
                _to_episodic.append(eid)
                stats["demoted_working"] += 1
            # Promote episodic→recall when important + accessed
            elif tier == "episodic" and importance >= promote_min_importance and access >= promote_min_access:
                _to_recall.append(eid)
                stats["promoted_to_recall"] += 1
            # Demote recall→archived by pure age (recall_ttl_days) — bounds
            # long-term recall growth even for frequently-idle items that
            # never trip the idle demotion. Audit H18: this previously keyed
            # on created_at alone, nulling the text of high-importance,
            # recently-accessed memories mid-life. Archive only rows stale
            # on BOTH clocks — created past ttl AND untouched past ttl
            # (age = now − last_touch) — AND below the promote floor (same
            # floor as the episodic branch below): actively-used or
            # important memories survive.
            elif (
                tier == "recall"
                and recall_ttl > 0
                and created_age > recall_ttl
                and age > recall_ttl
                and importance < promote_min_importance
            ):
                _archive_recall.append(eid)
                stats["demoted_recall"] += 1
            # Demote recall→episodic when idle past the threshold
            elif tier == "recall" and age > recall_idle:
                _idle_to_episodic.append(eid)
                stats["demoted_recall"] += 1
            # Demote episodic→archived: past the TTL on BOTH clocks and below
            # the promote floor. This used to test created_at only. Every
            # ordinary chat turn is written at importance 1, so it can never
            # be promoted, and a turn recalled daily still lost its text on
            # day 30. Recall bumps last_accessed, so `age` (now − last
            # touch) keeps an in-use memory out of the archive. Same rule
            # as the recall branch above (audit H18).
            elif (
                tier == "episodic"
                and created_age > episodic_ttl
                and age > episodic_ttl
                and importance < promote_min_importance
            ):
                _archive_episodic.append(eid)
                stats["demoted_episodic"] += 1

        if _to_episodic or _idle_to_episodic or _revive:
            primary_conn.executemany(
                "UPDATE episodes SET tier='episodic' WHERE id=?",
                [(eid,) for eid in _to_episodic + _idle_to_episodic + _revive],
            )
        if _to_recall:
            primary_conn.executemany(
                "UPDATE episodes SET tier='recall' WHERE id=?",
                [(eid,) for eid in _to_recall],
            )
        if _archive_recall:
            primary_conn.executemany(_ARCHIVE_EPISODE_SQL, [(eid,) for eid in _archive_recall])
        if _archive_episodic:
            primary_conn.executemany(_ARCHIVE_EPISODE_SQL, [(eid,) for eid in _archive_episodic])

        # ── Archive old superseded beliefs ──
        old_superseded = primary_conn.execute(
            """SELECT id, tenant_id, subject, predicate, object, confidence,
                      structural_importance, source_trust_weight, valid_from,
                      valid_until, ingested_at, invalidated_at, supersedes_id,
                      source_session, source_turn, extraction_method,
                      embedding_model_version, metadata_json
               FROM beliefs
               WHERE tenant_id=? AND valid_until IS NOT NULL
                 AND valid_until < ?""",
            (tenant_id, now - archive_after),
        ).fetchall()
        for b in old_superseded:
            bid = b["id"]
            blob = json.dumps(
                {k: b[k] for k in b.keys() if k != "id"}, ensure_ascii=False, default=str
            )
            primary_conn.execute(
                """INSERT OR IGNORE INTO beliefs_archive (id, tenant_id, original_belief_json, archived_at)
                   VALUES (?, ?, ?, ?)""",
                (bid, tenant_id, blob, now),
            )
            primary_conn.execute("DELETE FROM beliefs WHERE id=?", (bid,))
            stats["archived_beliefs"] += 1
            # Mirror hard-delete (M-04): the row left the SQLite SoT — the
            # mirror must not keep serving it live.
            try:
                from kazma_core.memory.state_backend import unmirror_belief_to_state

                unmirror_belief_to_state(bid)
            except Exception:
                logger.debug("[macro_sleep] unmirror skipped for %s", bid, exc_info=True)

        primary_conn.commit()
        _propagate_episode_moves(
            primary_conn,
            tenant_id=tenant_id,
            moved=_to_episodic + _idle_to_episodic + _to_recall + _revive,
            archived=_archive_recall + _archive_episodic,
        )
    except Exception:
        # A broken sweep (schema drift, corrupt row, locked DB) previously
        # logged at DEBUG and the caller still reported success — macro_sleep
        # could be effectively a no-op for weeks with no signal (the "silent
        # amnesia" pattern). Surface it at WARNING + flag the stat.
        logger.warning("[macro_sleep] sweep failed", exc_info=True)
        stats["sweep_error"] = True
    return stats
