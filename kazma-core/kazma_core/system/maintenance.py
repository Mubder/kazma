"""Memory backup, restoration, and maintenance operations for Kazma's memory architecture.

This module provides routines to perform zero-downtime hot backups, safe rolling
restorations with pre-restore checkpoints, database optimizations (VACUUM/ANALYZE),
and hot-reloading of active memory singletons.
"""

from __future__ import annotations

import os
import json
import shutil
import logging
import sqlite3
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def get_memory_paths() -> tuple[Path, Path, Path]:
    """Retrieve resolved absolute paths for memory resources and backups.

    Returns:
        A tuple of (fts5_path, vector_path, backups_dir) Path objects.
    """
    fts5_path = Path(os.environ.get("KAZMA_FTS5_PATH", "~/.kazma/memory.db")).expanduser().resolve()
    vector_path = Path(os.environ.get("KAZMA_VECTOR_PATH", "~/.kazma/vector_memory")).expanduser().resolve()
    backups_dir = Path(os.environ.get("KAZMA_BACKUPS_DIR", "~/.kazma/backups")).expanduser().resolve()
    return fts5_path, vector_path, backups_dir


def create_memory_backup() -> dict[str, Any]:
    """Perform a zero-downtime hot backup of both keyword and vector stores.

    Creates a timestamped backup directory in the backup folder containing
    copied/backed-up databases and a metadata manifest file.

    Returns:
        A summary dictionary of the created backup.
    """
    fts5_path, vector_path, backups_dir = get_memory_paths()

    # Generate timestamped backup folder
    backup_name = f"memory_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    backup_dir = backups_dir / backup_name
    backup_dir.mkdir(parents=True, exist_ok=True)

    fts5_size = None
    fts5_count = 0
    vector_size = None
    vector_count = None

    logger.info("[Maintenance] Initiating memory backup: %s", backup_name)

    # 1. Hot Backup keyword database (FTS5 SQLite)
    if fts5_path.exists():
        backup_db_file = backup_dir / "memory.db"
        try:
            # Safe zero-downtime hot backup via SQLite backup API
            src_conn = sqlite3.connect(fts5_path)
            dest_conn = sqlite3.connect(backup_db_file)
            src_conn.backup(dest_conn)
            dest_conn.close()
            src_conn.close()

            fts5_size = backup_db_file.stat().st_size

            # Count rows for manifest
            try:
                conn = sqlite3.connect(backup_db_file)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'")
                if cursor.fetchone():
                    cursor.execute("SELECT COUNT(*) FROM memory_fts")
                    fts5_count = cursor.fetchone()[0]
                conn.close()
            except Exception as e:
                logger.debug("[Maintenance] Could not count FTS5 records: %s", e)
        except Exception as e:
            logger.error("[Maintenance] FTS5 hot backup failed: %s", e, exc_info=True)
            # Cleanup half-baked backup dir
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            raise e

    # 2. Backup vector store (ChromaDB)
    if vector_path.exists() and vector_path.is_dir():
        backup_vector_dir = backup_dir / "vector_memory"
        try:
            shutil.copytree(vector_path, backup_vector_dir, dirs_exist_ok=True)
            vector_size = sum(f.stat().st_size for f in backup_vector_dir.glob("**/*") if f.is_file())

            # Count entries from active memory singleton if accessible
            from kazma_core.agent.tool_registry import get_vector_memory
            vm = get_vector_memory()
            if vm and not getattr(vm, "degraded", False):
                try:
                    vector_count = vm.count
                except Exception:
                    pass
        except Exception as e:
            logger.error("[Maintenance] Vector memory backup failed: %s", e, exc_info=True)
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            raise e

    # 3. Compile and save Manifest file
    manifest = {
        "name": backup_name,
        "timestamp": datetime.now(UTC).isoformat(),
        "fts5_size": fts5_size,
        "fts5_count": fts5_count,
        "vector_size": vector_size,
        "vector_count": vector_count,
    }

    manifest_file = backup_dir / "backup_manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    logger.info("[Maintenance] Backup %s completed successfully.", backup_name)
    return manifest


