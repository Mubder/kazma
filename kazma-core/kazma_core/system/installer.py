"""Asynchronous background package installer for hot-reloading dependencies."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

from kazma_core.config_store import get_config_store
from kazma_core.background import spawn_background

__all__ = ["ALLOWED_EXTRAS", "ALLOWED_PACKAGES", "asynchronous_install_extra", "asynchronous_install_package"]

logger = logging.getLogger(__name__)

# Track active installer tasks to prevent concurrent duplicate installations
_active_installations: set[str] = set()

# pyproject optional-dependencies extras (must stay allowlisted in the API).
# Keep in lockstep with [project.optional-dependencies] in pyproject.toml.
ALLOWED_EXTRAS: frozenset[str] = frozenset({
    "rag", "dev", "test", "tui", "observability", "web", "push", "postgres",
    "document", "ocr", "convert", "document-platform", "docling",
    "index", "sandbox", "durable", "database", "all",
})

# Individual packages that may be installed without an extra name
ALLOWED_PACKAGES: frozenset[str] = frozenset({
    "sentence-transformers",
    "chromadb",
    "sqlite-vec",
    "prometheus-client",
    "playwright",
    "textual",
    "python-bidi",
    "fakeredis",
    "tree-sitter",
    "e2b-code-interpreter",
    "temporalio",
    "docling",
    "pywebpush",
    "pytesseract",
    "weasyprint",
    "pymupdf",
    "pypdfium2",
    "pdf2image",
    "pillow",
    "tree-sitter-python",
    "tree-sitter-javascript",
})


def _repo_root() -> Path:
    """Best-effort monorepo root (where pyproject.toml lives)."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _record_status(*pairs: tuple[str, Any]) -> None:
    """Persist install-status keys in one write.

    Blocking -- a settings write is a database round trip, and on Postgres it
    waits for a pool connection -- so the async installer runs it with
    ``asyncio.to_thread``, never on the event loop.
    """
    get_config_store().batch_set([(key, value, "system") for key, value in pairs])


