"""Procedural memory — parametric action DAGs with Laplace-smoothed confidence.

Records recurring multi-step tool execution trajectories as reusable
"skills". Each procedural DAG has preconditions, parametric steps, and
postconditions. Confidence is Laplace-smoothed: ``C(d) = (S+1)/(N+2)``
to avoid cold-start penalties and lucky initial wins.

A DAG is demoted to ``quarantine`` status when ``C(d) < threshold``
after ``N >= min_trials`` attempts, signaling it needs repair or re-synthesis.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "laplace_confidence",
    "record_procedural_outcome",
    "should_quarantine",
]


def laplace_confidence(successes: int, trials: int) -> float:
    """C(d) = (S+1)/(N+2) — Laplace-smoothed skill reliability.

    Avoids the 0/1 cold-start cliff (a single failure shouldn't doom a
    skill, a single success shouldn't crown it). Starts at 0.5 for an
    untried skill.
    """
    if trials < 0:
        trials = 0
    if successes < 0:
        successes = 0
    if successes > trials:
        successes = trials
    return (successes + 1) / (trials + 2)


def should_quarantine(
    confidence: float,
    trials: int,
    *,
    cfg: dict[str, Any] | None = None,
) -> bool:
    """True when a DAG should be demoted to quarantine per §4.2."""
    v2 = (cfg or {}).get("v2") or {}
    threshold = float(v2.get("procedural_quarantine_threshold", 0.40))
    min_trials = int(v2.get("procedural_quarantine_min_trials", 3))
    return trials >= min_trials and confidence < threshold


def _signature_hash(preconditions: dict[str, Any]) -> str:
    """Stable hash of the canonical precondition set for DAG matching."""
    canonical = json.dumps(preconditions, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def record_procedural_outcome(
    conn: sqlite3.Connection,
    *,
    name: str,
    description: str,
    preconditions: dict[str, Any],
    dag_steps: list[dict[str, Any]],
    postconditions: dict[str, Any],
    success: bool,
    tenant_id: str = "default",
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a tool-execution outcome against a (possibly new) procedural DAG.

    Creates the DAG if its precondition signature is unseen, else updates
    the existing one's trial/success counts and recomputes confidence.
    Quarantines when the policy threshold is breached.

    Returns::

        {"dag_id": str, "action": "created"|"updated", "confidence": float,
         "quarantined": bool}
    """
    sig = _signature_hash(preconditions)
    now = time.time()
    try:
        existing = conn.execute(
            """SELECT id, success_count, total_trials, confidence_score, status
               FROM procedural_dags
               WHERE tenant_id=? AND precond_signature_hash=? LIMIT 1""",
            (tenant_id, sig),
        ).fetchone()
        if existing:
            dag_id = existing["id"]
            s = int(existing["success_count"]) + (1 if success else 0)
            n = int(existing["total_trials"]) + 1
            conf = laplace_confidence(s, n)
            quarantined = should_quarantine(conf, n, cfg=cfg)
            new_status = "quarantine" if quarantined else (
                "active" if existing["status"] != "retired" else "retired"
            )
            conn.execute(
                """UPDATE procedural_dags
                   SET success_count=?, total_trials=?, confidence_score=?,
                       status=?, last_executed=? WHERE id=?""",
                (s, n, conf, new_status, now, dag_id),
            )
            conn.commit()
            return {
                "dag_id": dag_id, "action": "updated",
                "confidence": conf, "quarantined": quarantined,
            }
        # Create new DAG
        dag_id = "dag_" + uuid.uuid4().hex[:20]
        s = 1 if success else 0
        n = 1
        conf = laplace_confidence(s, n)
        conn.execute(
            """INSERT INTO procedural_dags
               (id, tenant_id, name, description, precond_signature_hash,
                preconditions_json, dag_steps_json, postconditions_json,
                success_count, total_trials, confidence_score, status,
                created_at, last_executed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                dag_id, tenant_id, name, description, sig,
                json.dumps(preconditions, ensure_ascii=False),
                json.dumps(dag_steps, ensure_ascii=False),
                json.dumps(postconditions, ensure_ascii=False),
                s, n, conf, now, now,
            ),
        )
        conn.commit()
        return {
            "dag_id": dag_id, "action": "created",
            "confidence": conf, "quarantined": False,
        }
    except Exception:
        logger.debug("[procedural] record failed", exc_info=True)
        return {"dag_id": "", "action": "noop", "confidence": 0.0, "quarantined": False}
