"""Asynchronous background package installer for hot-reloading dependencies."""

from __future__ import annotations

import sys
import asyncio
import logging
import shutil
from kazma_core.config_store import get_config_store

logger = logging.getLogger(__name__)

# Track active installer tasks to prevent concurrent duplicate installations
_active_installations: set[str] = set()


async def asynchronous_install_package(package_name: str) -> None:
    """Install a package in the background using uv or pip, hot-reload, and update status.
    
    This ensures zero-timeout execution by running as a detached background task.
    """
    if package_name in _active_installations:
        logger.info("[Installer] Installation for %s is already in progress.", package_name)
        return
        
    _active_installations.add(package_name)
    
    # Set status to INSTALLING immediately to persist status across reloads
    try:
        store = get_config_store()
        store.set("system.memory.status", "INSTALLING", category="system")
    except Exception as e:
        logger.error("[Installer] Failed to set status to INSTALLING: %s", e)
        
    asyncio.create_task(_run_install_task(package_name))


async def _run_install_task(package_name: str) -> None:
    try:
        logger.info("[Installer] Starting background installation of: %s", package_name)
        
        # Determine package list
        packages = [package_name]
        if package_name in ("sentence-transformers", "chromadb", "sentence_transformers"):
            # Install both to resolve the memory pillar
            packages = ["sentence-transformers", "chromadb"]
            
        # Check if uv is available
        uv_path = shutil.which("uv")
        if uv_path:
            # Run: uv pip install --python <sys.executable> <packages>
            cmd = [uv_path, "pip", "install", "--python", sys.executable] + packages
        else:
            # Fallback to standard pip
            cmd = [sys.executable, "-m", "pip", "install"] + packages
            
        logger.info("[Installer] Executing command: %s", " ".join(cmd))
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        
        stdout, stderr = await proc.communicate()
        
        if proc.returncode == 0:
            logger.info("[Installer] Package(s) %s installed successfully!", packages)
            
            # Update ConfigStore status
            store = get_config_store()
            store.set("system.memory.status", "ACTIVE", category="system")
            
            # Hot-reload memory: trigger re-indexing
            await _hot_reload_memory()
        else:
            err_msg = stderr.decode(errors="replace")
            logger.error("[Installer] Package installation failed with code %d. Error: %s", proc.returncode, err_msg)
            try:
                store = get_config_store()
                store.set("system.memory.status", "DEGRADED", category="system")
            except Exception:
                pass
            
    except Exception as e:
        logger.error("[Installer] Unexpected error in background installer: %s", e, exc_info=True)
        try:
            store = get_config_store()
            store.set("system.memory.status", "DEGRADED", category="system")
        except Exception:
            pass
    finally:
        _active_installations.discard(package_name)


async def _hot_reload_memory() -> None:
    """Hot-reloads the VectorMemory to active status and triggers re-indexing of fallback memories if needed."""
    try:
        logger.info("[Installer] Triggering VectorMemory hot-reload...")
        from kazma_core.memory.vector_store import VectorMemory
        # Instantiate VectorMemory anew, which will now successfully import chromadb
        mem = VectorMemory()
        
        # Update global vector memory reference
        from kazma_core.agent.tool_registry import set_vector_memory
        set_vector_memory(mem)
        
        # Clear the degraded alerts for VectorMemory
        from kazma_core.observability.alerts import AlertDispatcher
        AlertDispatcher.resolve_alerts_for_subsystem("VectorMemory")
        
        # If there's any pending data in FTS5 fallback, we can migrate/re-index it into chromadb!
        from kazma_core.memory.fts5 import FTS5Memory
        fts = FTS5Memory()
        fts_count = fts.count()
        if fts_count > 0:
            logger.info("[Installer] Re-indexing %d entries from FTS5 memory fallback into VectorMemory...", fts_count)
            # Re-index: read all entries from FTS5 memory and add them to VectorMemory.
            rows = fts._conn.execute(f"SELECT text, metadata, doc_id FROM {fts._table_name}").fetchall()
            for row in rows:
                import json
                meta = json.loads(row["metadata"]) if row["metadata"] else {}
                mem.add(text=row["text"], metadata=meta, doc_id=row["doc_id"])
            logger.info("[Installer] Re-indexing complete.")
            
    except Exception as e:
        logger.error("[Installer] Error during memory hot-reload: %s", e, exc_info=True)
