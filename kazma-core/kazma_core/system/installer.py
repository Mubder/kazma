"""Asynchronous background package installer for hot-reloading dependencies."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

from kazma_core.config_store import get_config_store

__all__ = ["ALLOWED_EXTRAS", "ALLOWED_PACKAGES", "asynchronous_install_extra", "asynchronous_install_package"]

logger = logging.getLogger(__name__)

# Track active installer tasks to prevent concurrent duplicate installations
_active_installations: set[str] = set()

# pyproject optional-dependencies extras (must stay allowlisted in the API)
ALLOWED_EXTRAS: frozenset[str] = frozenset({
    "rag", "dev", "test", "tui", "observability", "web", "all",
})

# Individual packages that may be installed without an extra name
ALLOWED_PACKAGES: frozenset[str] = frozenset({
    "sentence-transformers",
    "chromadb",
    "prometheus-client",
    "playwright",
    "textual",
    "python-bidi",
    "fakeredis",
})


def _repo_root() -> Path:
    """Best-effort monorepo root (where pyproject.toml lives)."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


async def asynchronous_install_package(package_name: str) -> None:
    """Install a package in the background using uv or pip, hot-reload, and update status.

    This ensures zero-timeout execution by running as a detached background task.
    """
    key = f"pkg:{package_name}"
    if key in _active_installations:
        logger.info("[Installer] Installation for %s is already in progress.", package_name)
        return

    _active_installations.add(key)

    # Set status to INSTALLING immediately to persist status across reloads
    try:
        store = get_config_store()
        store.set("system.memory.status", "INSTALLING", category="system")
        store.set("system.install.last_target", package_name, category="system")
        store.set("system.install.last_status", "INSTALLING", category="system")
    except Exception as e:
        logger.error("[Installer] Failed to set status to INSTALLING: %s", e)

    asyncio.create_task(_run_install_task(package_name=package_name, extra=None, track_key=key))


async def asynchronous_install_extra(extra_name: str) -> None:
    """Install a pyproject optional-extra (e.g. ``rag``) in the background.

    Uses ``uv pip install -e ".[extra]"`` (additive) from the monorepo root.
    """
    extra = (extra_name or "").strip().lower()
    if extra not in ALLOWED_EXTRAS:
        raise ValueError(f"Extra '{extra}' is not allowlisted")

    key = f"extra:{extra}"
    if key in _active_installations:
        logger.info("[Installer] Installation for extra %s is already in progress.", extra)
        return

    _active_installations.add(key)
    try:
        store = get_config_store()
        store.set("system.install.last_target", f"extra:{extra}", category="system")
        store.set("system.install.last_status", "INSTALLING", category="system")
        if extra in ("rag", "all"):
            store.set("system.memory.status", "INSTALLING", category="system")
    except Exception as e:
        logger.error("[Installer] Failed to set install status: %s", e)

    asyncio.create_task(_run_install_task(package_name=None, extra=extra, track_key=key))


async def _run_install_task(
    *,
    package_name: str | None,
    extra: str | None,
    track_key: str,
) -> None:
    try:
        target_label = f"extra:{extra}" if extra else package_name
        logger.info("[Installer] Starting background installation of: %s", target_label)

        uv_path = shutil.which("uv")
        cwd = str(_repo_root())

        if extra:
            # Editable extra install — additive, does not remove other extras
            if uv_path:
                cmd = [
                    uv_path, "pip", "install", "--python", sys.executable,
                    "-e", f".[{extra}]",
                ]
            else:
                cmd = [sys.executable, "-m", "pip", "install", "-e", f".[{extra}]"]
        else:
            packages = [package_name or ""]
            if package_name in ("sentence-transformers", "chromadb", "sentence_transformers"):
                packages = ["sentence-transformers", "chromadb"]
            if uv_path:
                cmd = [uv_path, "pip", "install", "--python", sys.executable] + packages
            else:
                cmd = [sys.executable, "-m", "pip", "install"] + packages

        logger.info("[Installer] Executing command (cwd=%s): %s", cwd, " ".join(cmd))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        _stdout, stderr = await proc.communicate()

        if proc.returncode == 0:
            logger.info("[Installer] Installed successfully: %s", target_label)
            store = get_config_store()
            store.set("system.install.last_status", "OK", category="system")
            if extra in ("rag", "all") or package_name in (
                "sentence-transformers", "chromadb", "sentence_transformers",
            ):
                store.set("system.memory.status", "ACTIVE", category="system")
                await _hot_reload_memory()
        else:
            err_msg = stderr.decode(errors="replace")
            logger.error(
                "[Installer] Installation failed code=%d target=%s err=%s",
                proc.returncode, target_label, err_msg[:500],
            )
            try:
                store = get_config_store()
                store.set("system.install.last_status", "FAILED", category="system")
                store.set("system.install.last_error", err_msg[:1000], category="system")
                if extra in ("rag", "all") or package_name in (
                    "sentence-transformers", "chromadb",
                ):
                    store.set("system.memory.status", "DEGRADED", category="system")
            except Exception:
                pass

    except Exception as e:
        logger.error("[Installer] Unexpected error in background installer: %s", e, exc_info=True)
        try:
            store = get_config_store()
            store.set("system.install.last_status", "FAILED", category="system")
            store.set("system.install.last_error", str(e)[:1000], category="system")
            store.set("system.memory.status", "DEGRADED", category="system")
        except Exception:
            pass
    finally:
        _active_installations.discard(track_key)


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
