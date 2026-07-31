"""Memory maintenance operations — RETIRED (V1→V2 cutover).

The V1 memory stack (ChromaDB VectorMemory, FTS5, property graph) was removed.
These functions backed the legacy ``/api/system/memory/*`` routes, which now
call the V2 equivalents directly:

  - backup  → ``kazma_core.memory.backup.perform_native_backups`` (native
    sqlite3.backup of memory_state.db + memory_ops.db).
  - restore → file-swap memory_state.db from a chosen backup (route handler
    in kazma_ui/routes_direct.py).
  - maintenance (VACUUM/ANALYZE) → runs against the two V2 DBs directly
    (route handler in kazma_ui/routes_direct.py).
  - backups list → scans ``paths.backups_dir()`` for V2 backup files.

The stubs below are retained so ``from kazma_core.system import ...``
(``system/__init__.py`` re-exports them) does not break any lingering
importer, but they are no-ops that log the retirement. Do not add new
callers — call the V2 functions directly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

__all__ = [
    "create_memory_backup",
    "get_memory_paths",
    "list_memory_backups",
    "restore_memory_backup",
    "run_memory_integrity_backfill",
    "run_memory_maintenance",
]

logger = logging.getLogger(__name__)


def get_memory_paths() -> tuple[Path, Path, Path]:
    """Resolve (fts5_path, vector_path, backups_dir) — V1 paths are inert.

    The V1 fts5/vector paths no longer exist post-cutover; they're returned
    for backward-compat with code that still calls this (e.g. _get_system_status
    size probes, which will report 0 since the files are gone). backups_dir is
    the live V2 backups directory.
    """
    from kazma_core.paths import (
        backups_dir as _backups_dir,
        fts5_memory_path,
        vector_memory_path,
    )

    return (
        Path(fts5_memory_path()),
        Path(vector_memory_path()),
        _backups_dir(),
    )


def create_memory_backup() -> dict[str, Any]:
    """Retired — use ``memory.backup.perform_native_backups`` (V2)."""
    logger.warning(
        "[maintenance] create_memory_backup() is retired (V1 removed). "
        "Use memory.backup.perform_native_backups() via POST /api/system/memory/backup."
    )
    return {"status": "retired", "reason": "V1 memory stack removed; use V2 backup route."}


async def restore_memory_backup(backup_name: str) -> dict[str, Any]:
    """Retired — use the V2 restore route (POST /api/system/memory/restore)."""
    logger.warning(
        "[maintenance] restore_memory_backup() is retired (V1 removed). "
        "Use POST /api/system/memory/restore with {backup_name}."
    )
    return {"status": "retired", "reason": "V1 memory stack removed; use V2 restore route."}


def run_memory_integrity_backfill(**kwargs: Any) -> dict[str, Any]:
    """Retired — the V1 L3/graph integrity repair has no V2 equivalent
    (V2 is bi-temporal, no legacy NULL timestamps to repair)."""
    logger.warning(
        "[maintenance] run_memory_integrity_backfill() is retired (V1 removed). "
        "V2 has no equivalent (bi-temporal stores need no integrity backfill)."
    )
    return {"status": "retired", "reason": "V1 integrity repair not applicable to V2."}


def run_memory_maintenance() -> dict[str, Any]:
    """Retired — use the V2 maintenance route (VACUUM/ANALYZE on the two V2 DBs)."""
    logger.warning(
        "[maintenance] run_memory_maintenance() is retired (V1 removed). "
        "Use POST /api/system/memory/maintenance (VACUUM on V2 DBs)."
    )
    return {"status": "retired", "reason": "V1 memory stack removed; use V2 maintenance route."}


def list_memory_backups() -> list[dict[str, Any]]:
    """Retired — use the V2 backups route (GET /api/system/memory/backups)."""
    logger.warning(
        "[maintenance] list_memory_backups() is retired (V1 removed). "
        "Use GET /api/system/memory/backups (scans V2 backups_dir)."
    )
    return []


async def _hot_reload_memory() -> None:
    """Retired no-op (V2 needs no ChromaDB hot-reload)."""
    logger.info("[Maintenance] V2 memory stack — no V1 hot-reload needed (no-op).")
