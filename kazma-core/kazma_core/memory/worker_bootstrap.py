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


def register_v2_handlers() -> None:
    """Register all V2 task handlers on the durable queue (idempotent)."""
    global _registered
    if _registered:
        return
    from kazma_core.memory.task_queue import register_handler

    register_handler("macro_sleep", _handle_macro_sleep)
    register_handler("entity_merge", _handle_entity_merge)
    register_handler("micro_consolidation", _handle_micro_consolidation)
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
            return True
        finally:
            conn.close()
    except Exception:
        logger.debug("[memory_worker] macro_sleep handler failed", exc_info=True)
        return False


async def _handle_entity_merge(payload: dict[str, Any]) -> bool:
    """Resolve a pending entity merge (approve/reject via configured policy).

    Currently auto-approves vector-tier merges that exceed confidence and
    leaves the rest pending for human review. Returns True once a decision
    is recorded.
    """
    try:
        merge_id = payload.get("merge_id")
        if not merge_id:
            return False
        from kazma_core.paths import memory_ops_db, primary_memory_db

        # Read the pending merge
        ops = sqlite3.connect(memory_ops_db(), check_same_thread=False)
        ops.row_factory = sqlite3.Row
        try:
            row = ops.execute(
                "SELECT * FROM entity_merges WHERE id=? AND status='pending'",
                (merge_id,),
            ).fetchone()
            if not row:
                return True  # already resolved / gone
            # Auto-approve tier1_exact always; tier2_vector above 0.85; else leave pending
            conf = float(row["confidence"])
            tier = row["merge_tier"]
            if tier == "tier1_exact" or (tier == "tier2_vector" and conf >= 0.85):
                import time

                ops.execute(
                    "UPDATE entity_merges SET status='approved', resolved_at=? WHERE id=?",
                    (time.time(), merge_id),
                )
                # Apply: merge source aliases into target
                _apply_merge(
                    sqlite3.connect(primary_memory_db(), check_same_thread=False),
                    row["source_entity_id"], row["target_entity_id"],
                )
                logger.info("[memory_worker] auto-approved merge %s (conf=%.2f)", merge_id, conf)
            else:
                logger.info(
                    "[memory_worker] merge %s left pending (tier=%s conf=%.2f < 0.85)",
                    merge_id, tier, conf,
                )
            ops.commit()
            return True
        finally:
            ops.close()
    except Exception:
        logger.debug("[memory_worker] entity_merge handler failed", exc_info=True)
        return False


def _apply_merge(conn: sqlite3.Connection, source_id: str, target_id: str) -> None:
    """Merge source entity's aliases into target, then drop the source row."""
    try:
        src = conn.execute(
            "SELECT aliases_json, name FROM entities WHERE id=?", (source_id,)
        ).fetchone()
        tgt = conn.execute(
            "SELECT aliases_json FROM entities WHERE id=?", (target_id,)
        ).fetchone()
        if src and tgt:
            src_aliases = json.loads(src[0] or "[]")
            tgt_aliases = json.loads(tgt[0] or "[]")
            for a in src_aliases:
                if a not in tgt_aliases:
                    tgt_aliases.append(a)
            if src[1] and src[1] not in tgt_aliases:
                tgt_aliases.append(src[1])
            conn.execute(
                "UPDATE entities SET aliases_json=? WHERE id=?",
                (json.dumps(tgt_aliases), target_id),
            )
            # Redirect beliefs pointing at the source to the target
            conn.execute(
                "UPDATE beliefs SET subject=? WHERE subject=?", (target_id, source_id)
            )
            conn.execute(
                "UPDATE beliefs SET object=? WHERE object=?", (target_id, source_id)
            )
            conn.execute("DELETE FROM entities WHERE id=?", (source_id,))
            conn.commit()
    except Exception:
        logger.debug("[memory_worker] merge apply failed", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass


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
        logger.debug("[memory_worker] micro_consolidation handler failed", exc_info=True)
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
    except Exception:
        logger.debug("[memory_worker] could not start worker", exc_info=True)


_MACRO_SLEEP_INTERVAL_HOURS = 6
# Backup + export cadence: once per day. Kept separate from the 6h
# macro_sleep loop so decay still runs on its own cycle even if the
# backup step stalls on a slow disk.
_BACKUP_EXPORT_INTERVAL_HOURS = 24


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

                enqueue_task("macro_sleep", {"tenant_id": "default", "live_config": True})
                logger.debug("[memory_worker] enqueued periodic macro_sleep")
            except Exception:
                logger.debug("[memory_worker] macro_sleep enqueue failed", exc_info=True)
            await asyncio.sleep(_MACRO_SLEEP_INTERVAL_HOURS * 3600)

    try:
        loop.create_task(_loop())
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

                enqueue_task("native_backup", {"retention": 10})
                enqueue_task("nightly_export", {"tenant_id": "default"})
                logger.debug("[memory_worker] enqueued nightly backup + export")
            except Exception:
                logger.debug("[memory_worker] backup/export enqueue failed", exc_info=True)
            await asyncio.sleep(_BACKUP_EXPORT_INTERVAL_HOURS * 3600)

    try:
        loop.create_task(_loop())
        logger.info(
            "[memory_worker] backup/export scheduler started (every %dh)",
            _BACKUP_EXPORT_INTERVAL_HOURS,
        )
    except Exception:
        logger.debug("[memory_worker] could not start backup/export scheduler", exc_info=True)


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
    _backup_export_registered = True
    logger.info("[memory_worker] backup/export task handlers registered")


async def _handle_native_backup(payload: dict[str, Any]) -> bool:
    """Run one native ``sqlite3.backup()`` sweep of both memory DBs."""
    try:
        from kazma_core.memory.backup import perform_native_backups

        retention = int(payload.get("retention", 10))
        written = perform_native_backups(retention=retention)
        logger.info("[memory_worker] native_backup done: %d file(s)", len(written))
        return True
    except Exception:
        logger.debug("[memory_worker] native_backup handler failed", exc_info=True)
        return False


async def _handle_nightly_export(payload: dict[str, Any]) -> bool:
    """Run one nightly JSONL + GraphML export of the cognitive state."""
    try:
        from kazma_core.memory.export import export_nightly_snapshots

        tenant_id = str(payload.get("tenant_id", "default"))
        written = export_nightly_snapshots(tenant_id=tenant_id)
        logger.info("[memory_worker] nightly_export done: %d file(s)", len(written))
        return True
    except Exception:
        logger.debug("[memory_worker] nightly_export handler failed", exc_info=True)
        return False
