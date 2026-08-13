"""Native SQLite streaming backups for the V2 memory databases.

Uses Python's built-in :meth:`sqlite3.Connection.backup` (the Online
Backup API) — no external Go binaries, no ``sqlite3`` CLI required.
This is the correct way to copy a WAL-mode database while it is in use:
the backup holds a read transaction and sees a consistent snapshot
without blocking writers.

Backups are written to :func:`kazma_core.paths.backups_dir` with a
timestamped filename and a configurable retention cap. Both the primary
(``memory_state.db``) and operational (``memory_ops.db``) databases are
backed up on each call.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["perform_native_backups", "prune_old_backups", "backup_one"]

# Default retention: keep the most recent N backups per database.
_DEFAULT_RETENTION = 10


def backup_one(src_path: Path, dest_path: Path) -> bool:
    """Stream-copy one database via the Online Backup API.

    Returns True on success.  ``pages=100`` batches the copy to avoid
    holding a long lock; ``sleep`` yields between batches so concurrent
    writers are not starved. Reusable by callers outside the memory
    subsystem (e.g. the migration importer's pre-swap safety backup).
    """
    return _backup_one(src_path, dest_path)


def _backup_one(src_path: Path, dest_path: Path) -> bool:
    """Stream-copy one database via the Online Backup API (internal impl).

    Kept as a private alias so existing call sites (and the migration
    engine) keep working; new external callers should prefer
    :func:`backup_one`.
    """
    try:
        with sqlite3.connect(str(src_path)) as src, sqlite3.connect(str(dest_path)) as dst:
            src.backup(dst, pages=100, sleep=0.01)
        return True
    except Exception as exc:
        logger.warning("[backup] failed for %s → %s: %s", src_path, dest_path, exc)
        # Don't leave a half-written backup lying around.
        try:
            dest_path.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def perform_native_backups(*, retention: int = _DEFAULT_RETENTION) -> list[Path]:
    """Back up both V2 memory databases to the backups directory.

    Args:
        retention: Keep at most this many most-recent backups per DB.
            Older copies beyond the cap are pruned after a successful run.

    Returns:
        List of backup file paths actually written (empty if a DB file
        does not exist yet — e.g. before first use).
    """
    from kazma_core.paths import backups_dir, memory_ops_db, primary_memory_db

    out_dir = backups_dir()
    timestamp = int(time.time())
    written: list[Path] = []

    for db_path_str in (primary_memory_db(), memory_ops_db()):
        db_path = Path(db_path_str)
        if not db_path.exists():
            logger.debug("[backup] skip %s (does not exist yet)", db_path.name)
            continue
        dest = out_dir / f"{db_path.stem}_{timestamp}.db"
        if _backup_one(db_path, dest):
            written.append(dest)
            logger.info("[backup] %s → %s", db_path.name, dest.name)

    if written:
        prune_old_backups(retention=retention)
    return written


def prune_old_backups(*, retention: int = _DEFAULT_RETENTION) -> int:
    """Delete oldest backups beyond the retention cap, per database stem.

    Matches files named ``<stem>_<timestamp>.db`` in the backups dir.
    Returns the number of files deleted.
    """
    from kazma_core.paths import backups_dir

    out_dir = backups_dir()
    deleted = 0
    # Only the two memory DBs are backed up here; restrict to their stems so
    # an unrelated *_<digits>.db file in backups_dir() (e.g. an export) isn't
    # grouped and pruned if its "group" exceeded retention (audit finding).
    _known_prefixes = {"memory_state", "memory_ops"}
    # Group by the prefix before the final _<timestamp>.db
    groups: dict[str, list[Path]] = {}
    for f in out_dir.glob("*_*.db"):
        # stem like "memory_state_1234567890" → prefix "memory_state"
        name = f.stem  # e.g. memory_state_1234567890
        # Split off the trailing integer timestamp
        parts = name.rsplit("_", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        prefix = parts[0]
        if prefix not in _known_prefixes:
            continue
        groups.setdefault(prefix, []).append(f)

    for prefix, files in groups.items():
        if len(files) <= retention:
            continue
        # Sort by mtime descending; keep newest `retention`, delete the rest
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[retention:]:
            try:
                stale.unlink()
                deleted += 1
                logger.debug("[backup] pruned old backup %s", stale.name)
            except Exception:
                pass
    return deleted
