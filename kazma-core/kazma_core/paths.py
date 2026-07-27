"""Centralized path resolution for Kazma.

All file paths in Kazma are resolved through this module to ensure
**portability** — the project is self-contained in its directory.

Two data categories (both **project-local**, travel with the repo):

1. **Project data** (``kazma-data/`` at the project root):
   - Vector memory, FTS5 memory, backups
   - ConfigStore, checkpoints, audit logs
   - Self-improvement evolution history

2. **Kazma home** (``.kazma/`` at the project root):
   - Hub skill registry, installed skills
   - TUI themes, tutorial state
   - The application log (``kazma.log``) — see ``logging_config.py``
   - Previously this lived at ``~/.kazma`` (user-global); it is now
     project-local so the whole Kazma state moves with the repo.
     :func:`migrate_legacy_user_home` performs a one-time move of any
     pre-existing ``~/.kazma`` on first boot. Override with
     ``KAZMA_USER_HOME``.

The project root is resolved as the directory containing ``pyproject.toml``
(walking up from CWD).  This ensures paths work regardless of where the
process is launched from.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "agent_skills_dir",
    "audit_db",
    "backups_dir",
    "checkpoints_db",
    "data_dir",
    "fts5_memory_path",
    "get_project_root",
    "hub_registry_db",
    "installed_extras_path",
    "installed_skills_dir",
    "legacy_user_home",
    "log_file",
    "merge_legacy_hub_if_empty",
    "migrate_legacy_user_home",
    "preferences_path",
    "rbac_db",
    "settings_db",
    "snapshots_db",
    "swarm_tasks_db",
    "tui_state_dir",
    "tui_themes_dir",
    "user_home",
    "vault_db_path",
    "vector_memory_path",
]

# ── Project root resolution ────────────────────────────────────────────────

_project_root: Path | None = None


def get_project_root() -> Path:
    """Return the project root (the directory containing pyproject.toml).

    Walks up from CWD until pyproject.toml is found. Falls back to CWD.
    Cached after first resolution.
    """
    global _project_root
    if _project_root is not None:
        return _project_root

    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists():
            _project_root = parent
            return _project_root

    # Fallback: check if KAZMA_PROJECT_ROOT is set
    env_root = os.environ.get("KAZMA_PROJECT_ROOT")
    if env_root:
        _project_root = Path(env_root).resolve()
        return _project_root

    # Last resort: use CWD
    _project_root = cwd
    return _project_root


# ── Project data paths (portable — inside the project) ────────────────────


def data_dir() -> Path:
    """The project data directory (``kazma-data/``). Created if missing.

    Override with ``KAZMA_DATA_DIR`` for relocatable production layouts.
    """
    env = (os.environ.get("KAZMA_DATA_DIR") or "").strip()
    if env:
        d = Path(env).expanduser().resolve()
    else:
        d = get_project_root() / "kazma-data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def vector_memory_path() -> str:
    """ChromaDB vector memory path."""
    env = os.environ.get("KAZMA_VECTOR_PATH")
    if env:
        return str(Path(env).expanduser().resolve())
    return str(data_dir() / "vector_memory")


def fts5_memory_path() -> str:
    """FTS5 SQLite memory database path."""
    env = os.environ.get("KAZMA_FTS5_PATH")
    if env:
        return str(Path(env).expanduser().resolve())
    return str(data_dir() / "memory.db")


def backups_dir() -> Path:
    """Backups directory."""
    env = os.environ.get("KAZMA_BACKUPS_DIR")
    if env:
        return Path(env).expanduser().resolve()
    d = data_dir() / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def vault_db_path() -> str:
    """Secret vault database path."""
    return str(data_dir() / "vault.db")


def checkpoints_db() -> str:
    """LangGraph checkpointer database."""
    return str(data_dir() / "checkpoints.db")


def settings_db() -> str:
    """ConfigStore database."""
    return str(data_dir() / "settings.db")


def snapshots_db() -> str:
    """Time-travel snapshots database."""
    return str(data_dir() / "snapshots.db")


def swarm_tasks_db() -> str:
    """Swarm TaskStore database."""
    return str(data_dir() / "swarm_tasks.db")


def audit_db() -> str:
    """Audit log database."""
    return str(data_dir() / "audit.db")


def rbac_db() -> str:
    """RBAC engine database (roles / division permissions)."""
    return str(data_dir() / "rbac.db")


def log_file() -> Path:
    """Application log file — lives under ``.kazma/`` (the single-folder rule).

    Override with ``KAZMA_LOG_FILE`` for ad-hoc redirection (e.g. tests).
    """
    env = (os.environ.get("KAZMA_LOG_FILE") or "").strip()
    if env:
        return Path(env).expanduser()
    return user_home() / "kazma.log"


# ── User data paths (project-local — travels with the repo) ───────────────
#
# Historically these lived at ``~/.kazma`` (user-global, mirroring
# ``~/.gitconfig``). That breaks portability: moving/cloning the repo to
# another machine leaves skill installs, TUI themes, etc. behind. The
# default is now **project-local** (``<repo>/.kazma``) so the whole Kazma
# state travels together. Power users can override with ``KAZMA_USER_HOME``.
# A one-time migration moves any legacy ``~/.kazma`` into the new location.


def user_home() -> Path:
    """The Kazma home directory — **project-local** by default.

    Resolution precedence:
      1. ``KAZMA_USER_HOME`` env (absolute path; power-user override)
      2. ``<repo>/.kazma`` (default — travels with the repo)

    The directory is created if missing.  Callers should also invoke
    :func:`migrate_legacy_user_home` once at boot to move any pre-existing
    ``~/.kazma`` into the new location.
    """
    env = (os.environ.get("KAZMA_USER_HOME") or "").strip()
    if env:
        h = Path(env).expanduser()
    else:
        # Project-local: sibling of kazma-data/, at the repo root.
        h = get_project_root() / ".kazma"
    h.mkdir(parents=True, exist_ok=True)
    return h


def migrate_legacy_user_home() -> bool:
    """One-time migration: move ``~/.kazma`` → ``<repo>/.kazma`` if needed.

    Idempotent and safe:
      - No-op if no legacy ``~/.kazma`` exists.
      - No-op if legacy exists but target already exists (target wins;
        we don't overwrite the user's current state). Logs a **warning**
        once so dual homes are visible.
      - Only moves when legacy exists AND target doesn't — the clean
        first-boot case.

    Returns ``True`` if a migration was performed.
    """
    import logging

    _log = logging.getLogger(__name__)
    legacy = Path.home() / ".kazma"
    if not legacy.exists() or not legacy.is_dir():
        return False
    # Resolve target without creating it (we want to detect existence).
    env = (os.environ.get("KAZMA_USER_HOME") or "").strip()
    target = Path(env).expanduser() if env else (get_project_root() / ".kazma")
    if target.exists():
        # Both exist — user has state in both places. Don't clobber the
        # target; leave legacy in place for manual reconciliation.
        _log.warning(
            "[paths] Dual Kazma homes detected: legacy=%s and project=%s. "
            "Runtime uses the project home only. Safe to archive/delete "
            "legacy after confirming hub/skills live under the project path. "
            "Clone dir ~/kazma-repos is separate (Switch Repo clones).",
            legacy,
            target,
        )
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        legacy.rename(target)
        # Drop a marker at the old location so the user knows where it went.
        try:
            legacy.parent.mkdir(parents=True, exist_ok=True)
            (legacy.with_suffix(".kazma.migrated.txt")).write_text(
                f"Your ~/.kazma was migrated to {target} on first boot of the "
                "project-local layout. This file is a marker; safe to delete.\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        return True
    except OSError:
        # Cross-device rename can fail; fall back to copy. We avoid shutil
        # at module top to keep paths.py dependency-light.
        import shutil

        try:
            shutil.copytree(legacy, target)
            shutil.rmtree(legacy)
            return True
        except Exception:
            return False


def hub_registry_db() -> str:
    """Hub skill registry database (user-level)."""
    env = os.environ.get("KAZMA_HUB_DB")
    if env:
        return env
    return str(user_home() / "hub" / "registry.db")


def installed_skills_dir() -> Path:
    """Directory for user-installed skills (user-level)."""
    d = user_home() / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tui_themes_dir() -> Path:
    """TUI theme configuration directory (user-level)."""
    d = user_home() / "themes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def tui_state_dir() -> Path:
    """TUI state directory (tutorial progress, etc.)."""
    d = user_home() / "tui"
    d.mkdir(parents=True, exist_ok=True)
    return d


def agent_skills_dir() -> Path:
    """Kazma-owned Agent Skills install directory under project home."""
    d = user_home() / "agent-skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def installed_extras_path() -> Path:
    """JSON file listing optional extras installed by the system installer."""
    return user_home() / "installed_extras.json"


def preferences_path() -> Path:
    """TUI/UI preferences JSON under project home."""
    return user_home() / "preferences.json"


def legacy_user_home() -> Path:
    """Historical ``~/.kazma`` path (migration / read fallback only).

    New code must **not** write here — use :func:`user_home`.
    """
    return Path.home() / ".kazma"


def merge_legacy_hub_if_empty() -> bool:
    """If dual homes exist and project hub is empty, copy legacy hub registry.

    Non-destructive: never overwrites a non-empty project hub DB.
    Returns True if a copy was performed.
    """
    import logging
    import shutil

    _log = logging.getLogger(__name__)
    legacy = legacy_user_home()
    if not legacy.is_dir():
        return False
    target = user_home()
    legacy_hub = legacy / "hub" / "registry.db"
    target_hub = target / "hub" / "registry.db"
    if not legacy_hub.is_file():
        return False
    if target_hub.is_file() and target_hub.stat().st_size > 0:
        return False
    try:
        target_hub.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_hub, target_hub)
        _log.info(
            "[paths] Copied legacy hub registry %s → %s (project hub was empty)",
            legacy_hub,
            target_hub,
        )
        return True
    except Exception as exc:
        _log.warning("[paths] Legacy hub merge failed: %s", exc)
        return False