async def restore_memory_backup(backup_name: str) -> dict[str, Any]:
    """Restore memory stores from a designated backup with pre-restore rollbacks.

    Args:
        backup_name: The subdirectory folder name under backups directory.

    Returns:
        Status dictionary indicating success.
    """
    fts5_path, vector_path, backups_dir = get_memory_paths()
    backup_dir = backups_dir / backup_name

    if not backup_dir.exists() or not (backup_dir / "backup_manifest.json").exists():
        raise FileNotFoundError(f"Backup target {backup_name} not found or invalid.")

    logger.info("[Maintenance] Initiating restore from backup: %s", backup_name)

    # 1. Create a safe temporary rolling rollback checkpoint
    temp_rollback_dir = backups_dir / ".temp_rollback"
    if temp_rollback_dir.exists():
        shutil.rmtree(temp_rollback_dir)
    temp_rollback_dir.mkdir(parents=True)

    try:
        if fts5_path.exists():
            src_conn = sqlite3.connect(fts5_path)
            dest_conn = sqlite3.connect(temp_rollback_dir / "memory.db")
            src_conn.backup(dest_conn)
            dest_conn.close()
            src_conn.close()
        if vector_path.exists():
            shutil.copytree(vector_path, temp_rollback_dir / "vector_memory", dirs_exist_ok=True)
    except Exception as e:
        logger.warning("[Maintenance] Rolling backup checkpoint warning: %s. Proceeding.", e)

    # 2. Execute restoration with rollback protection
    try:
        # Restore FTS5 DB (Keyword)
        backup_db_file = backup_dir / "memory.db"
        if backup_db_file.exists():
            # To bypass Windows file locking, write directly using SQLite's backup API
            dest_conn = sqlite3.connect(fts5_path)
            src_conn = sqlite3.connect(backup_db_file)
            src_conn.backup(dest_conn)
            dest_conn.close()
            src_conn.close()
            logger.info("[Maintenance] Restored FTS5 SQLite database in-place.")
        else:
            if fts5_path.exists():
                fts5_path.unlink()

        # Restore Vector Memory (ChromaDB)
        backup_vector_dir = backup_dir / "vector_memory"
        if backup_vector_dir.exists():
            # Disconnect vector memory to try to release locks
            from kazma_core.agent.tool_registry import get_vector_memory
            vm = get_vector_memory()
            if vm and hasattr(vm, "_client"):
                try:
                    del vm._client
                    import gc
                    gc.collect()
                except Exception:
                    pass

            if vector_path.exists():
                try:
                    shutil.rmtree(vector_path)
                except Exception as e:
                    logger.debug("[Maintenance] Could not delete active vector folder. Overwriting files. Error: %s", e)

            shutil.copytree(backup_vector_dir, vector_path, dirs_exist_ok=True)
            logger.info("[Maintenance] Restored Vector store files.")
        else:
            if vector_path.exists():
                try:
                    shutil.rmtree(vector_path)
                except Exception:
                    pass

        # 3. Dynamic Hot Reload of memory instances
        await _hot_reload_memory()

        # Clean up temp rollback folder
        if temp_rollback_dir.exists():
            shutil.rmtree(temp_rollback_dir)

        logger.info("[Maintenance] Restoration of backup %s completed successfully.", backup_name)
        return {"status": "success", "message": f"Successfully restored {backup_name} and hot-reloaded memory stores."}

    except Exception as restore_err:
        logger.error("[Maintenance] Restoration failed: %s. Reverting to rollback checkpoint.", restore_err, exc_info=True)
        try:
            # Revert to rollback checkpoint
            if (temp_rollback_dir / "memory.db").exists():
                dest_conn = sqlite3.connect(fts5_path)
                src_conn = sqlite3.connect(temp_rollback_dir / "memory.db")
                src_conn.backup(dest_conn)
                dest_conn.close()
                src_conn.close()
            if (temp_rollback_dir / "vector_memory").exists():
                if vector_path.exists():
                    shutil.rmtree(vector_path, ignore_errors=True)
                shutil.copytree(temp_rollback_dir / "vector_memory", vector_path, dirs_exist_ok=True)
            await _hot_reload_memory()
            logger.info("[Maintenance] Reverted to safe rollback checkpoint successfully.")
        except Exception as rollback_err:
            logger.critical("[Maintenance] CRITICAL: Reversion to safe rollback checkpoint also failed: %s", rollback_err, exc_info=True)
        
        if temp_rollback_dir.exists():
            shutil.rmtree(temp_rollback_dir, ignore_errors=True)
        raise restore_err


