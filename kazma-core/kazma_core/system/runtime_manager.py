"""Runtime manager for safe detached background package installations and promotions."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from typing import Set

from kazma_core.config_store import get_config_store
from kazma_core.background import spawn_background

__all__ = ["trigger_package_promotion"]

logger = logging.getLogger(__name__)

# Track active installer tasks to prevent concurrent duplicate installations
_active_promotions: Set[str] = set()


async def trigger_package_promotion(package_name: str) -> None:
    """Trigger a safe package promotion (installation) in a fully detached background task.

    This ensures zero-timeout execution by running as a detached background task,
    so ASGI servers, WebSockets, or platform polling loops do not block or time out.

    **The name must be on ``ALLOWED_PACKAGES``.** That list existed but was
    enforced in exactly one place -- the HTTP route -- and neither of the two
    functions that actually install anything consulted it, so every
    chat-platform button bypassed it. On 2026-09-12 a
    RAM-pressure alert produced a "Resolve Subsystem Issue" button that ran
    ``uv add system-init`` against the live install -- the name came from
    ``f"{subsystem.lower()}-init"``, a label, not a package. It failed only
    because nobody has registered `system-init` on PyPI, which is not a
    security control: the name is now an obvious squat target, and a hit would
    have installed a stranger's code into the venv and written it into
    pyproject.toml as a permanent dependency.

    Being admin-gated does not help here. The admin is not choosing a package;
    they are clicking "resolve" on a disk or memory warning and being handed an
    installer for a name they never saw.
    """
    from kazma_core.system.installer import ALLOWED_PACKAGES

    if package_name not in ALLOWED_PACKAGES:
        logger.error(
            "[RuntimeManager] Refusing to install %r: not in ALLOWED_PACKAGES. "
            "An alert asked to install something that is not a known Kazma "
            "dependency -- this is a bug in whatever built that alert, not a "
            "package to go and add.",
            package_name,
        )
        try:
            get_config_store().set(
                "system.memory.status", "INSTALL_REFUSED", category="system"
            )
        except Exception:  # pragma: no cover - status is best-effort
            logger.debug("[RuntimeManager] status write failed", exc_info=True)
        return

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
    spawn_background(_run_promotion_task(package_name), name=f"promote:{package_name}")


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
                # Windows: the server's SelectorEventLoop (psycopg compat)
                # cannot host asyncio subprocesses — run via a worker thread.
                import subprocess

                result = await asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                if result.returncode == 0:
                    logger.info("[RuntimeManager] Package(s) %s added successfully with uv add!", packages)
                    success = True
                else:
                    err_msg = result.stderr.decode(errors="replace")
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
            import subprocess

            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            if result.returncode == 0:
                logger.info("[RuntimeManager] Package(s) %s installed successfully via fallback!", packages)
                success = True
            else:
                err_msg = result.stderr.decode(errors="replace")
                logger.error("[RuntimeManager] Fallback promotion installation failed with code %d. Error: %s", result.returncode, err_msg)

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
