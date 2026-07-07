"""Runtime manager for safe detached background package installations and promotions."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from typing import Set

from kazma_core.config_store import get_config_store

logger = logging.getLogger(__name__)

# Track active installer tasks to prevent concurrent duplicate installations
_active_promotions: Set[str] = set()


async def trigger_package_promotion(package_name: str) -> None:
    """Trigger a safe package promotion (installation) in a fully detached background task.
    
    This ensures zero-timeout execution by running as a detached background task,
    so ASGI servers, WebSockets, or platform polling loops do not block or time out.
    """
    if package_name in _active_promotions:
        logger.info("[RuntimeManager] Promotion for %s is already in progress.", package_name)
        return

    _active_promotions.add(package_name)

    # Set status to INSTALLING immediately to persist status across reloads
    try:
        store = get_config_store()
        store.set("system.memory.status", "INSTALLING", category="system")
    except Exception as e:
        logger.error("[RuntimeManager] Failed to set status to INSTALLING: %s", e)

    # Spawn the promotion task as a detached background task on the running loop
    asyncio.create_task(_run_promotion_task(package_name))


async def _run_promotion_task(package_name: str) -> None:
    try:
        logger.info("[RuntimeManager] Starting detached background package promotion of: %s", package_name)

        # Determine package list
        packages = [package_name]
        if package_name in ("sentence-transformers", "chromadb", "sentence_transformers"):
            # Install both to fully resolve the memory pillar
            packages = ["sentence-transformers", "chromadb"]

        uv_path = shutil.which("uv")
        success = False

        if uv_path:
            # First try the requested command: uv add {package_name}
            cmd = [uv_path, "add"] + packages
            logger.info("[RuntimeManager] Executing primary promotion command: %s", " ".join(cmd))
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode == 0:
                    logger.info("[RuntimeManager] Package(s) %s added successfully with uv add!", packages)
                    success = True
                else:
                    err_msg = stderr.decode(errors="replace")
                    logger.warning("[RuntimeManager] `uv add` failed: %s. Trying fallback `uv pip install`.", err_msg)
            except Exception as e:
                logger.warning("[RuntimeManager] Exception trying `uv add`: %s. Trying fallback.", e)

        # Fallback to uv pip install or python -m pip install if uv add didn't work/wasn't present
        if not success:
            if uv_path:
                cmd = [uv_path, "pip", "install", "--python", sys.executable] + packages
            else:
                cmd = [sys.executable, "-m", "pip", "install"] + packages

            logger.info("[RuntimeManager] Executing fallback promotion command: %s", " ".join(cmd))
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                logger.info("[RuntimeManager] Package(s) %s installed successfully via fallback!", packages)
                success = True
            else:
                err_msg = stderr.decode(errors="replace")
                logger.error("[RuntimeManager] Fallback promotion installation failed with code %d. Error: %s", proc.returncode, err_msg)

        if success:
            # Update ConfigStore status to ACTIVE
            store = get_config_store()
            store.set("system.memory.status", "ACTIVE", category="system")

            # Hot-reload memory: trigger re-indexing
            try:
                from kazma_core.system.installer import _hot_reload_memory
                await _hot_reload_memory()
            except Exception as e:
                logger.error("[RuntimeManager] Error triggering hot-reload: %s", e)

            # Broadcast success (Post-Upgrade Broadcast)
            try:
                from kazma_core.observability.alerts import trigger_system_alert
                await trigger_system_alert(
                    subsystem="Memory",
                    status="ACTIVE",
                    message="[✅ KAZMA SYSTEM HEALTHY] Semantic Memory has been successfully activated and hot-reloaded!"
                )
            except Exception as e:
                logger.error("[RuntimeManager] Failed to broadcast celebration: %s", e)
        else:
            # Mark back to DEGRADED
            store = get_config_store()
            store.set("system.memory.status", "DEGRADED", category="system")

    except Exception as e:
        logger.error("[RuntimeManager] Unexpected error in background promotion: %s", e, exc_info=True)
        try:
            store = get_config_store()
            store.set("system.memory.status", "DEGRADED", category="system")
        except Exception:
            pass
    finally:
        _active_promotions.discard(package_name)
