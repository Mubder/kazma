"""Registration of V2 memory task-queue handlers + boot wiring.

Registers handlers for the three task types on the durable queue and
provides :func:`start_memory_worker` to be called at app boot. The
handlers wrap the existing V2 modules so the queue drains real work:

  - ``macro_sleep``     → :func:`run_macro_sleep` (decay, demote, archive)
  - ``entity_merge``    → resolve a pending high-stakes merge candidate
  - ``micro_consolidation`` → re-extract beliefs from a stored episode

All handlers return ``True`` on success, ``False`` on failure (the queue
then retries up to ``max_attempts``).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "register_v2_handlers",
    "start_memory_worker",
    "register_backup_export_handlers",
]

_registered = False
# Separate guard so backup/export handlers can be registered independently
# of the core V2 handlers (e.g. by tests) without re-churning either set.
_backup_export_registered = False

# Strong references to the background scheduler loops (macro_sleep / backup /
# commitment-gc / reconsolidation). Without these, CPython may GC a scheduler
# task mid-loop and silently halt a cadence — the exact "scheduler existed but
# nothing ran it" failure mode (audit finding).
_scheduler_tasks: set = set()


def register_v2_handlers() -> None:
    """Register all V2 task handlers on the durable queue (idempotent)."""
    global _registered
    if _registered:
        return
    from kazma_core.memory.task_queue import register_handler

    register_handler("macro_sleep", _handle_macro_sleep)
    register_handler("entity_merge", _handle_entity_merge)
    register_handler("micro_consolidation", _handle_micro_consolidation)
    register_handler("global_reconsolidation", _handle_global_reconsolidation)
    _registered = True
    logger.info("[memory_worker] V2 task handlers registered")


async def _handle_macro_sleep(payload: dict[str, Any]) -> bool:
    """Run one macro-consolidation sweep (decay + tier transitions + archive)."""
    try:
        from kazma_core.memory.config import DEFAULT_MEMORY_CFG, read_memory_cfg
        from kazma_core.memory.macro_sleep import run_macro_sleep
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        tenant_id = payload.get("tenant_id", "default")
        cfg = read_memory_cfg() if payload.get("live_config") else DEFAULT_MEMORY_CFG
        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            ensure_primary_schema(conn)
            stats = run_macro_sleep(conn, cfg=cfg, tenant_id=tenant_id)
            logger.info("[memory_worker] macro_sleep done: %s", stats)
            # False when the sweep flagged sweep_error — run_macro_sleep
            # never raises, so returning True unconditionally marked broken
            # sweeps as done and the queue never retried them.
            return not bool(stats.get("sweep_error"))
        finally:
            conn.close()
    except Exception:
        logger.warning("[memory_worker] macro_sleep handler failed", exc_info=True)
        return False


async def _handle_entity_merge(payload: dict[str, Any]) -> bool:
    """Resolve a pending entity merge (approve/reject via configured policy).

    ``entity_merges`` lives on the primary memory DB (not ops). Auto-approves
    tier1_exact always and tier2_vector above 0.85; else leaves pending.
    """
    try:
        merge_id = payload.get("merge_id")
        if not merge_id:
            return False
        from kazma_core.memory.entity_resolution import decide_entity_merge
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            ensure_primary_schema(conn)
            row = conn.execute(
                "SELECT * FROM entity_merges WHERE id=? AND status='pending'",
                (merge_id,),
            ).fetchone()
            if not row:
                return True  # already resolved / gone
            conf = float(row["confidence"])
            tier = row["merge_tier"]
            if tier == "tier1_exact" or (tier == "tier2_vector" and conf >= 0.85):
                result = decide_entity_merge(conn, merge_id, approve=True)
                logger.info(
                    "[memory_worker] auto-approved merge %s (conf=%.2f ok=%s)",
                    merge_id, conf, result.get("ok"),
                )
            else:
                logger.info(
                    "[memory_worker] merge %s left pending (tier=%s conf=%.2f < 0.85)",
                    merge_id, tier, conf,
                )
            return True
        finally:
            conn.close()
    except Exception:
        logger.warning("[memory_worker] entity_merge handler failed", exc_info=True)
        return False


async def _handle_global_reconsolidation(payload: dict[str, Any]) -> bool:
    """Nightly/global re-consolidation (dedupe beliefs + re-embed missing).

    Huge corpora auto-partition by subject hash; remaining shards are
    enqueued as follow-up ``global_reconsolidation`` tasks.
    """
    try:
        from kazma_core.memory.global_reconsolidation import run_global_reconsolidation
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        tenant_id = payload.get("tenant_id", "default")
        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            ensure_primary_schema(conn)
            stats = run_global_reconsolidation(
                conn,
                tenant_id=tenant_id,
                max_merges=int(payload.get("max_merges") or 50),
                reembed_limit=int(payload.get("reembed_limit") or 100),
                partition_index=int(payload.get("partition_index") or 0),
                partition_count=int(payload.get("partition_count") or 1),
                auto_partition=bool(payload.get("auto_partition", True)),
            )
            logger.info("[memory_worker] global_reconsolidation: %s", stats)
            # Chain next subject-hash partition for huge corpora
            if stats.get("has_more") and stats.get("next_partition_index") is not None:
                try:
                    from kazma_core.memory.task_queue import enqueue_task

                    nxt = {
                        "tenant_id": tenant_id,
                        "max_merges": int(payload.get("max_merges") or 50),
                        "reembed_limit": int(payload.get("reembed_limit") or 100),
                        "partition_index": int(stats["next_partition_index"]),
                        "partition_count": int(
                            stats.get("partition_count")
                            or payload.get("partition_count")
                            or 1
                        ),
                        # Follow-up shards must not re-expand the grid
                        "auto_partition": False,
                    }
                    enqueue_task("global_reconsolidation", nxt)
                    logger.info(
                        "[memory_worker] enqueued reconsolidation partition %s/%s",
                        nxt["partition_index"],
                        nxt["partition_count"],
                    )
                except Exception:
                    logger.debug(
                        "[memory_worker] reconsolidation chain enqueue failed",
                        exc_info=True,
                    )
            return True
        finally:
            conn.close()
    except Exception:
        logger.warning("[memory_worker] global_reconsolidation failed", exc_info=True)
        return False


# (Removed) ``_apply_merge`` was dead code (zero callers). It closed a
# caller-owned connection in its finally, did an unscoped cross-tenant belief
# redirect (no tenant_id filter), and hard-deleted the source entity row
# instead of soft-retiring it. The live path is
# entity_resolution.decide_entity_merge. (audit finding)


async def _handle_micro_consolidation(payload: dict[str, Any]) -> bool:
    """Re-extract beliefs from a stored episode (background deep-consolidation)."""
    try:
        episode_id = payload.get("episode_id")
        if not episode_id:
            return False
        from kazma_core.memory.belief_extractor import extract_and_apply_beliefs
        from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
        from kazma_core.paths import memory_ops_db, primary_memory_db

        primary = sqlite3.connect(primary_memory_db(), check_same_thread=False, isolation_level=None)
        primary.row_factory = sqlite3.Row
        ops = sqlite3.connect(memory_ops_db(), check_same_thread=False, isolation_level=None)
        try:
            ensure_primary_schema(primary)
            ensure_ops_schema(ops)
            row = primary.execute(
                "SELECT user_text, assistant_text, session_id, turn_number, tenant_id FROM episodes WHERE id=?",
                (episode_id,),
            ).fetchone()
            if not row:
                return True  # episode gone
            # ── Cost-gate (memory.v2.extraction_every_n_turns /
            #   skip_llm_if_heuristic_extracted) ─────────────────────
            from kazma_core.memory.config import read_memory_cfg

            v2cfg = (read_memory_cfg().get("v2") or {})
            every_n = max(1, int(v2cfg.get("extraction_every_n_turns", 1)))
            turn_n = int(row["turn_number"] or 0)
            # Skip the LLM pass entirely on turns that don't fall on the cadence
            if every_n > 1 and (turn_n % every_n) != 0:
                logger.debug(
                    "[memory_worker] skip LLM extraction for %s (turn %d, every_n=%d)",
                    episode_id, turn_n, every_n,
                )
                return True
            # If the sync heuristic pass already extracted beliefs and the
            # skip flag is set, don't spend another LLM call on this turn.
            skip_if_heur = bool(v2cfg.get("skip_llm_if_heuristic_extracted", False))
            use_llm = True
            if skip_if_heur:
                try:
                    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync

                    sync_stats = extract_and_apply_beliefs_sync(
                        primary, ops,
                        row["user_text"] or "", row["assistant_text"] or "",
                        session_id=row["session_id"], turn=row["turn_number"],
                        tenant_id=row["tenant_id"],
                    )
                    if sync_stats.get("applied", 0) > 0:
                        logger.debug(
                            "[memory_worker] heuristic already extracted %d belief(s) for %s — skipping LLM",
                            sync_stats["applied"], episode_id,
                        )
                        use_llm = False
                except Exception:
                    pass  # fall through to LLM extraction
            stats = await extract_and_apply_beliefs(
                primary, ops,
                row["user_text"] or "", row["assistant_text"] or "",
                session_id=row["session_id"], turn=row["turn_number"],
                tenant_id=row["tenant_id"],
                use_llm=use_llm,
            )
            logger.info(
                "[memory_worker] micro_consolidation of %s: applied=%d (llm=%s)",
                episode_id, stats.get("applied", 0), use_llm,
            )
            return True
        finally:
            primary.close()
            ops.close()
    except Exception:
        logger.warning("[memory_worker] micro_consolidation handler failed", exc_info=True)
        return False


def start_memory_worker() -> None:
    """Register handlers + start the durable worker (call at app boot).

    Also starts two lightweight background schedulers:

      - a ``macro_sleep`` task every 6 hours (decay / tier-transitions /
        archival), and
      - a nightly backup + export sweep every 24 hours (native
        ``sqlite3.backup()`` copies of both memory DBs + JSONL/GraphML
        long-term exports), so recovery artefacts exist without manual
        intervention.
    """
    try:
        register_v2_handlers()
        from kazma_core.memory.task_queue import start_worker

        start_worker()
        _start_macro_sleep_scheduler()
        _start_backup_export_scheduler()
        _start_reconsolidation_scheduler()
        _start_commitment_gc_scheduler()
    except Exception:
        logger.warning("[memory_worker] could not start worker", exc_info=True)


async def stop_memory_worker() -> None:
    """Stop the durable V2 memory worker + drain in-flight handler tasks.

    Call on shutdown so a handler mid-execution (e.g. an LLM belief extraction
    holding a SQLite transaction) is awaited/cancelled rather than abandoned
    when the loop closes. The schedulers are fire-and-forget loops the loop
    cancellation handles. (audit finding: stop_worker existed but was unwired.)
    """
    try:
        from kazma_core.memory.task_queue import stop_worker

        await stop_worker()
    except Exception:
        logger.debug("[memory_worker] stop failed", exc_info=True)


_MACRO_SLEEP_INTERVAL_HOURS = 6
# Backup + export cadence: once per day. Kept separate from the 6h
# macro_sleep loop so decay still runs on its own cycle even if the
# backup step stalls on a slow disk.
_BACKUP_EXPORT_INTERVAL_HOURS = 24
# Global reconsolidation: once per day, offset from backup so they don't
# contend for the same disk I/O window.
_RECONSOLIDATION_INTERVAL_HOURS = 24
# Commitment TTL/GC cadence (plan §3.9): every 15 min, matching the shortest
# pending TTL (ready=15m) so orphans get swept promptly. Lightweight + idempotent.
_COMMITMENT_GC_INTERVAL_MINUTES = 15


def _distinct_tenants() -> list[str]:
    """Return all tenant IDs that have episodes in memory_state.db.

    Used by the schedulers to fan maintenance tasks over every active
    tenant instead of only ``"default"``. In the common single-user case
    (``tenant_mode="shared"``) this returns ``["default"]`` and behavior
    is unchanged from the old hardcoded-default path.

    Best-effort: on any error returns ``["default"]`` so maintenance
    continues even if the query fails.
    """
    try:
        import sqlite3

        from kazma_core.paths import primary_memory_db

        conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
        try:
            rows = conn.execute(
                "SELECT DISTINCT tenant_id FROM episodes"
            ).fetchall()
            tenants = [r[0] for r in rows if r[0]]
            # Always include "default" even if it has no episodes yet (fresh install).
            if "default" not in tenants:
                tenants.insert(0, "default")
            return tenants
        finally:
            conn.close()
    except Exception:
        logger.debug("[memory_worker] _distinct_tenants failed — using ['default']", exc_info=True)
        return ["default"]


def _start_macro_sleep_scheduler() -> None:
    """Enqueue a macro_sleep task every N hours (fire-and-forget)."""
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[memory_worker] no loop — macro_sleep scheduler deferred")
        return

    async def _loop() -> None:
        import time

        # Run once shortly after boot (skip the full interval for the first sweep)
        await asyncio.sleep(60)
        while True:
            try:
                from kazma_core.memory.task_queue import enqueue_task

                # Fan out over all active tenants so decay/promotion/archival
                # runs for every tenant, not just "default". In single-user
                # (shared) mode _distinct_tenants() returns ["default"] —
                # unchanged from the old hardcoded behavior.
                for tenant in _distinct_tenants():
                    enqueue_task("macro_sleep", {"tenant_id": tenant, "live_config": True})
                logger.debug("[memory_worker] enqueued periodic macro_sleep")
            except Exception:
                logger.debug("[memory_worker] macro_sleep enqueue failed", exc_info=True)
            await asyncio.sleep(_MACRO_SLEEP_INTERVAL_HOURS * 3600)

    try:
        _t = loop.create_task(_loop())
        _scheduler_tasks.add(_t)
        _t.add_done_callback(_scheduler_tasks.discard)
        logger.info(
            "[memory_worker] macro_sleep scheduler started (every %dh)",
            _MACRO_SLEEP_INTERVAL_HOURS,
        )
    except Exception:
        logger.debug("[memory_worker] could not start macro_sleep scheduler", exc_info=True)


def _start_backup_export_scheduler() -> None:
    """Run native DB backups + nightly exports once per day (fire-and-forget).

    The work itself is enqueued onto the durable task queue (so it inherits
    the queue's retry/dead-letter bounds) via two task handlers registered
    here, rather than performed inline. This keeps the scheduler loop
    trivial and crash-isolated: a failed ``enqueue_task`` cannot kill the
    24h cadence, and a failed handler is retried/bounded by the worker.
    """
    # Register the backup/export handlers (idempotent — safe to call each boot).
    register_backup_export_handlers()

    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[memory_worker] no loop — backup/export scheduler deferred")
        return

    async def _loop() -> None:
        # First sweep shortly after boot (skip the full day-long wait), then
        # repeat once per day. The body only enqueues; the durable worker
        # drains the actual backup/export work on its own loop.
        await asyncio.sleep(120)
        while True:
            try:
                from kazma_core.memory.task_queue import enqueue_task
                from kazma_core.db.pg_backup import pg_backup_enabled

                enqueue_task("native_backup", {"retention": 10})
                # Universal backup (all DBs + assets + PG) — separate task so
                # the native_backup handler stays fast (<300s) and doesn't
                # trigger the queue's processing-reclaim.
                enqueue_task("universal_backup", {})
                # Postgres shared-state dump (self-disables on SQLite installs
                # or the backups.pg.enabled kill-switch — checked live here).
                if pg_backup_enabled():
                    enqueue_task("native_pg_backup", {})
                # Export per-tenant so each tenant's beliefs/graph land in
                # their own file (not overwritten by "default").
                for tenant in _distinct_tenants():
                    enqueue_task("nightly_export", {"tenant_id": tenant})
                logger.debug("[memory_worker] enqueued nightly backup + export")
            except Exception:
                logger.debug("[memory_worker] backup/export enqueue failed", exc_info=True)
            await asyncio.sleep(_BACKUP_EXPORT_INTERVAL_HOURS * 3600)

    try:
        _t = loop.create_task(_loop())
        _scheduler_tasks.add(_t)
        _t.add_done_callback(_scheduler_tasks.discard)
        logger.info(
            "[memory_worker] backup/export scheduler started (every %dh)",
            _BACKUP_EXPORT_INTERVAL_HOURS,
        )
    except Exception:
        logger.debug("[memory_worker] could not start backup/export scheduler", exc_info=True)


def _start_commitment_gc_scheduler() -> None:
    """Run the commitment TTL/GC cycle every ~15 min (plan §3.9).

    ``run_gc_cycle`` does sweep_expired (rule 1) + enforce_all_pending_caps
    (rule 6) + delete_retained (rules 3+4). All three are lightweight SQL
    passes on the ops DB and idempotent, so this runs inline (no durable-queue
    overhead, unlike the heavy backup/export path). Without this scheduler,
    expired pending commitments would accumulate forever even though the sweep
    logic exists — the same "scheduler existed but nothing called it" gap that
    once left backups inert (AGENTS.md §15B). Failures are logged and the
    cadence continues.
    """
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[memory_worker] no loop — commitment GC scheduler deferred")
        return

    async def _loop() -> None:
        await asyncio.sleep(90)  # first sweep shortly after boot
        while True:
            try:
                from kazma_core.safety.commitment.store import run_gc_cycle

                summary = run_gc_cycle()
                if any(summary.values()):
                    logger.info("[memory_worker] commitment GC: %s", summary)
            except Exception:
                logger.debug("[memory_worker] commitment GC cycle failed", exc_info=True)
            await asyncio.sleep(_COMMITMENT_GC_INTERVAL_MINUTES * 60)

    try:
        _t = loop.create_task(_loop())
        _scheduler_tasks.add(_t)
        _t.add_done_callback(_scheduler_tasks.discard)
        logger.info(
            "[memory_worker] commitment GC scheduler started (every %dm)",
            _COMMITMENT_GC_INTERVAL_MINUTES,
        )
    except Exception:
        logger.debug("[memory_worker] could not start commitment GC scheduler", exc_info=True)


def _start_reconsolidation_scheduler() -> None:
    """Enqueue global_reconsolidation every 24h (offset from backup)."""
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[memory_worker] no loop — reconsolidation scheduler deferred")
        return

    async def _loop() -> None:
        # Offset first run from backup (180s) so they don't stampede disk
        await asyncio.sleep(180)
        while True:
            try:
                from kazma_core.memory.task_queue import enqueue_task

                # Fan dedup/re-embed over all active tenants.
                for tenant in _distinct_tenants():
                    enqueue_task(
                        "global_reconsolidation",
                        {"tenant_id": tenant, "max_merges": 50, "reembed_limit": 100},
                    )
                logger.debug("[memory_worker] enqueued global_reconsolidation")
            except Exception:
                logger.debug(
                    "[memory_worker] reconsolidation enqueue failed", exc_info=True
                )
            await asyncio.sleep(_RECONSOLIDATION_INTERVAL_HOURS * 3600)

    try:
        _t = loop.create_task(_loop())
        _scheduler_tasks.add(_t)
        _t.add_done_callback(_scheduler_tasks.discard)
        logger.info(
            "[memory_worker] reconsolidation scheduler started (every %dh)",
            _RECONSOLIDATION_INTERVAL_HOURS,
        )
    except Exception:
        logger.debug(
            "[memory_worker] could not start reconsolidation scheduler", exc_info=True
        )


def register_backup_export_handlers() -> None:
    """Register the native-backup and nightly-export queue handlers (idempotent).

    Each handler wraps the corresponding best-effort routine in
    :mod:`kazma_core.memory.backup` / :mod:`kazma_core.memory.export` and
    returns ``True`` on success / ``False`` on failure so the durable queue
    retries up to ``max_attempts`` before dead-lettering.
    """
    global _backup_export_registered
    if _backup_export_registered:
        return
    from kazma_core.memory.task_queue import register_handler

    register_handler("native_backup", _handle_native_backup)
    register_handler("nightly_export", _handle_nightly_export)
    register_handler("native_pg_backup", _handle_native_pg_backup)
    register_handler("universal_backup", _handle_universal_backup)
    _backup_export_registered = True
    logger.info("[memory_worker] backup/export task handlers registered")


async def _handle_native_backup(payload: dict[str, Any]) -> bool:
    """Run one native ``sqlite3.backup()`` sweep of both memory DBs.

    Also performs a consistent document-store backup (documents.db + the
    referenced content-addressed tree) so the document platform is covered by
    the same nightly cadence. The universal backup (ALL DBs + assets + PG)
    is a SEPARATE task (``universal_backup``) so this handler stays fast.
    """
    try:
        from kazma_core.memory.backup import perform_native_backups

        retention = int(payload.get("retention", 10))
        written = perform_native_backups(retention=retention)
        logger.info("[memory_worker] native_backup done: %d file(s)", len(written))
    except Exception:
        logger.warning("[memory_worker] native_backup handler failed", exc_info=True)
        return False
    # Document store backup is independent — a failure here must not fail the
    # memory backup that already succeeded.
    try:
        from kazma_core.documents.backup import perform_document_backup

        report = perform_document_backup(retention=max(1, int(payload.get("retention", 10)) // 2))
        if report.get("ok"):
            logger.info(
                "[memory_worker] document backup done: %s blob(s)",
                report.get("copied_blobs", report.get("skipped", 0)),
            )
        else:
            logger.warning("[memory_worker] document backup: %s", report.get("error"))
    except Exception:
        logger.warning("[memory_worker] document backup failed", exc_info=True)
    return True


async def _handle_universal_backup(payload: dict[str, Any]) -> bool:
    """Run the universal backup (ALL DBs + assets + Postgres) as its own task.

    Separated from _handle_native_backup so the memory+document backup stays
    fast (<300s) and doesn't trigger the durable queue's processing-reclaim
    (which was re-enqueuing every 5 min and causing duplicate backups).
    """
    try:
        import asyncio as _aio

        from kazma_core.backup.universal import perform_universal_backup

        result = await _aio.to_thread(perform_universal_backup, trigger="auto")
        if result.get("ok"):
            logger.info(
                "[memory_worker] universal_backup done: %d DBs, %.1f MB",
                result.get("databases_ok", 0),
                result.get("total_size_mb", 0),
            )
        else:
            logger.debug("[memory_worker] universal_backup skipped: %s", result.get("error", ""))
        return True
    except Exception:
        logger.warning("[memory_worker] universal_backup failed", exc_info=True)
        return True  # never fail the queue on backup errors


async def _handle_nightly_export(payload: dict[str, Any]) -> bool:
    """Run one nightly JSONL + GraphML export of the cognitive state."""
    try:
        from kazma_core.memory.export import export_nightly_snapshots

        tenant_id = str(payload.get("tenant_id", "default"))
        written = export_nightly_snapshots(tenant_id=tenant_id)
        logger.info("[memory_worker] nightly_export done: %d file(s)", len(written))
        return True
    except Exception:
        logger.warning("[memory_worker] nightly_export handler failed", exc_info=True)
        return False


async def _handle_native_pg_backup(payload: dict[str, Any]) -> bool:
    """Run one nightly pg_dump of the Postgres shared-state tables.

    Self-disabling: returns True (success/no-op) when Kazma is not on
    Postgres or the ``backups.pg.enabled`` kill-switch is off — the durable
    queue must not retry a deliberately-disabled task. The dump itself runs
    in a worker thread (pg_dump is a blocking subprocess).
    """
    try:
        import asyncio

        from kazma_core.db.pg_backup import perform_pg_backup, pg_backup_enabled

        if not pg_backup_enabled():
            return True  # not on Postgres / kill-switched — nothing to do
        path = await asyncio.to_thread(perform_pg_backup)
        if path is None:
            logger.warning("[memory_worker] native_pg_backup produced no dump")
            return False  # real failure — let the queue retry
        logger.info("[memory_worker] native_pg_backup done: %s", path.name)
        return True
    except Exception:
        logger.warning("[memory_worker] native_pg_backup handler failed", exc_info=True)
        return False