async def asynchronous_install_package(package_name: str) -> None:
    """Install a package in the background using uv or pip, hot-reload, and update status.

    This ensures zero-timeout execution by running as a detached background task.

    **The name must be on :data:`ALLOWED_PACKAGES`.** That list existed from the
    start but was only ever checked by the HTTP route in
    ``kazma_ui/routes_direct/system.py`` -- so the chat-platform "Install"
    buttons, which call this directly, installed whatever name the alert card
    happened to carry. The check belongs here, where every caller inherits it,
    rather than in each caller, where it was already missing from two of three
    (audit 2026-09-12; see ``trigger_package_promotion`` for the incident).
    """
    if package_name not in ALLOWED_PACKAGES:
        logger.error(
            "[Installer] Refusing to install %r: not in ALLOWED_PACKAGES.",
            package_name,
        )
        return

    key = f"pkg:{package_name}"
    if key in _active_installations:
        logger.info("[Installer] Installation for %s is already in progress.", package_name)
        return

    _active_installations.add(key)

    # Set status to INSTALLING immediately to persist status across reloads
    try:
        await asyncio.to_thread(
            _record_status,
            ("system.memory.status", "INSTALLING"),
            ("system.install.last_target", package_name),
            ("system.install.last_status", "INSTALLING"),
        )
    except Exception as e:
        logger.error("[Installer] Failed to set status to INSTALLING: %s", e)

    spawn_background(_run_install_task(package_name=package_name, extra=None, track_key=key), name=f"install:{package_name}")


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
        status: list[tuple[str, Any]] = [
            ("system.install.last_target", f"extra:{extra}"),
            ("system.install.last_status", "INSTALLING"),
        ]
        if extra in ("rag", "all"):
            status.append(("system.memory.status", "INSTALLING"))
        await asyncio.to_thread(_record_status, *status)
    except Exception as e:
        logger.error("[Installer] Failed to set install status: %s", e)

    spawn_background(_run_install_task(package_name=None, extra=extra, track_key=key), name=f"install-extra:{extra}")


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

        # Run the blocking install in a worker thread instead of
        # asyncio.create_subprocess_exec. On Windows the asyncio subprocess
        # transport requires a ProactorEventLoop, but this background task may
        # run on a SelectorEventLoop (thread-spawned loop / uvicorn worker
        # context), which raises NotImplementedError — every "Install ML
        # Dependencies" click failed with that (issue report). subprocess.run
        # in a thread works on any loop/platform.
        import subprocess

        result = await asyncio.to_thread(
            subprocess.run, cmd, cwd=cwd, capture_output=True,
        )
        stderr = result.stderr

        if result.returncode == 0:
            logger.info("[Installer] Installed successfully: %s", target_label)
            await asyncio.to_thread(_record_status, ("system.install.last_status", "OK"))
            # Remember extras so ``kazma update`` reinstalls them (never bare uv sync).
            try:
                await asyncio.to_thread(_record_installed_extra, extra, package_name)
            except Exception as rec_exc:
                logger.debug("[Installer] Could not persist extras list: %s", rec_exc)
            if extra in ("rag", "all") or package_name in (
                "sentence-transformers", "chromadb", "sentence_transformers",
            ):
                await asyncio.to_thread(_record_status, ("system.memory.status", "ACTIVE"))
                await _hot_reload_memory()
        else:
            err_msg = stderr.decode(errors="replace")
            logger.error(
                "[Installer] Installation failed code=%d target=%s err=%s",
                result.returncode, target_label, err_msg[:500],
            )
            try:
                failed: list[tuple[str, Any]] = [
                    ("system.install.last_status", "FAILED"),
                    ("system.install.last_error", err_msg[:1000]),
                ]
                if extra in ("rag", "all") or package_name in (
                    "sentence-transformers", "chromadb",
                ):
                    failed.append(("system.memory.status", "DEGRADED"))
                await asyncio.to_thread(_record_status, *failed)
            except Exception:
                logger.warning("[Installer] could not record the failed install", exc_info=True)

    except Exception as e:
        logger.error("[Installer] Unexpected error in background installer: %s", e, exc_info=True)
        try:
            await asyncio.to_thread(
                _record_status,
                ("system.install.last_status", "FAILED"),
                ("system.install.last_error", str(e)[:1000]),
                ("system.memory.status", "DEGRADED"),
            )
        except Exception:
            logger.warning("[Installer] could not record the failed install", exc_info=True)
    finally:
        _active_installations.discard(track_key)


def _record_installed_extra(extra: str | None, package_name: str | None) -> None:
    """Append installed extras to ConfigStore + project-home installed_extras.json."""
    import json
    from pathlib import Path

    to_add: list[str] = []
    if extra:
        name = extra.strip().lower()
        if name == "all":
            to_add = sorted(e for e in ALLOWED_EXTRAS if e != "all")
        elif name:
            to_add = [name]
    elif package_name in (
        "chromadb", "sentence-transformers", "sentence_transformers",
    ):
        to_add = ["rag"]
    elif package_name == "prometheus-client":
        to_add = ["observability"]
    elif package_name == "playwright":
        to_add = ["web"]
    elif package_name in ("textual", "python-bidi"):
        to_add = ["tui"]
    elif package_name == "fakeredis":
        to_add = ["test"]

    if not to_add:
        return

    store = get_config_store()
    existing = store.get("system.installed_extras") or []
    if isinstance(existing, str):
        existing = [p.strip() for p in existing.split(",") if p.strip()]
    if not isinstance(existing, list):
        existing = []
    merged = list(dict.fromkeys([*(str(x) for x in existing), *to_add]))
    store.set("system.installed_extras", merged, category="system")

    try:
        from kazma_core.paths import installed_extras_path

        path = installed_extras_path()
    except Exception:
        path = Path.cwd() / ".kazma" / "installed_extras.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"extras": merged}, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


async def _hot_reload_memory() -> None:
    """V1 VectorMemory hot-reload — retired.

    The legacy ChromaDB/FTS5 stack was removed in the V1→V2 cutover. V2 uses
    its own SQLite stores (memory_state.db / memory_ops.db) and the shared
    embedder, neither of which needs a post-install hot-reload. This is kept
    as a no-op so the install flow's call site (installer.py:152) stays valid.
    """
    logger.info("[Installer] V2 memory stack — no V1 hot-reload needed (no-op).")
