"""Macro-consolidation "sleep cycle" — decay, tier demotion, compaction, archive.

Runs as a ``macro_sleep`` task on the durable queue during idle periods.
Implements the V_retention scoring (§4.1) and the tier lifecycle:

  - **Decay** — compute V_retention per episode; flag low-retention rows.
  - **Demote recall→episodic** — recall rows with no recent access for
    ``recall_demote_idle_days`` drop to episodic.
  - **Demote episodic→archived** — episodic rows past ``episodic_ttl_days``
    with low importance are archived (raw text dropped, summary kept).
  - **Archive beliefs** — superseded beliefs older than
    ``archive_after_days`` move to ``beliefs_archive`` cold storage.

The decay formula (§4.1):

    V_retention(m) = W_trust(m) * [ω1 * I(m) + ω2 * ln(1 + A_m) * exp(-λ * Δt)]

where ``λ`` is selected by ``memory_class`` (resolution #4).
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["compute_retention", "run_macro_sleep"]


def compute_retention(
    *,
    trust_weight: float,
    importance: int,
    access_count: int,
    age_seconds: float,
    memory_class: str,
    cfg: dict[str, Any] | None = None,
) -> float:
    """V_retention per §4.1.

    Args:
        trust_weight: Source trust (user=1.0, tool=0.85, llm=0.60).
        importance: 1..5 structural importance.
        access_count: Lifetime access frequency.
        age_seconds: Time since last access (or creation if never accessed).
        memory_class: 'identity' | 'general' | 'ephemeral' (resolution #4).
        cfg: V2 config block for the weights/lambdas.
    """
    v2 = (cfg or {}).get("v2") or {}
    omega1 = float(v2.get("retention_importance_weight", 0.60))
    omega2 = float(v2.get("retention_access_weight", 0.40))
    lambdas = {
        "identity": float(v2.get("decay_lambda_identity", 0.0001)),
        "general": float(v2.get("decay_lambda_general", 0.01)),
        "ephemeral": float(v2.get("decay_lambda_ephemeral", 0.10)),
    }
    lam = lambdas.get(memory_class, lambdas["general"])
    decay = math.exp(-lam * age_seconds)
    access_term = math.log(1 + max(0, access_count)) * decay
    return trust_weight * (omega1 * importance + omega2 * access_term)


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
        for r in rows:
            eid = r["id"]
            tier = r["tier"]
            importance = int(r["structural_importance"])
            access = int(r["access_count"])
            last_touch = float(r["last_touch"] or now)
            age = max(0.0, now - last_touch)
            created_age = max(0.0, now - float(r["created_at"] or now))
            # Derive memory_class from importance (episodes have no predicate_type)
            mem_class = (
                "identity" if importance >= int(v2.get("identity_min_importance", 4))
                else "ephemeral" if importance <= int(v2.get("ephemeral_max_importance", 2))
                else "general"
            )
            ret = compute_retention(
                trust_weight=1.0, importance=importance,
                access_count=access, age_seconds=age,
                memory_class=mem_class, cfg=cfg,
            )
            stats["scored_episodes"] += 1

            # Working-tier TTL → episodic (active buffer must not grow forever)
            if tier == "working" and created_age > working_ttl:
                primary_conn.execute(
                    "UPDATE episodes SET tier='episodic' WHERE id=?", (eid,)
                )
                stats["demoted_working"] += 1
            # Promote episodic→recall when important + accessed
            elif tier == "episodic" and importance >= promote_min_importance and access >= promote_min_access:
                primary_conn.execute(
                    "UPDATE episodes SET tier='recall' WHERE id=?", (eid,)
                )
                stats["promoted_to_recall"] += 1
            # Demote recall→archived by pure age (recall_ttl_days) — bounds
            # long-term recall growth even for frequently-idle items that
            # never trip the idle demotion.
            elif tier == "recall" and recall_ttl > 0 and created_age > recall_ttl:
                primary_conn.execute(
                    "UPDATE episodes SET tier='archived', "
                    "summary_text=COALESCE(summary_text, SUBSTR(user_text, 1, 200)), "
                    "user_text=NULL, assistant_text=NULL WHERE id=?",
                    (eid,),
                )
                stats["demoted_recall"] += 1
            # Demote recall→episodic when idle past the threshold
            elif tier == "recall" and age > recall_idle:
                primary_conn.execute(
                    "UPDATE episodes SET tier='episodic' WHERE id=?", (eid,)
                )
                stats["demoted_recall"] += 1
            # Demote episodic→archived when past TTL + low importance
            elif tier == "episodic" and (now - float(r["created_at"] or now)) > episodic_ttl and importance < promote_min_importance:
                # Drop raw text, keep summary (or synthesize a stub)
                primary_conn.execute(
                    """UPDATE episodes SET tier='archived',
                       summary_text=COALESCE(summary_text, SUBSTR(user_text, 1, 200)),
                       user_text=NULL, assistant_text=NULL WHERE id=?""",
                    (eid,),
                )
                stats["demoted_episodic"] += 1

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

        primary_conn.commit()
    except Exception:
        # A broken sweep (schema drift, corrupt row, locked DB) previously
        # logged at DEBUG and the caller still reported success — macro_sleep
        # could be effectively a no-op for weeks with no signal (the "silent
        # amnesia" pattern). Surface it at WARNING + flag the stat.
        logger.warning("[macro_sleep] sweep failed", exc_info=True)
        stats["sweep_error"] = True
    return stats