def run_memory_maintenance() -> dict[str, Any]:
    """Execute maintenance optimization (VACUUM and ANALYZE) on memory databases.

    Reclaims disk space, optimizes index statistics, and returns reclaimed metrics.

    Returns:
        Optimization outcome results detailing space reclaimed.
    """
    fts5_path, vector_path, _ = get_memory_paths()
    details: dict[str, Any] = {}

    # 1. Optimize Keyword Memory (SQLite FTS5)
    if fts5_path.exists():
        try:
            size_before = fts5_path.stat().st_size
            conn = sqlite3.connect(fts5_path)
            conn.execute("VACUUM;")
            conn.execute("ANALYZE;")
            conn.close()
            size_after = fts5_path.stat().st_size

            details["fts5"] = {
                "status": "success",
                "size_before": size_before,
                "size_after": size_after,
                "reclaimed_bytes": max(0, size_before - size_after),
            }
            logger.info("[Maintenance] FTS5 SQLite optimized successfully.")
        except Exception as e:
            logger.error("[Maintenance] FTS5 optimization failed: %s", e)
            details["fts5"] = {"status": "failed", "error": str(e)}

    # 2. Optimize Vector Memory Database (ChromaDB chroma.sqlite3 if present)
    chroma_sqlite = vector_path / "chroma.sqlite3"
    if chroma_sqlite.exists():
        try:
            size_before = chroma_sqlite.stat().st_size
            conn = sqlite3.connect(chroma_sqlite)
            conn.execute("VACUUM;")
            conn.execute("ANALYZE;")
            conn.close()
            size_after = chroma_sqlite.stat().st_size

            details["vector"] = {
                "status": "success",
                "size_before": size_before,
                "size_after": size_after,
                "reclaimed_bytes": max(0, size_before - size_after),
            }
            logger.info("[Maintenance] Vector store SQLite optimized successfully.")
        except Exception as e:
            logger.error("[Maintenance] Vector SQLite optimization failed: %s", e)
            details["vector"] = {"status": "failed", "error": str(e)}

    return details


def list_memory_backups() -> list[dict[str, Any]]:
    """Retrieve and list all available memory backups sorted by date.

    Returns:
        A sorted list of manifest dictionaries.
    """
    _, _, backups_dir = get_memory_paths()
    if not backups_dir.exists():
        return []

    backups = []
    for path in backups_dir.iterdir():
        if path.is_dir() and not path.name.startswith("."):
            manifest_file = path / "backup_manifest.json"
            if manifest_file.exists():
                try:
                    with open(manifest_file, "r", encoding="utf-8") as f:
                        manifest = json.load(f)
                    backups.append(manifest)
                except Exception as e:
                    logger.debug("[Maintenance] Could not read manifest for %s: %s", path.name, e)
            else:
                # Reconstruct info if manifest is missing
                try:
                    fts5_size = None
                    vector_size = None
                    if (path / "memory.db").exists():
                        fts5_size = (path / "memory.db").stat().st_size
                    if (path / "vector_memory").exists():
                        vector_size = sum(f.stat().st_size for f in (path / "vector_memory").glob("**/*") if f.is_file())
                    
                    backups.append({
                        "name": path.name,
                        "timestamp": datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(),
                        "fts5_size": fts5_size,
                        "fts5_count": 0,
                        "vector_size": vector_size,
                        "vector_count": None,
                    })
                except Exception:
                    pass

    # Sort descending by timestamp
    backups.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return backups


async def _hot_reload_memory() -> None:
    """Hot-reload the VectorMemory singleton globally.

    Uses the SAME environment variables as ``app.py`` startup so the
    hot-reload points at the user's configured path/collection/model,
    not the defaults.
    """
    import os
    from kazma_core.memory.vector_store import VectorMemory
    from kazma_core.agent.tool_registry import set_vector_memory

    logger.info("[Maintenance] Reloading global memory instance...")
    try:
        path = os.environ.get("KAZMA_VECTOR_PATH", "~/.kazma/vector_memory")
        collection = os.environ.get("KAZMA_VECTOR_COLLECTION", "agent_memory")
        model = os.environ.get("KAZMA_VECTOR_MODEL", "all-MiniLM-L6-v2")
        mem = VectorMemory(path=path, collection_name=collection, model_name=model)
        set_vector_memory(mem)
        logger.info("[Maintenance] Memory reload complete. Registered count: %s", mem.count)
    except Exception as e:
        logger.error("[Maintenance] Failed to hot-reload global memory: %s", e, exc_info=True)
