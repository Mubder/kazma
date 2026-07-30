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

__all__ = ["register_v2_handlers", "start_memory_worker"]

_registered = False


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
                "SELECT user_text, assistant_text, session_id, turn_number FROM episodes WHERE id=?",
                (episode_id,),
            ).fetchone()
            if not row:
                return True  # episode gone
            stats = await extract_and_apply_beliefs(
                primary, ops,
                row["user_text"] or "", row["assistant_text"] or "",
                session_id=row["session_id"], turn=row["turn_number"],
            )
            logger.info(
                "[memory_worker] micro_consolidation of %s: applied=%d",
                episode_id, stats.get("applied", 0),
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

    Also starts a lightweight background scheduler that enqueues a
    ``macro_sleep`` task every 6 hours so decay/tier-transitions/archival
    run periodically without manual intervention.
    """
    try:
        register_v2_handlers()
        from kazma_core.memory.task_queue import start_worker

        start_worker()
        _start_macro_sleep_scheduler()
    except Exception:
        logger.debug("[memory_worker] could not start worker", exc_info=True)


_MACRO_SLEEP_INTERVAL_HOURS = 6


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
