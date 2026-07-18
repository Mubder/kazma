"""Centralized path resolution for Kazma.

All file paths in Kazma are resolved through this module to ensure
**portability** — the project is self-contained in its directory.

Two data categories:

1. **Project data** (``kazma-data/`` relative to the project root):
   - Vector memory, FTS5 memory, backups
   - ConfigStore, checkpoints, audit logs
   - Self-improvement evolution history
   - These are *project-specific* and should travel with the project.

2. **User data** (``~/.kazma/``):
   - Hub skill registry, installed skills
   - TUI themes, tutorial state
   - These are *user preferences* shared across projects (like ``~/.gitconfig``).

The project root is resolved as the directory containing ``pyproject.toml``
(walking up from CWD).  This ensures paths work regardless of where the
process is launched from.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["audit_db", "backups_dir", "checkpoints_db", "data_dir", "fts5_memory_path", "get_project_root", "hub_registry_db", "installed_skills_dir", "log_file", "settings_db", "snapshots_db", "swarm_tasks_db", "tui_state_dir", "tui_themes_dir", "user_home", "vault_db_path", "vector_memory_path"]

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
    """The project data directory (``kazma-data/``). Created if missing."""
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


def log_file() -> Path:
    """Application log file."""
    return data_dir() / "kazma.log"


# ── User data paths (user-level — shared across projects) ─────────────────


def user_home() -> Path:
    """The user's Kazma home directory (``~/.kazma/``). Created if missing."""
    h = Path.home() / ".kazma"
    h.mkdir(parents=True, exist_ok=True)
    return h


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
