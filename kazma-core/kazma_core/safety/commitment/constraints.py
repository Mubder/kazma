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

__all__ = ["is_commitment_enabled", "load_constraint_beliefs", "cron_pending_jobs"]


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


def cron_pending_jobs(
    thread_id: str | None = None, tenant_id: str = "default",
) -> list[dict[str, str]] | None:
    """Sync read of PENDING cron jobs for the cancel_job resolver (plan §3.5).

    The cron store API is async-only and the resolver runs in the sync
    ``authorize_effect``; this mirrors ``load_constraint_beliefs``' direct-sqlite
    pattern. Only ``status='pending'`` jobs are cancellable
    (``SQLiteCronStore.cancel`` updates ``WHERE status='pending'``), so that's
    what we return — counting ``running`` would false-match jobs that can't be
    cancelled.

    Returns ``[{"job_id": ..., "prompt": ...}, ...]`` for pending jobs on the
    given thread; **``[]`` means "checked, none"; ``None`` means "couldn't
    check"** (no scheduler / DB error) so the resolver can degrade to audit-only
    rather than over-blocking every cancel when verification is unavailable.
    """
    try:
        from kazma_core.cron.scheduler import get_cron_scheduler
        from kazma_core.paths import data_dir

        sched = get_cron_scheduler()
        if sched is None:
            return None  # can't verify → caller degrades to audit-only
        # Resolve the exact DB file the store writes (privately held on the
        # store). Fall back to data_dir/cron.db (matches the common case).
        db_path = getattr(getattr(sched, "_store", None), "_db_path", None)
        if not db_path:
            db_path = str(data_dir() / "cron.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            if thread_id:
                rows = conn.execute(
                    "SELECT job_id, prompt FROM cron_jobs "
                    "WHERE status='pending' AND thread_id=? AND tenant_id=?",
                    (thread_id, tenant_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT job_id, prompt FROM cron_jobs "
                    "WHERE status='pending' AND tenant_id=?",
                    (tenant_id,),
                ).fetchall()
        finally:
            conn.close()
        return [{"job_id": r[0], "prompt": r[1] or ""} for r in rows]
    except Exception:
        logger.debug("[commitment] cron_pending_jobs failed — degrading to None", exc_info=True)
        return None
