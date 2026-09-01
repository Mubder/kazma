"""Scheduled Postgres backup + schema assurance for Kazma's shared-state tables.

Why this module exists (incident 2026-08-14): a second app (KCA) was pointed
at the same ``kazma`` database and its migration dropped Kazma's tables —
``checkpoints``, ``kazma_settings``, ``kazma_chat_sessions``,
``document_jobs``, … There was no scheduled Postgres backup, so recovery
depended on a lucky migration-bundle dump. This module closes that gap from
both sides:

1. **Nightly ``pg_dump``** of *exactly* Kazma's tables (never the whole DB —
   a shared DB must not leak a foreign app's data into our backups, and our
   restore must never touch theirs). The dump is written atomically
   (tmp + rename), magic-validated, and retention-capped.
2. **Boot-time schema verification** (:func:`verify_required_pg_tables`): if
   a required table is missing, boot logs a CRITICAL with the exact restore
   command instead of limping along with UndefinedTable errors.

Dumps go through the pg_dump/pg_restore bridge (:mod:`kazma_core.migration.pg_bridge`):
binary discovery (PATH → ``docker exec ${KAZMA_DB_CONTAINER}``), ``PGPASSWORD``
passed via env (never on the command line), custom (``-Fc``) format.

Config is live-read from the ConfigStore (mirrors ``get_hitl_config`` /
``get_lifecycle_config``) and never raises:

- ``backups.pg.enabled`` (default True — self-disables when not on Postgres)
- ``backups.pg.retention`` (default 7 most-recent dumps kept)

Env kill-switch / tuning: ``KAZMA_PG_BACKUP_ENABLED=0``,
``KAZMA_PG_BACKUP_RETENTION=n``.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "KAZMA_PG_TABLES",
    "get_pg_backup_config",
    "pg_backup_enabled",
    "pg_backup_dir",
    "perform_pg_backup",
    "prune_pg_backups",
    "latest_pg_backup",
    "verify_required_pg_tables",
]

# SoT for the tables Kazma owns in Postgres. Both the nightly dump (filtered
# to EXACTLY these tables — never the whole DB) and the boot-time schema
# verification key off this list. A new shared-state PG table MUST be added
# here (the same deliberate-SoT pattern as CANONICAL_DANGER_TOOLS).
KAZMA_PG_TABLES: list[str] = [
    # LangGraph checkpointer (langgraph-checkpoint-postgres)
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    # Shared-state stores
    "kazma_settings",
    "kazma_chat_sessions",
    "kazma_swarm_tasks",
    "kazma_swarm_worker_metrics",
    "kazma_platform_users",
    "kazma_web_sessions",
    # Document job queue (auto-created by documents/jobs_pg.py)
    "document_jobs",
    "document_job_events",
    # Document catalog / ACL (documents/repository_pg.py) — required when
    # KAZMA_DOCUMENTS_METADATA_BACKEND=postgres|auto (audit H-13 / §21A).
    "documents",
    "document_blobs",
    "document_versions",
    "document_artifacts",
    "document_acl",
    "document_tombstones",
    "document_chunks",
    "document_audit_events",
]

# How many raw .dump files stay on local disk. These are staging, not the
# archive: restic snapshots each dump and keeps the real history under
# KEEP_POLICY, deduplicated. At 1.67 GB apiece the old default of 7 held
# ~11.7 GB of near-identical copies for no recovery benefit that restic was
# not already providing -- and the dumps got more frequent when the backup
# loop moved to 6-hourly, which would have made that worse.
_DEFAULT_RETENTION = 3

_DUMP_MAGIC = b"PGDMP"


def get_pg_backup_config() -> dict[str, Any]:
    """Re-read the PG backup settings LIVE from the ConfigStore.

    Reads flat dotted keys ``backups.pg.enabled`` / ``backups.pg.retention``,
    falls back to env overrides and safe defaults on any error. Never raises.
    Toggling via ``PUT /api/settings/single`` takes effect on the next dump.
    """
    enabled = True
    retention = _DEFAULT_RETENTION
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        raw_enabled = cs.get("backups.pg.enabled")
        if raw_enabled is not None:
            # Tolerate "0"/"false" strings — bool("0") is True, so don't
            # just bool() it.
            if isinstance(raw_enabled, str):
                enabled = raw_enabled.strip().lower() not in ("0", "false", "no", "off", "")
            else:
                enabled = bool(raw_enabled)
        raw_retention = cs.get("backups.pg.retention")
        if raw_retention is not None:
            retention = max(1, int(raw_retention))
    except Exception:
        logger.debug("[pg_backup] config read failed; using defaults", exc_info=True)

    env_kill = (os.environ.get("KAZMA_PG_BACKUP_ENABLED") or "").strip().lower()
    if env_kill in ("0", "false", "no", "off"):
        enabled = False
    env_ret = (os.environ.get("KAZMA_PG_BACKUP_RETENTION") or "").strip()
    if env_ret.isdigit():
        retention = max(1, int(env_ret))
    return {"enabled": enabled, "retention": retention}


def pg_backup_enabled() -> bool:
    """True when the Postgres backend is active AND the backup isn't disabled.

    Never raises — any failure resolves to False (self-disabling).
    """
    try:
        from kazma_core.db.backend import is_postgres

        if not is_postgres():
            return False
    except Exception:
        return False
    return bool(get_pg_backup_config()["enabled"])


def pg_backup_dir() -> Path:
    """Directory for PG dumps: ``{kazma-data}/backups/pg/``."""
    from kazma_core.paths import backups_dir

    return backups_dir() / "pg"


def perform_pg_backup(*, retention: int | None = None) -> Path | None:
    """Dump Kazma's PG shared-state tables to the backups dir (blocking).

    The dump is written to a ``.tmp`` file, validated against the ``PGDMP``
    magic, then atomically renamed into place — a crashed dump can never be
    mistaken for a valid backup. Retention pruning runs only after a
    successful dump (mirrors :func:`kazma_core.memory.backup.perform_native_backups`).

    Returns the written Path, or None when disabled / not on Postgres /
    failed. Never raises — this runs on a background scheduler where a
    failure must not kill the cadence (callers may wrap in
    ``asyncio.to_thread``).
    """
    try:
        if not pg_backup_enabled():
            return None
        from kazma_core.db.backend import get_database_url
        from kazma_core.migration.pg_bridge import dump_database

        dsn = get_database_url()
        if not dsn:
            logger.debug("[pg_backup] no KAZMA_DATABASE_URL — skipping")
            return None

        keep = _DEFAULT_RETENTION if retention is None else max(1, int(retention))
        out_dir = pg_backup_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"pg_shared_{int(time.time())}.dump"
        tmp = out_dir / f".{dest.name}.tmp"

        dump_database(dsn, tmp, tables=KAZMA_PG_TABLES)
        if not _is_valid_dump(tmp):
            tmp.unlink(missing_ok=True)
            logger.warning("[pg_backup] dump failed validation (bad magic) — discarded")
            return None
        tmp.replace(dest)
        pruned = prune_pg_backups(retention=keep)
        size_mb = dest.stat().st_size / (1024 * 1024)
        logger.info(
            "[pg_backup] wrote %s (%.1f MB, %d table(s), pruned %d)",
            dest.name, size_mb, len(KAZMA_PG_TABLES), pruned,
        )
        return dest
    except Exception:
        logger.warning("[pg_backup] pg_dump failed", exc_info=True)
        # Never leave a half-written dump behind.
        try:
            if "tmp" in locals():
                Path(tmp).unlink(missing_ok=True)
        except Exception:
            pass
        return None


def _is_valid_dump(path: Path) -> bool:
    """A dump must start with the pg_dump custom-format magic and be non-trivial."""
    try:
        with open(path, "rb") as f:
            return f.read(len(_DUMP_MAGIC)) == _DUMP_MAGIC and path.stat().st_size > 1024
    except Exception:
        return False


def prune_pg_backups(*, retention: int = _DEFAULT_RETENTION) -> int:
    """Delete oldest PG dumps beyond the retention cap (most-recent N kept).

    Matches ``pg_shared_*.dump`` in the PG backup dir. Returns the number of
    files deleted.
    """
    out_dir = pg_backup_dir()
    files = sorted(
        out_dir.glob("pg_shared_*.dump"),
        # (mtime, name) with name as tie-breaker: the epoch prefix makes
        # lexicographic == chronological, so equal-mtime files (fast test
        # runs, coarse filesystem clocks) still prune deterministically.
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    deleted = 0
    for stale in files[retention:]:
        try:
            stale.unlink()
            deleted += 1
        except Exception:
            logger.debug("[pg_backup] could not prune %s", stale.name, exc_info=True)
    deleted += _sweep_orphaned_tmp(out_dir)
    return deleted


# A dump takes seconds to minutes. Anything still called .tmp an hour later
# belongs to a process that is not coming back.
_TMP_ORPHAN_AGE_S = 3600


def _sweep_orphaned_tmp(out_dir: Path) -> int:
    """Delete .tmp dumps left behind by a process that was killed.

    perform_pg_backup removes its temp file when the dump RAISES, but a
    killed process never runs that handler -- and each abandoned file is a
    full-size dump. Live, 2026-08-29: four orphans totalling 3.83 GB, one
    per Kazma restart that happened to land mid-dump, and nothing had ever
    swept them. Retention only ever matched ``pg_shared_*.dump``, so they
    were invisible to it and grew without bound.
    """
    removed = 0
    cutoff = time.time() - _TMP_ORPHAN_AGE_S
    try:
        for tmp in out_dir.glob(".pg_shared_*.dump.tmp"):
            try:
                if tmp.stat().st_mtime < cutoff:
                    size = tmp.stat().st_size
                    tmp.unlink()
                    removed += 1
                    logger.warning(
                        "[pg_backup] swept orphaned temp dump %s (%.0f MB) -- "
                        "left by an interrupted backup",
                        tmp.name, size / (1024 * 1024),
                    )
            except Exception:
                logger.debug("[pg_backup] could not sweep %s", tmp.name, exc_info=True)
    except Exception:
        logger.debug("[pg_backup] temp sweep failed", exc_info=True)
    return removed


def latest_pg_backup() -> Path | None:
    """Path of the most-recent valid PG dump, or None if none exists yet."""
    files = sorted(
        pg_backup_dir().glob("pg_shared_*.dump"),
        key=lambda p: (p.stat().st_mtime, p.name),
        reverse=True,
    )
    return files[0] if files else None


def verify_required_pg_tables(pool: Any) -> list[str] | None:
    """Return the list of KAZMA_PG_TABLES missing from the connected PG.

    ``pool`` is the shared ``PostgresPool`` (psycopg ConnectionPool). Returns
    None when the schema can't be checked at all (pool down) — callers must
    treat None as "unknown", not "all present". Never raises.
    """
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )
                # The shared PostgresPool uses psycopg's dict_row factory, so
                # rows are dict-like keyed by column name (row[0] would raise
                # KeyError: 0). Index by the selected column name.
                present = {row["tablename"] for row in cur.fetchall()}
    except Exception:
        logger.warning("[pg_backup] schema verification skipped (pool unavailable)", exc_info=True)
        return None
    return [t for t in KAZMA_PG_TABLES if t not in present]
