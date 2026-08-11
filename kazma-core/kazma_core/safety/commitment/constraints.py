"""Commitment gate ↔ memory bridge + kill-switch (Commitment Layer §3.6 / §2.3).

``load_constraint_beliefs`` is the gate's read path into the belief store — the
"machine-readable appendix" of structured constraints (plan §3.6). It returns
the active FUNCTIONAL beliefs (current-truth facts: dates, subscriptions,
identity) the gate checks against. This is a small, targeted set; G1 measured
the scan at sub-millisecond. ``is_commitment_enabled`` is the live kill-switch.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["is_commitment_enabled", "load_constraint_beliefs"]


def is_commitment_enabled() -> bool:
    """Kill-switch (plan §2.3 #10). Delegates to get_commitment_config() so
    there is ONE config reader for the layer. Default ON."""
    try:
        from kazma_core.safety.commitment.config import get_commitment_config

        return bool(get_commitment_config()["enabled"])
    except Exception:
        return True


def load_constraint_beliefs(tenant_id: str = "default", *, limit: int = 50) -> list[dict[str, Any]]:
    """Active functional beliefs (current-truth facts) for the gate to check.

    Returns ``[{"predicate": ..., "object": ...}, ...]`` — the small set of
    single-valued facts (next_reset dates, subscriptions, identity) the remind
    resolver anchors against. Best-effort: any failure returns ``[]`` so the
    gate degrades to from-now resolution rather than erroring the turn.
    """
    try:
        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT predicate, object FROM beliefs "
                "WHERE predicate_type='functional' "
                "AND valid_until IS NULL AND invalidated_at IS NULL "
                "AND tenant_id=? LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        finally:
            conn.close()
        return [{"predicate": r["predicate"], "object": r["object"]} for r in rows]
    except Exception:
        logger.debug("[commitment] load_constraint_beliefs failed — degrading to []", exc_info=True)
        return []
